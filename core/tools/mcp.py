"""MCP client and tool adapters.

Implements a small stdio JSON-RPC MCP client so ATRI can discover configured
servers and expose their tools through the normal Tool registry.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from core import logger

from .base import Tool

_MCP_PROTOCOL_VERSION = "2025-11-25"
_DEFAULT_TIMEOUT = 20.0
_TOOL_NAME_LIMIT = 64
_SUPPORTED_TRANSPORTS = {"stdio", "streamable_http", "http"}
_DISCOVERY_CACHE_TTL = 5.0


def mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Return a provider-safe function name for a server/tool pair."""
    server = _safe_name(server_name, fallback="server")
    tool = _safe_name(tool_name, fallback="tool")
    name = f"mcp_{server}_{tool}"
    if len(name) <= _TOOL_NAME_LIMIT:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]  # noqa: S324
    keep = _TOOL_NAME_LIMIT - len(digest) - 1
    return f"{name[:keep].rstrip('_-')}_{digest}"


def _safe_name(value: str, fallback: str = "item") -> str:
    raw = str(value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    safe = re.sub(r"_+", "_", safe)
    if not safe:
        safe = fallback
    if raw and safe != raw:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]  # noqa: S324
        safe = f"{safe}_{digest}"
    return safe


def _server_transport(cfg: dict) -> str:
    transport = str(cfg.get("transport") or "").strip().lower()
    if transport:
        return transport
    if cfg.get("command"):
        return "stdio"
    if cfg.get("url"):
        return "streamable_http"
    return "stdio"


def _json_fingerprint(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        payload = repr(value)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()  # noqa: S324


@dataclass
class MCPDiscoveredTool:
    registered_name: str
    server_name: str
    remote_name: str
    description: str
    parameters: dict
    kind: str = "tool"
    raw: dict = field(default_factory=dict)


class MCPError(RuntimeError):
    pass


class MCPStdioClient:
    """Minimal MCP stdio transport client.

    The client uses newline-delimited JSON-RPC framing, and the reader also
    accepts Content-Length framed messages for compatibility with older servers.
    """

    def __init__(self, name: str, cfg: dict, workspace: str):
        self.name = name
        self.cfg = dict(cfg or {})
        self.workspace = os.path.abspath(workspace or ".")
        self.timeout = float(self.cfg.get("timeout") or _DEFAULT_TIMEOUT)
        self.process: subprocess.Popen | None = None
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.protocol_version = ""
        self._next_id = 1
        self._pending: dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stderr_lines: list[str] = []
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_lines[-20:])

    def start(self) -> None:
        if self.alive:
            return
        command = str(self.cfg.get("command") or "").strip()
        if not command:
            raise MCPError("stdio MCP server requires a command")
        args = self.cfg.get("args") or []
        if isinstance(args, str):
            args = [args]
        if not isinstance(args, list):
            raise MCPError("stdio MCP server args must be a list")

        cwd = str(self.cfg["cwd"]) if "cwd" in self.cfg else self.workspace
        env = os.environ.copy()
        cfg_env = self.cfg.get("env") or {}
        if isinstance(cfg_env, dict):
            env.update({str(k): str(v) for k, v in cfg_env.items()})

        try:
            self.process = subprocess.Popen(  # noqa: S603
                [command, *[str(arg) for arg in args]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                text=False,
            )
        except FileNotFoundError as e:
            raise MCPError(f"MCP command not found: {command}") from e
        except Exception as e:
            raise MCPError(f"failed to start MCP server: {e}") from e

        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"mcp-{self.name}-stdout",
            daemon=True,
        )
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            name=f"mcp-{self.name}-stderr",
            daemon=True,
        )
        self._stderr_thread.start()

        try:
            result = self.request(
                "initialize",
                {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "ATRI", "version": "0.1.0"},
                },
            )
            self.protocol_version = str(result.get("protocolVersion") or "")
            self.capabilities = result.get("capabilities") or {}
            self.server_info = result.get("serverInfo") or {}
            self.notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        proc = self.process
        self.process = None
        with self._pending_lock:
            for pending in self._pending.values():
                pending.put({"error": {"message": "MCP server closed"}})
            self._pending.clear()
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception as e:
            logger.debug(f"Error closing MCP server {self.name}: {e}")

    def request(
        self,
        method: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        if not self.alive:
            raise MCPError("MCP server is not running")
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            response_queue: queue.Queue = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
        try:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            try:
                response = response_queue.get(timeout=timeout or self.timeout)
            except queue.Empty as e:
                raise MCPError(f"MCP request timed out: {method}") from e
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

        if "error" in response:
            error = response["error"]
            if isinstance(error, dict):
                message = error.get("message") or error
            else:
                message = error
            raise MCPError(f"{method} failed: {message}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict | None = None) -> None:
        if self.alive:
            self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def list_tools(self) -> list[dict]:
        result = self._list_paginated("tools/list", "tools")
        return [item for item in result if isinstance(item, dict)]

    def list_resources(self) -> list[dict]:
        result = self._list_paginated("resources/list", "resources")
        return [item for item in result if isinstance(item, dict)]

    def list_resource_templates(self) -> list[dict]:
        result = self._list_paginated("resources/templates/list", "resourceTemplates")
        return [item for item in result if isinstance(item, dict)]

    def list_prompts(self) -> list[dict]:
        result = self._list_paginated("prompts/list", "prompts")
        return [item for item in result if isinstance(item, dict)]

    def _list_paginated(self, method: str, key: str) -> list[dict]:
        items: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            try:
                result = self.request(method, params)
            except MCPError as e:
                if "Method not found" in str(e) or "not found" in str(e).lower():
                    return items
                raise
            page = result.get(key) or []
            if isinstance(page, list):
                items.extend(item for item in page if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return items

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def read_resource(self, uri: str) -> dict:
        return self.request("resources/read", {"uri": uri})

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("prompts/get", {"name": name, "arguments": arguments or {}})

    def _send(self, message: dict) -> None:
        if not self.process or not self.process.stdin:
            raise MCPError("MCP server stdin is not available")
        raw = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        framing = str(self.cfg.get("framing") or "newline").lower()
        if framing == "headers":
            payload = b"Content-Length: " + str(len(raw)).encode("ascii") + b"\r\n\r\n" + raw
        else:
            payload = raw + b"\n"
        with self._write_lock:
            try:
                self.process.stdin.write(payload)
                self.process.stdin.flush()
            except BrokenPipeError as e:
                raise MCPError("MCP server pipe closed") from e

    def _read_loop(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        while True:
            try:
                line = proc.stdout.readline()
                if not line:
                    break
                message: dict | None
                if line.lower().startswith(b"content-length:"):
                    message = self._read_header_framed_message(line)
                else:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    message = json.loads(stripped.decode("utf-8"))
                if isinstance(message, dict):
                    self._handle_message(message)
            except Exception:
                break

    def _read_header_framed_message(self, first_line: bytes) -> dict | None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return None
        headers = [first_line]
        while True:
            line = proc.stdout.readline()
            if not line or line in {b"\r\n", b"\n"}:
                break
            headers.append(line)
        length = 0
        for header in headers:
            key, _, value = header.decode("ascii", errors="ignore").partition(":")
            if key.lower() == "content-length":
                length = int(value.strip())
                break
        if length <= 0:
            return None
        body = proc.stdout.read(length)
        if not body:
            return None
        return json.loads(body.decode("utf-8"))

    def _handle_message(self, message: dict) -> None:
        request_id = message.get("id")
        if request_id is None:
            return
        try:
            request_id = int(request_id)
        except (TypeError, ValueError):
            return
        with self._pending_lock:
            pending = self._pending.get(request_id)
        if pending is not None:
            pending.put(message)

    def _stderr_loop(self) -> None:
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_lines.append(text)
                if len(self._stderr_lines) > 100:
                    del self._stderr_lines[:50]


class MCPStreamableHttpClient:
    """Minimal MCP streamable HTTP client."""

    def __init__(self, name: str, cfg: dict, workspace: str):
        self.name = name
        self.cfg = dict(cfg or {})
        self.workspace = os.path.abspath(workspace or ".")
        self.url = str(self.cfg.get("url") or "").strip()
        self.timeout = float(self.cfg.get("timeout") or _DEFAULT_TIMEOUT)
        self.server_info: dict = {}
        self.capabilities: dict = {}
        self.protocol_version = ""
        self.session_id = ""
        self._next_id = 1
        self._initialized = False

    @property
    def alive(self) -> bool:
        return self._initialized

    @property
    def stderr_tail(self) -> str:
        return ""

    def start(self) -> None:
        if self.alive:
            return
        if not self.url:
            raise MCPError("streamable HTTP MCP server requires a url")
        result = self.request(
            "initialize",
            {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ATRI", "version": "0.1.0"},
            },
        )
        self.protocol_version = str(result.get("protocolVersion") or "")
        self.capabilities = result.get("capabilities") or {}
        self.server_info = result.get("serverInfo") or {}
        self._initialized = True
        self.notify("notifications/initialized")

    def close(self) -> None:
        self._initialized = False

    def request(
        self,
        method: str,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        request_id = self._next_id
        self._next_id += 1
        response = self._exchange(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
            timeout=timeout,
        )
        if "error" in response:
            error = response["error"]
            if isinstance(error, dict):
                message = error.get("message") or error
            else:
                message = error
            raise MCPError(f"{method} failed: {message}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict | None = None) -> None:
        self._exchange(
            {"jsonrpc": "2.0", "method": method, "params": params or {}},
            expect_response=False,
        )

    def list_tools(self) -> list[dict]:
        result = self._list_paginated("tools/list", "tools")
        return [item for item in result if isinstance(item, dict)]

    def list_resources(self) -> list[dict]:
        result = self._list_paginated("resources/list", "resources")
        return [item for item in result if isinstance(item, dict)]

    def list_resource_templates(self) -> list[dict]:
        result = self._list_paginated("resources/templates/list", "resourceTemplates")
        return [item for item in result if isinstance(item, dict)]

    def list_prompts(self) -> list[dict]:
        result = self._list_paginated("prompts/list", "prompts")
        return [item for item in result if isinstance(item, dict)]

    def _list_paginated(self, method: str, key: str) -> list[dict]:
        items: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            try:
                result = self.request(method, params)
            except MCPError as e:
                if "Method not found" in str(e) or "not found" in str(e).lower():
                    return items
                raise
            page = result.get(key) or []
            if isinstance(page, list):
                items.extend(item for item in page if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                return items

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def read_resource(self, uri: str) -> dict:
        return self.request("resources/read", {"uri": uri})

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("prompts/get", {"name": name, "arguments": arguments or {}})

    def _exchange(
        self,
        message: dict,
        timeout: float | None = None,
        expect_response: bool = True,
    ) -> dict:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": _MCP_PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        cfg_headers = self.cfg.get("headers") or {}
        if isinstance(cfg_headers, dict):
            headers.update({str(k): str(v) for k, v in cfg_headers.items()})

        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme not in {"http", "https"}:
            raise MCPError("HTTP MCP server URL must use http or https")

        req = urllib.request.Request(  # noqa: S310
            self.url,
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:  # noqa: S310
                session_id = resp.headers.get("Mcp-Session-Id") or resp.headers.get(
                    "mcp-session-id"
                )
                if session_id:
                    self.session_id = session_id
                content_type = resp.headers.get("Content-Type", "")
                if not expect_response:
                    return {}
                if "text/event-stream" in content_type.lower():
                    return _read_sse_rpc_response(resp, message.get("id"))
                body = resp.read()
                return _decode_http_rpc_response(body, content_type, message.get("id"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise MCPError(f"HTTP {e.code}: {body or e.reason}") from e
        except urllib.error.URLError as e:
            raise MCPError(f"HTTP transport error: {e.reason}") from e
        except TimeoutError as e:
            raise MCPError("HTTP transport timed out") from e


class MCPRegistry:
    """Discovers MCP servers and creates Tool adapters."""

    def __init__(self):
        self._lock = threading.RLock()
        self._clients: dict[str, MCPStdioClient | MCPStreamableHttpClient] = {}
        self._client_fingerprints: dict[str, str] = {}
        self._tool_index: dict[str, MCPDiscoveredTool] = {}
        self._snapshot: dict[str, Any] = self._empty_snapshot()
        self._fingerprint = ""
        self._workspace = "."

    def refresh(self, servers: dict, workspace: str = ".", force: bool = False) -> dict:
        normalized = self._normalize_servers(servers)
        fingerprint = _json_fingerprint(
            {"servers": normalized, "workspace": os.path.abspath(workspace or ".")}
        )
        now = time.time()
        with self._lock:
            if (
                not force
                and fingerprint == self._fingerprint
                and self._snapshot_can_be_reused(normalized, now)
            ):
                return self.snapshot()
            self._workspace = workspace or "."
            self._fingerprint = fingerprint
            self._tool_index = {}

            active_names = {
                name
                for name, cfg in normalized.items()
                if bool(cfg.get("active", True)) and _server_transport(cfg) in _SUPPORTED_TRANSPORTS
            }
            for name in list(self._clients):
                if force or name not in active_names:
                    self._clients.pop(name).close()
                    self._client_fingerprints.pop(name, None)

            server_states = []
            used_names: set[str] = set()
            for name, cfg in normalized.items():
                state = self._discover_server(name, cfg, workspace, used_names, force=force)
                server_states.append(state)

            tools = [
                {
                    "name": info.registered_name,
                    "server": info.server_name,
                    "remote_name": info.remote_name,
                    "description": info.description,
                    "kind": info.kind,
                }
                for info in sorted(self._tool_index.values(), key=lambda item: item.registered_name)
            ]
            tools_fingerprint = _json_fingerprint(tools)
            self._snapshot = {
                "servers": server_states,
                "tools": tools,
                "fingerprint": fingerprint,
                "tools_fingerprint": tools_fingerprint,
                "push_fingerprint": _json_fingerprint(
                    {"config": fingerprint, "tools": tools_fingerprint}
                ),
                "counts": {
                    "servers": len(server_states),
                    "active_servers": sum(1 for s in server_states if s.get("active")),
                    "connected_servers": sum(
                        1 for s in server_states if s.get("status") == "connected"
                    ),
                    "tools": len([t for t in tools if t["kind"] == "tool"]),
                    "tool_adapters": len(tools),
                    "resources": sum(len(s.get("resources", [])) for s in server_states),
                    "prompts": sum(len(s.get("prompts", [])) for s in server_states),
                },
                "updated_at": now,
            }
            return self.snapshot()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._snapshot, default=str))

    def create_tools(self, workspace: str = ".") -> list[Tool]:
        with self._lock:
            return [MCPTool(workspace, self, info) for info in self._tool_index.values()]

    def call(self, registered_name: str, arguments: dict) -> dict:
        with self._lock:
            info = self._tool_index.get(registered_name)
            if info is None:
                raise MCPError(f"unknown MCP tool: {registered_name}")
            client = self._clients.get(info.server_name)
        if client is None or not client.alive:
            raise MCPError(f"MCP server is not running: {info.server_name}")
        if info.kind == "resource_reader":
            uri = str(arguments.get("uri") or "")
            if not uri:
                raise MCPError("uri is required")
            return client.read_resource(uri)
        if info.kind == "prompt_getter":
            prompt_name = str(arguments.get("name") or "")
            prompt_args = arguments.get("arguments") or {}
            if not prompt_name:
                raise MCPError("name is required")
            if not isinstance(prompt_args, dict):
                raise MCPError("arguments must be an object")
            return client.get_prompt(prompt_name, prompt_args)
        return client.call_tool(info.remote_name, arguments or {})

    def validate_server(self, name: str, cfg: dict, workspace: str = ".") -> dict:
        cfg = dict(cfg or {})
        cfg.setdefault("active", True)
        transport = _server_transport(cfg)
        if transport not in _SUPPORTED_TRANSPORTS:
            return {
                "name": name,
                "active": bool(cfg.get("active", True)),
                "transport": transport,
                "status": "unsupported",
                "error": "This MCP transport is not supported for execution.",
                "tools": [],
                "resources": [],
                "prompts": [],
                "resource_templates": [],
            }
        client = self._create_client(name, cfg, workspace)
        try:
            client.start()
            tools = client.list_tools()
            resources = client.list_resources()
            resource_templates = client.list_resource_templates()
            prompts = client.list_prompts()
            return {
                "name": name,
                "active": bool(cfg.get("active", True)),
                "transport": transport,
                "status": "connected",
                "protocol_version": client.protocol_version,
                "server_info": client.server_info,
                "tools": tools,
                "resources": resources,
                "resource_templates": resource_templates,
                "prompts": prompts,
                "error": "",
            }
        except Exception as e:
            return {
                "name": name,
                "active": bool(cfg.get("active", True)),
                "transport": transport,
                "status": "error",
                "error": _diagnostic_error(e, client.stderr_tail),
                "tools": [],
                "resources": [],
                "resource_templates": [],
                "prompts": [],
            }
        finally:
            client.close()

    def close(self) -> None:
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()
            self._client_fingerprints.clear()
            self._tool_index.clear()
            self._snapshot = self._empty_snapshot()
            self._fingerprint = ""

    def _snapshot_can_be_reused(self, normalized: dict[str, dict], now: float) -> bool:
        updated_at = float(self._snapshot.get("updated_at") or 0)
        if now - updated_at > _DISCOVERY_CACHE_TTL:
            return False

        active_names = {
            name
            for name, cfg in normalized.items()
            if bool(cfg.get("active", True)) and _server_transport(cfg) in _SUPPORTED_TRANSPORTS
        }
        states = {item.get("name"): item for item in self._snapshot.get("servers", [])}
        for name in active_names:
            state = states.get(name)
            if state is None or state.get("status") != "connected":
                return False
            client = self._clients.get(name)
            if client is None or not client.alive:
                return False
        return True

    def _discover_server(
        self,
        name: str,
        cfg: dict,
        workspace: str,
        used_names: set[str],
        force: bool = False,
    ) -> dict:
        transport = _server_transport(cfg)
        base_state = {
            "name": name,
            "active": bool(cfg.get("active", True)),
            "transport": transport,
            "status": "inactive",
            "tools": [],
            "resources": [],
            "resource_templates": [],
            "prompts": [],
            "error": "",
        }
        if not base_state["active"]:
            return base_state
        if transport not in _SUPPORTED_TRANSPORTS:
            base_state.update(
                {
                    "status": "unsupported",
                    "error": "This MCP transport is not supported for execution.",
                }
            )
            return base_state

        client_fingerprint = _json_fingerprint(cfg)
        client = self._clients.get(name)
        fingerprint_changed = self._client_fingerprints.get(name) != client_fingerprint
        if force or client is None or not client.alive or fingerprint_changed:
            if client is not None:
                client.close()
            client = self._create_client(name, cfg, workspace)
            self._clients[name] = client
            self._client_fingerprints[name] = client_fingerprint
            try:
                client.start()
            except Exception as e:
                self._clients.pop(name, None)
                self._client_fingerprints.pop(name, None)
                base_state.update(
                    {"status": "error", "error": _diagnostic_error(e, client.stderr_tail)}
                )
                return base_state

        try:
            tools = client.list_tools()
            resources = client.list_resources()
            resource_templates = client.list_resource_templates()
            prompts = client.list_prompts()
        except Exception as e:
            base_state.update(
                {"status": "error", "error": _diagnostic_error(e, client.stderr_tail)}
            )
            return base_state

        tool_summaries = []
        for remote in tools:
            remote_name = str(remote.get("name") or "").strip()
            if not remote_name:
                continue
            registered_name = self._unique_tool_name(mcp_tool_name(name, remote_name), used_names)
            description = str(remote.get("description") or f"MCP tool {remote_name} from {name}")
            input_schema = remote.get("inputSchema")
            parameters = input_schema if isinstance(input_schema, dict) else {}
            parameters = _normalize_parameters(parameters)
            info = MCPDiscoveredTool(
                registered_name=registered_name,
                server_name=name,
                remote_name=remote_name,
                description=f"[MCP:{name}] {description}",
                parameters=parameters,
                kind="tool",
                raw=remote,
            )
            self._tool_index[registered_name] = info
            tool_summaries.append(
                {
                    "name": remote_name,
                    "registered_name": registered_name,
                    "description": description,
                    "input_schema": parameters,
                }
            )

        if resources or resource_templates:
            registered_name = self._unique_tool_name(
                mcp_tool_name(name, "read_resource"),
                used_names,
            )
            self._tool_index[registered_name] = MCPDiscoveredTool(
                registered_name=registered_name,
                server_name=name,
                remote_name="resources/read",
                description=f"[MCP:{name}] Read a resource exposed by this MCP server by URI.",
                parameters={
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string", "description": "Resource URI to read"}
                    },
                    "required": ["uri"],
                },
                kind="resource_reader",
            )
        if prompts:
            registered_name = self._unique_tool_name(mcp_tool_name(name, "get_prompt"), used_names)
            self._tool_index[registered_name] = MCPDiscoveredTool(
                registered_name=registered_name,
                server_name=name,
                remote_name="prompts/get",
                description=f"[MCP:{name}] Get a prompt exposed by this MCP server.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Prompt name"},
                        "arguments": {
                            "type": "object",
                            "description": "Prompt arguments keyed by argument name",
                        },
                    },
                    "required": ["name"],
                },
                kind="prompt_getter",
            )

        base_state.update(
            {
                "status": "connected",
                "protocol_version": client.protocol_version,
                "server_info": client.server_info,
                "tools": tool_summaries,
                "resources": resources,
                "resource_templates": resource_templates,
                "prompts": prompts,
                "error": "",
            }
        )
        return base_state

    @staticmethod
    def _create_client(name: str, cfg: dict, workspace: str):
        transport = _server_transport(cfg)
        if transport == "stdio":
            return MCPStdioClient(name, cfg, workspace)
        return MCPStreamableHttpClient(name, cfg, workspace)

    @staticmethod
    def _unique_tool_name(name: str, used_names: set[str]) -> str:
        if name not in used_names:
            used_names.add(name)
            return name
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]  # noqa: S324
        base = name[: _TOOL_NAME_LIMIT - 7].rstrip("_-")
        candidate = f"{base}_{digest}"
        counter = 2
        while candidate in used_names:
            suffix = f"_{counter}"
            candidate = f"{base[: _TOOL_NAME_LIMIT - len(suffix)]}{suffix}"
            counter += 1
        used_names.add(candidate)
        return candidate

    @staticmethod
    def _normalize_servers(servers: dict) -> dict[str, dict]:
        if not isinstance(servers, dict):
            return {}
        normalized: dict[str, dict] = {}
        for raw_name, raw_cfg in servers.items():
            name = str(raw_name or "").strip()
            if not name:
                continue
            cfg = dict(raw_cfg or {}) if isinstance(raw_cfg, dict) else {}
            cfg.setdefault("active", True)
            normalized[name] = cfg
        return normalized

    @staticmethod
    def _empty_snapshot() -> dict:
        return {
            "servers": [],
            "tools": [],
            "fingerprint": "",
            "tools_fingerprint": "",
            "push_fingerprint": "",
            "counts": {
                "servers": 0,
                "active_servers": 0,
                "connected_servers": 0,
                "tools": 0,
                "tool_adapters": 0,
                "resources": 0,
                "prompts": 0,
            },
            "updated_at": 0,
        }


class MCPTool(Tool):
    def __init__(self, workspace: str, registry: MCPRegistry, info: MCPDiscoveredTool):
        super().__init__(workspace)
        self._registry = registry
        self._info = info
        self.name = info.registered_name
        self.description = info.description
        self.parameters = info.parameters

    def execute(self, **kwargs: Any) -> str:
        result = self._registry.call(self.name, kwargs)
        return _format_mcp_result(result)


def _normalize_parameters(parameters: dict) -> dict:
    if not isinstance(parameters, dict):
        return {"type": "object", "properties": {}}
    normalized = dict(parameters)
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    required = normalized.get("required")
    if required is not None and not isinstance(required, list):
        normalized.pop("required", None)
    return normalized


def _decode_http_rpc_response(body: bytes, content_type: str, request_id: Any) -> dict:
    if not body:
        raise MCPError("empty HTTP response from MCP server")
    text = body.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type.lower():
        for event in text.split("\n\n"):
            data_lines = []
            for line in event.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if not data_lines:
                continue
            message = _decode_rpc_message("\n".join(data_lines), request_id)
            if message is not None:
                return message
        raise MCPError("no JSON-RPC message found in HTTP event stream")
    message = _decode_rpc_message(text, request_id)
    if message is None:
        raise MCPError("no matching JSON-RPC response from MCP server")
    return message


def _read_sse_rpc_response(resp, request_id: Any) -> dict:
    event_lines: list[str] = []
    while True:
        line = resp.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if text == "":
            message = _decode_sse_event(event_lines, request_id)
            if message is not None:
                return message
            event_lines = []
            continue
        event_lines.append(text)

    message = _decode_sse_event(event_lines, request_id)
    if message is not None:
        return message
    raise MCPError("no JSON-RPC message found in HTTP event stream")


def _decode_sse_event(lines: list[str], request_id: Any) -> dict | None:
    data_lines = []
    for line in lines:
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return None
    return _decode_rpc_message("\n".join(data_lines), request_id)


def _decode_rpc_message(text: str, request_id: Any) -> dict | None:
    payload = json.loads(text)
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and _rpc_id_matches(item.get("id"), request_id):
                return item
        return None
    if isinstance(payload, dict):
        if request_id is None or _rpc_id_matches(payload.get("id"), request_id):
            return payload
    return None


def _rpc_id_matches(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _format_mcp_result(result: dict) -> str:
    is_error = bool(result.get("isError"))
    parts: list[str] = []
    if isinstance(result.get("content"), list):
        parts.extend(_format_content_item(item) for item in result["content"])
    if isinstance(result.get("contents"), list):
        parts.extend(_format_resource_content(item) for item in result["contents"])
    if isinstance(result.get("messages"), list):
        parts.extend(_format_prompt_message(item) for item in result["messages"])
    if result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, indent=2))
    text = "\n\n".join(part for part in parts if part).strip()
    if not text:
        text = json.dumps(result, ensure_ascii=False, indent=2)
    return f"Error: {text}" if is_error else text


def _format_content_item(item: Any) -> str:
    if not isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False)
    item_type = item.get("type")
    if item_type == "text":
        return str(item.get("text") or "")
    if item_type == "resource":
        resource = item.get("resource") or {}
        if isinstance(resource, dict):
            return _format_resource_content(resource)
    if item_type in {"image", "audio"}:
        mime = item.get("mimeType") or "unknown"
        return f"[{item_type} content omitted: {mime}]"
    return json.dumps(item, ensure_ascii=False, indent=2)


def _format_resource_content(item: Any) -> str:
    if not isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False)
    uri = item.get("uri") or "resource"
    if item.get("text") is not None:
        return f"{uri}\n{item.get('text')}"
    if item.get("blob") is not None:
        mime = item.get("mimeType") or "binary"
        return f"{uri}\n[binary content omitted: {mime}]"
    return json.dumps(item, ensure_ascii=False, indent=2)


def _format_prompt_message(item: Any) -> str:
    if not isinstance(item, dict):
        return json.dumps(item, ensure_ascii=False)
    role = item.get("role") or "message"
    content = item.get("content")
    if isinstance(content, dict):
        body = _format_content_item(content)
    else:
        body = json.dumps(content, ensure_ascii=False)
    return f"{role}: {body}"


def _diagnostic_error(error: Exception, stderr_tail: str = "") -> str:
    message = str(error)
    if stderr_tail:
        message = f"{message}\n{stderr_tail}"
    return message.strip()


_registry = MCPRegistry()


def get_mcp_registry() -> MCPRegistry:
    return _registry


def create_mcp_tools(workspace: str, mcp_servers: dict | None) -> list[Tool]:
    if mcp_servers is None:
        return []
    if not mcp_servers:
        _registry.refresh({}, workspace=workspace, force=True)
        return []
    _registry.refresh(mcp_servers, workspace=workspace, force=False)
    return _registry.create_tools(workspace)
