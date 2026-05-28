"""ATRI Dashboard - OpenCode-style vibecoding WebUI.

Serves REST API, WebSocket for real-time updates, and the SPA frontend.
Chat messages go through the WebChat platform adapter and full pipeline.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from quart import Quart, Response, jsonify, request, send_from_directory

from core import logger
from core.config_schema import CHAT_MODEL_CONFIG_DEFAULT, CONFIG_SCHEMA
from dashboard.routes._helpers import (
    AUTH_COOKIE,
    AUTH_EXEMPT_API_PATHS,
    DASHBOARD_CSP,
    PBKDF2_PREFIX,
    local_bridge_api_allowed,
    verify_password,
)

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle


DASHBOARD_MAX_CONTENT_LENGTH = 512 * 1024 * 1024


class Dashboard:
    def __init__(self, lifecycle: Lifecycle, host: str = "127.0.0.1", port: int = 6185):
        self.lifecycle = lifecycle
        self.host = host
        self.port = port
        self.auth_session_tokens: set[str] = set()
        self.auth_tool_session_token = ""
        self._sync_auth_from_config()
        self._reset_auth_sessions()
        self._mcp_push_fingerprint = ""
        self._loop = asyncio.get_running_loop()

        static_dir = str(Path(__file__).parent / "static")
        self.app = Quart("atri-dashboard", static_folder=static_dir, static_url_path="/static")
        self.app.config["MAX_CONTENT_LENGTH"] = DASHBOARD_MAX_CONTENT_LENGTH
        self._ws_clients: set = set()
        self._audio_clients: set = set()
        self._audio_send_failed_clients: set = set()
        self._wire_audio_host()
        self._register_routes()

        from dashboard.music import bp as music_bp
        from dashboard.music import init_music

        init_music(lifecycle)
        self.app.register_blueprint(music_bp)

        if lifecycle.process_stage:
            lifecycle.process_stage.broadcast_fn = self.broadcast

    def _wire_audio_host(self) -> None:
        """Route Rust host PCM chunks into the dashboard audio WebSocket."""
        from core.host import configure_host_manager

        audio_cfg = self.lifecycle.config.get("audio_host", {})
        host = configure_host_manager(
            binary_path=audio_cfg.get("binary_path") or None,
            sample_rate=int(audio_cfg.get("sample_rate", 48000) or 48000),
            buffer_size=int(audio_cfg.get("buffer_size", 256) or 256),
            audio_engine=audio_cfg.get("audio_engine") or "default",
            bit_depth=audio_cfg.get("bit_depth") or "f32",
        )

        def schedule_audio(pcm_bytes: bytes, nframes: int, channels: int, sample_rate: int) -> None:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    self.broadcast_audio(pcm_bytes, nframes, channels, sample_rate)
                )
            )

        host.set_audio_callback(schedule_audio)

    # ── Config / auth state ──

    def _sync_auth_from_config(self):
        """Refresh auth state from config and reset sessions on auth changes."""
        previous_state = (
            getattr(self, "auth_username", None),
            getattr(self, "auth_password", None),
            getattr(self, "auth_enabled", None),
            getattr(self, "auth_setup_required", None),
        )
        dashboard_cfg = self.lifecycle.config.get("dashboard", {})
        dashboard_enabled = bool(dashboard_cfg.get("enabled", True))
        self.auth_username = str(dashboard_cfg.get("username", "") or "")
        self.auth_password = str(dashboard_cfg.get("password", "") or "")
        self.auth_password_is_hashed = self.auth_password.startswith(PBKDF2_PREFIX)
        self.auth_setup_required = bool(
            dashboard_enabled and (not self.auth_username or not self.auth_password)
        )
        self.auth_enabled = bool(dashboard_enabled and not self.auth_setup_required)
        current_state = (
            self.auth_username,
            self.auth_password,
            self.auth_enabled,
            self.auth_setup_required,
        )
        if (
            previous_state[0] is not None
            and previous_state != current_state
            and hasattr(self, "auth_session_tokens")
        ):
            self._reset_auth_sessions()

    # ── Model helpers ──

    def _apply_model(self, provider_name: str, model: str):
        """Apply a model + provider credentials as the current active model."""
        lc = self.lifecycle
        providers = lc.config.get("providers", {})
        if provider_name and provider_name in providers:
            pcfg = providers[provider_name]
            lc.config["api_key"] = pcfg.get("api_key", "")
            lc.config["base_url"] = pcfg.get("base_url") or None
            lc.config["api_format"] = pcfg.get("api_format", "openai")
        lc.config["model"] = model
        lc.config["model_provider"] = provider_name
        model_config = _chat_model_entry_config(lc.config, provider_name, model)
        if lc.process_stage:
            lc.process_stage.update_config(
                model=model,
                model_provider=provider_name,
                api_key=lc.config["api_key"],
                base_url=lc.config.get("base_url"),
                api_format=lc.config.get("api_format", "openai"),
                active_models=lc.config.get("active_models", []),
                embedding_model=lc.config.get("embedding_model", ""),
                embedding_provider=lc.config.get("embedding_provider", ""),
                active_embedding_models=lc.config.get("active_embedding_models", []),
                rerank_model=lc.config.get("rerank_model", ""),
                rerank_provider=lc.config.get("rerank_provider", ""),
                active_rerank_models=lc.config.get("active_rerank_models", []),
                providers=lc.config.get("providers", {}),
                max_tokens=model_config["max_tokens"],
                temperature=model_config["temperature"],
                max_context_tokens=model_config["max_context_tokens"],
                max_rounds=model_config["max_rounds"],
            )

    def _clear_current_model(self):
        """Clear the selected chat model when no configured model remains."""
        lc = self.lifecycle
        lc.config["model"] = ""
        lc.config["api_key"] = ""
        lc.config["base_url"] = None
        lc.config["api_format"] = "openai"
        lc.config["model_provider"] = ""
        self._push_model_config()

    def _push_model_config(self):
        lc = self.lifecycle
        if lc.process_stage:
            lc.process_stage.update_config(
                model=lc.config.get("model", ""),
                model_provider=lc.config.get("model_provider", ""),
                api_key=lc.config.get("api_key", ""),
                base_url=lc.config.get("base_url"),
                api_format=lc.config.get("api_format", "openai"),
                active_models=lc.config.get("active_models", []),
                embedding_model=lc.config.get("embedding_model", ""),
                embedding_provider=lc.config.get("embedding_provider", ""),
                active_embedding_models=lc.config.get("active_embedding_models", []),
                rerank_model=lc.config.get("rerank_model", ""),
                rerank_provider=lc.config.get("rerank_provider", ""),
                active_rerank_models=lc.config.get("active_rerank_models", []),
                providers=lc.config.get("providers", {}),
            )

    def _current_uses_provider_config(self, provider_cfg: dict | None) -> bool:
        if not isinstance(provider_cfg, dict):
            return False
        lc = self.lifecycle
        return (
            (lc.config.get("base_url") or "") == (provider_cfg.get("base_url") or "")
            and lc.config.get("api_format", "openai") == provider_cfg.get("api_format", "openai")
            and lc.config.get("api_key", "") == provider_cfg.get("api_key", "")
        )

    def _active_model_entry_available(self, entry: dict) -> bool:
        if not isinstance(entry, dict) or not entry.get("model"):
            return False
        provider = entry.get("provider", "")
        return not provider or provider in self.lifecycle.config.get("providers", {})

    def _select_first_active_model_or_clear(self):
        active_models = self.lifecycle.config.get("active_models", [])
        for entry in active_models:
            if not self._active_model_entry_available(entry):
                continue
            self._apply_model(entry.get("provider", ""), entry.get("model", ""))
            return
        self._clear_current_model()

    # ── Skills / MCP helpers ──

    def _reload_skills_prompt(self):
        """Hot-reload the skills prompt on all live agents after a config change."""
        ps = self.lifecycle.process_stage
        if ps is not None:
            ps.reload_skills()

    async def _reload_mcp_tools(self, force: bool = False) -> dict:
        """Discover configured MCP servers and push tools to live agents."""
        from core.tools.mcp import get_mcp_registry

        servers = self.lifecycle.config.get("mcp_servers", {})
        workspace = self.lifecycle.config.get("workspace", ".")
        registry = get_mcp_registry()
        snapshot = await asyncio.to_thread(
            registry.refresh,
            servers,
            workspace,
            force,
        )
        fingerprint = str(snapshot.get("push_fingerprint") or snapshot.get("fingerprint") or "")
        ps = self.lifecycle.process_stage
        if ps is not None and (force or fingerprint != self._mcp_push_fingerprint):
            await asyncio.to_thread(ps.update_config, mcp_servers=servers)
            self._mcp_push_fingerprint = fingerprint
        return snapshot

    async def _validate_mcp_server(self, name: str, cfg: dict) -> dict:
        from core.tools.mcp import get_mcp_registry

        return await asyncio.to_thread(
            get_mcp_registry().validate_server,
            name,
            cfg,
            self.lifecycle.config.get("workspace", "."),
        )

    # ── Auth helpers ──

    def _provided_session_token(self) -> str:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.headers.get("X-ATRI-Session", "") or request.cookies.get(AUTH_COOKIE, "")

    def _session_ok(self, token: str) -> bool:
        return bool(self.auth_enabled and token) and any(
            hmac.compare_digest(token, session_token) for session_token in self.auth_session_tokens
        )

    def _publish_auth_token_to_tools(self) -> None:
        """Share the dashboard's internal tool session token with Agent tools."""
        from core.tools.studio import set_dashboard_session_token

        set_dashboard_session_token(self.auth_tool_session_token)

    def _create_auth_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self.auth_session_tokens.add(token)
        return token

    def _revoke_auth_session(self, token: str) -> None:
        if token:
            self.auth_session_tokens.discard(token)

    def _reset_auth_sessions(self) -> None:
        self.auth_session_tokens.clear()
        self.auth_tool_session_token = self._create_auth_session() if self.auth_enabled else ""
        self._publish_auth_token_to_tools()

    def _credentials_ok(self, username: str, password: str) -> bool:
        return (
            self.auth_enabled
            and hmac.compare_digest(username, self.auth_username)
            and verify_password(self.auth_password, password)
        )

    def _request_authenticated(self) -> bool:
        return self._session_ok(self._provided_session_token())

    # ── Route registration ──

    def _register_routes(self):
        """Register all API routes — core handlers here, domain routes via sub-modules."""
        self._register_core_handlers()

        from dashboard.routes import register_all

        register_all(self)

    def _register_core_handlers(self):
        app = self.app

        @app.before_request
        async def require_dashboard_auth():
            if not self.auth_enabled and not self.auth_setup_required:
                return None
            path = request.path
            if request.method == "OPTIONS":
                return None
            if not path.startswith("/api/"):
                return None
            if path in AUTH_EXEMPT_API_PATHS:
                return None
            if local_bridge_api_allowed(path, request):
                return None
            if self.auth_setup_required:
                return jsonify({"error": "setup required", "setup_required": True}), 428
            if self._request_authenticated():
                return None
            return jsonify({"error": "authentication required"}), 401

        @app.after_request
        async def add_security_headers(response: Response):
            """Add security headers to all responses."""
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault(
                "Content-Security-Policy",
                DASHBOARD_CSP,
            )
            response.headers.setdefault("Cache-Control", "no-store")
            return response

        @app.route("/")
        async def index():
            response = await send_from_directory(app.static_folder or "static", "index.html")
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.route("/api/config/schema")
        async def config_schema():
            return jsonify(CONFIG_SCHEMA)

        @app.route("/api/ping")
        async def api_ping():
            return jsonify({"pong": True, "routes": len(list(app.url_map.iter_rules()))})

    # ── Instance helpers ──

    def _find_approval_tool(self, session_id: str, approval_id: str = ""):
        """Find any live tool with a pending WebUI approval request."""
        if not self.lifecycle.process_stage:
            return None
        agent = self.lifecycle.process_stage.get_agent(session_id)
        if not agent:
            return None
        for tool in agent.tools:
            if not getattr(tool, "has_pending", False):
                continue
            if approval_id and not _tool_has_pending_approval(tool, approval_id):
                continue
            if approval_id and _tool_has_pending_approval(tool, approval_id):
                return tool
            if not approval_id:
                return tool
        return None

    def _find_bash_tool(self, session_id: str):
        """Backward-compatible alias for the shared approval lookup."""
        return self._find_approval_tool(session_id)

    def _runtime_store(self):
        if not self.lifecycle.process_stage:
            return None
        return getattr(self.lifecycle.process_stage, "runtime_store", None)

    async def broadcast(self, data: dict):
        msg = json.dumps(data)
        for ws in list(self._ws_clients):
            try:
                await ws.send(msg)
            except Exception:
                self._ws_clients.discard(ws)

    def _has_audio_stream_consumers(self) -> bool:
        return any(ws not in self._audio_send_failed_clients for ws in self._audio_clients)

    async def register_audio_client(self, ws: object) -> None:
        """Track an audio WebSocket client and enable host PCM streaming."""
        had_consumers = self._has_audio_stream_consumers()
        self._audio_send_failed_clients.discard(ws)
        self._audio_clients.add(ws)
        if not had_consumers and self._has_audio_stream_consumers():
            await self._set_host_streaming(True)

    async def discard_audio_client(self, ws: object) -> None:
        """Forget an audio WebSocket client and stop PCM when the last one leaves."""
        had_client = ws in self._audio_clients
        self._audio_clients.discard(ws)
        self._audio_send_failed_clients.discard(ws)
        if had_client and not self._audio_clients:
            await self._set_host_streaming(False)

    async def reconcile_audio_streaming_state(self) -> None:
        """Replay desired PCM streaming state after the host starts or restarts."""
        if self._has_audio_stream_consumers():
            await self._set_host_streaming(True)

    async def _set_host_streaming(self, enabled: bool) -> None:
        from core.host import get_host_manager

        host = get_host_manager()
        if not host.is_running:
            return
        try:
            await host.send_command("set_streaming", {"enabled": enabled})
        except Exception as e:
            logger.debug("Failed to set host streaming=%s: %s", enabled, e)

    async def broadcast_audio(
        self,
        pcm_bytes: bytes,
        nframes: int,
        channels: int,
        sample_rate: int = 48000,
    ):
        """Send raw PCM audio to all connected audio WebSocket clients."""
        audio_clients = self._audio_clients
        if not audio_clients:
            return
        # Prepend a JSON header line followed by raw PCM bytes
        header = json.dumps(
            {
                "type": "audio",
                "samples": nframes,
                "channels": channels,
                "sample_rate": sample_rate,
                "format": "f32_le_interleaved",
            }
        )
        for ws in list(audio_clients):
            if ws in self._audio_send_failed_clients:
                continue
            try:
                await ws.send(header)
                await ws.send(pcm_bytes)
            except Exception as e:
                logger.debug("Audio WebSocket send failed: %s", e)
                self._audio_send_failed_clients.add(ws)
                close = getattr(ws, "close", None)
                if close is not None:
                    try:
                        await close(1011)
                    except Exception as close_error:
                        logger.debug(
                            "Audio WebSocket close after send failure failed: %s",
                            close_error,
                        )

    async def run(self):
        from hypercorn.asyncio import serve
        from hypercorn.config import Config

        config = Config()
        config.bind = [f"{self.host}:{self.port}"]
        config.accesslog = None
        logger.info(f"\n  ATRI Dashboard ready\n  -> http://localhost:{self.port}\n")
        if self.auth_setup_required:
            logger.info("Dashboard setup required. Open the dashboard to create an admin account.")
        elif self.auth_enabled:
            logger.info(
                "Dashboard auth enabled. Username: "
                f"{self.auth_username}. Password is stored in config.yaml."
            )
        self.shutdown_event = asyncio.Event()
        await serve(self.app, config, shutdown_trigger=self.shutdown_event.wait)

    async def stop(self):
        if hasattr(self, "shutdown_event"):
            self.shutdown_event.set()


def _tool_has_pending_approval(tool: object, approval_id: str) -> bool:
    infos = getattr(tool, "pending_infos", None)
    if isinstance(infos, list):
        return any(str(info.get("approval_id") or "") == approval_id for info in infos)
    info = getattr(tool, "pending_info", None)
    if isinstance(info, dict):
        return str(info.get("approval_id") or "") == approval_id
    return False


def _chat_model_entry_config(config: dict, provider_name: str, model: str) -> dict:
    fallback = {
        "max_tokens": int(config.get("max_tokens") or CHAT_MODEL_CONFIG_DEFAULT["max_tokens"]),
        "temperature": float(config.get("temperature", CHAT_MODEL_CONFIG_DEFAULT["temperature"])),
        "max_context_tokens": int(
            config.get("max_context_tokens") or CHAT_MODEL_CONFIG_DEFAULT["max_context_tokens"]
        ),
        "max_rounds": int(config.get("max_rounds") or CHAT_MODEL_CONFIG_DEFAULT["max_rounds"]),
    }
    for entry in config.get("active_models", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("model", "") != model or entry.get("provider", "") != provider_name:
            continue
        entry_config = entry.get("config")
        return {**fallback, **(entry_config if isinstance(entry_config, dict) else {})}
    return fallback
