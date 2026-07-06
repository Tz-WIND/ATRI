"""Chat, agent-mode, tools, and command-approval routes."""

from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from quart import jsonify, request

from core import logger
from core.config_schema import AGENT_TIMEOUT_SECONDS_DEFAULT, AGENT_TIMEOUT_SECONDS_MINIMUM
from core.document_text import DocumentTextError, extract_document_text
from core.platform.message import Image, Plain, display_session_id, normalize_session_id

if TYPE_CHECKING:
    from dashboard.server import Dashboard

_CHAT_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_CHAT_IMAGES = 4
_MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_CHAT_FILES = 4
_MAX_CHAT_FILE_BYTES = 20 * 1024 * 1024


def agent_timeout_seconds(config: dict[str, Any]) -> float:
    try:
        timeout = float(config.get("agent_timeout_seconds", AGENT_TIMEOUT_SECONDS_DEFAULT))
    except (TypeError, ValueError):
        return AGENT_TIMEOUT_SECONDS_DEFAULT
    if timeout < AGENT_TIMEOUT_SECONDS_MINIMUM:
        return AGENT_TIMEOUT_SECONDS_DEFAULT
    return timeout


def format_timeout_seconds(timeout: float) -> str:
    return str(int(timeout)) if float(timeout).is_integer() else f"{timeout:g}"


def _serialize_response_chain(chain: object) -> list[dict[str, object]] | None:
    if not isinstance(chain, list):
        return None
    items: list[dict[str, object]] = []
    for comp in chain:
        if isinstance(comp, Plain):
            items.append({"type": "plain", "text": comp.text})
        elif isinstance(comp, Image):
            file_value = "" if comp.file.startswith("base64://") else comp.file
            items.append(
                {
                    "type": "image",
                    "url": comp.url,
                    "file": file_value,
                    "mime_type": comp.mime_type,
                    "size": comp.size,
                }
            )
    return items


def _parse_image_data_url(data_url: str) -> tuple[str, int]:
    if not data_url.startswith("data:") or "," not in data_url:
        raise ValueError("images must be base64 data URLs")

    header, encoded = data_url.split(",", 1)
    meta = header[5:].split(";")
    mime_type = (meta[0] or "").lower()
    flags = {part.lower() for part in meta[1:]}
    if mime_type not in _CHAT_IMAGE_MIME_TYPES:
        raise ValueError("image type must be PNG, JPEG, WebP, or GIF")
    if "base64" not in flags:
        raise ValueError("image data URL must be base64 encoded")

    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("invalid image data") from e

    if not decoded:
        raise ValueError("image data is empty")
    if len(decoded) > _MAX_CHAT_IMAGE_BYTES:
        limit_mb = _MAX_CHAT_IMAGE_BYTES // (1024 * 1024)
        raise ValueError(f"image must be {limit_mb} MB or smaller")
    return mime_type, len(decoded)


def _parse_file_data_url(data_url: str) -> tuple[str, bytes]:
    if not data_url.startswith("data:") or "," not in data_url:
        raise ValueError("files must be base64 data URLs")

    header, encoded = data_url.split(",", 1)
    meta = header[5:].split(";")
    mime_type = (meta[0] or "application/octet-stream").lower()
    flags = {part.lower() for part in meta[1:]}
    if "base64" not in flags:
        raise ValueError("file data URL must be base64 encoded")

    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("invalid file data") from e

    if not decoded:
        raise ValueError("file data is empty")
    if len(decoded) > _MAX_CHAT_FILE_BYTES:
        limit_mb = _MAX_CHAT_FILE_BYTES // (1024 * 1024)
        raise ValueError(f"file must be {limit_mb} MB or smaller")
    return mime_type, decoded


def _normalize_chat_images(raw_images: object) -> list[dict[str, Any]]:
    if raw_images in (None, ""):
        return []
    if not isinstance(raw_images, list):
        raise ValueError("images must be a list")
    if len(raw_images) > _MAX_CHAT_IMAGES:
        raise ValueError(f"at most {_MAX_CHAT_IMAGES} images can be attached")

    images: list[dict[str, Any]] = []
    for index, item in enumerate(raw_images, start=1):
        if not isinstance(item, dict):
            raise ValueError("each image must be an object")
        data_url = str(item.get("dataUrl") or item.get("url") or "").strip()
        mime_type, size = _parse_image_data_url(data_url)
        name = Path(str(item.get("name") or f"image-{index}")).name[:120]
        images.append(
            {
                "url": data_url,
                "file": name or f"image-{index}",
                "mime_type": mime_type,
                "size": size,
            }
        )
    return images


def _normalize_chat_files(raw_files: object) -> list[dict[str, Any]]:
    if raw_files in (None, ""):
        return []
    if not isinstance(raw_files, list):
        raise ValueError("files must be a list")
    if len(raw_files) > _MAX_CHAT_FILES:
        raise ValueError(f"at most {_MAX_CHAT_FILES} files can be attached")

    files: list[dict[str, Any]] = []
    for index, item in enumerate(raw_files, start=1):
        if not isinstance(item, dict):
            raise ValueError("each file must be an object")
        data_url = str(item.get("dataUrl") or item.get("url") or "").strip()
        mime_type, content = _parse_file_data_url(data_url)
        name = Path(str(item.get("name") or f"attachment-{index}.txt")).name[:120]
        file_name = name or f"attachment-{index}.txt"
        try:
            text = extract_document_text(file_name, content)
        except DocumentTextError as e:
            raise ValueError(str(e)) from e
        files.append(
            {
                "file": file_name,
                "mime_type": mime_type,
                "size": len(content),
                "text": text,
            }
        )
    return files


def _message_with_file_context(message: str, files: list[dict[str, Any]]) -> str:
    parts = [str(message or "").strip()] if str(message or "").strip() else []
    for item in files:
        file_name = str(item.get("file") or "attachment")
        text = str(item.get("text") or "").strip()
        if text:
            parts.append(f"[File: {file_name}]\n{text}")
    return "\n\n".join(parts).strip()


def _file_display_attachments(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attachments = []
    for item in files:
        name = str(item.get("file") or "").strip()
        if not name:
            continue
        attachments.append(
            {
                "kind": "file",
                "name": name,
                "type": str(item.get("mime_type") or ""),
                "size": int(item.get("size") or 0),
            }
        )
    return attachments


# ── Route registration ──


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    @app.route("/api/agent-mode", methods=["GET"])
    async def get_agent_mode():
        mode = (
            dashboard.lifecycle.process_stage.agent_mode
            if dashboard.lifecycle.process_stage
            else dashboard.lifecycle.config.get("agent_mode", "agent")
        )
        return jsonify({"mode": mode})

    @app.route("/api/agent-mode", methods=["POST"])
    async def set_agent_mode():
        from core.agent.mode import normalize_agent_mode

        data = await request.get_json(silent=True) or {}
        try:
            mode = normalize_agent_mode(data.get("mode", ""))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        reason = str(data.get("reason") or "user selected mode").strip()
        dashboard.lifecycle.config["agent_mode"] = mode
        if dashboard.lifecycle.process_stage:
            mode = dashboard.lifecycle.process_stage.set_agent_mode(
                mode,
                source="user",
                reason=reason,
            )
        dashboard.lifecycle.save_config()
        return jsonify({"mode": mode})

    @app.route("/api/chat", methods=["POST"])
    async def chat():
        data = await request.get_json(silent=True) or {}
        message = str(data.get("message") or "").strip()
        display_message = message
        session_id = str(data.get("session_id") or "webchat_default")
        try:
            images = _normalize_chat_images(data.get("images"))
            files = _normalize_chat_files(data.get("files"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        message = _message_with_file_context(message, files)
        if not message and not images:
            return jsonify({"error": "empty message"}), 400
        webchat = dashboard.lifecycle.webchat
        if not webchat:
            return jsonify({"error": "webchat adapter not available"}), 503

        event, future = webchat.create_event(
            message,
            session_id,
            images=images,
            display_user_input=display_message,
            file_attachments=_file_display_attachments(files),
        )
        await dashboard.broadcast({"type": "thinking", "session_id": session_id})

        timeout_seconds = agent_timeout_seconds(dashboard.lifecycle.config)
        try:
            result = await asyncio.wait_for(future, timeout=timeout_seconds)
            response_text = result.get("text", "")
            token_usage: dict[str, Any] = {}
            if dashboard.lifecycle.process_stage:
                agent = dashboard.lifecycle.process_stage.get_agent(event.unified_msg_origin)
                if agent:
                    token_usage = {
                        "prompt": agent.llm.total_prompt_tokens,
                        "completion": agent.llm.total_completion_tokens,
                        "cost": agent.llm.estimated_cost,
                    }
            return jsonify(
                {
                    "response": response_text,
                    "chain": _serialize_response_chain(result.get("chain")),
                    "session_id": display_session_id(event.unified_msg_origin),
                    "tool_events": event._extras.get("tool_events", []),
                    "token_usage": token_usage,
                }
            )
        except TimeoutError:
            timeout_label = format_timeout_seconds(timeout_seconds)
            return jsonify({"error": f"Agent timed out ({timeout_label}s)"}), 504
        except Exception as e:
            logger.exception(f"WebUI chat error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/chat/cancel", methods=["POST"])
    async def cancel_chat():
        """Cancel the currently running agent operation for a session."""
        data = await request.get_json(silent=True) or {}
        session_id = data.get("session_id", "")
        cancelled = dashboard.lifecycle.cancel_operation(
            session_id=session_id if session_id else None
        )
        return jsonify({"ok": cancelled})

    @app.route("/api/tools")
    async def list_tools():
        from core.tools import create_tools

        ws = dashboard.lifecycle.config.get("workspace", ".")
        tools = await asyncio.to_thread(
            create_tools,
            ws,
            mcp_servers=dashboard.lifecycle.config.get("mcp_servers", {}),
        )
        return jsonify(
            [
                {
                    "name": t.name,
                    "description": t.description,
                    "metadata": t.metadata(),
                }
                for t in tools
            ]
        )

    @app.route("/api/approve-command", methods=["POST"])
    async def approve_command():
        data = await request.get_json()
        session_id = normalize_session_id(data.get("session_id", ""))
        approval_id = str(data.get("approval_id") or "")
        approval_tool = dashboard._find_approval_tool(session_id, approval_id=approval_id)
        if approval_tool and approval_tool.has_pending:
            result = await asyncio.to_thread(
                _call_pending_approval_method,
                approval_tool,
                "approve_pending",
                approval_id,
            )
            if result is None:
                return jsonify({"error": "no pending command"}), 404
            await dashboard.broadcast(
                {
                    "type": "command_approved",
                    "session_id": session_id,
                    "approval_id": approval_id,
                    "result": result,
                }
            )
            return jsonify({"ok": True, "result": result})
        return jsonify({"error": "no pending command"}), 404

    @app.route("/api/reject-command", methods=["POST"])
    async def reject_command():
        data = await request.get_json()
        session_id = normalize_session_id(data.get("session_id", ""))
        approval_id = str(data.get("approval_id") or "")
        approval_tool = dashboard._find_approval_tool(session_id, approval_id=approval_id)
        if approval_tool and approval_tool.has_pending:
            result = await asyncio.to_thread(
                _call_pending_approval_method,
                approval_tool,
                "reject_pending",
                approval_id,
            )
            if result is None:
                return jsonify({"error": "no pending command"}), 404
            await dashboard.broadcast(
                {
                    "type": "command_rejected",
                    "session_id": session_id,
                    "approval_id": approval_id,
                    "result": result,
                }
            )
            return jsonify({"ok": True, "result": result})
        return jsonify({"error": "no pending command"}), 404

    @app.route("/api/pending-command", methods=["POST"])
    async def pending_command():
        """Check if there is a pending dangerous command for a session."""
        data = await request.get_json()
        session_id = normalize_session_id(data.get("session_id", ""))
        approval_id = str(data.get("approval_id") or "")
        approval_tool = dashboard._find_approval_tool(session_id, approval_id=approval_id)
        if approval_tool and approval_tool.has_pending:
            pending_info = _pending_approval_info(approval_tool, approval_id)
            if pending_info:
                return jsonify({"pending": True, **pending_info})
        return jsonify({"pending": False})


def _call_pending_approval_method(tool: object, method_name: str, approval_id: str) -> str | None:
    method = getattr(tool, method_name)
    if approval_id and inspect.signature(method).parameters:
        return cast(str | None, method(approval_id))
    return cast(str | None, method())


def _pending_approval_info(tool: object, approval_id: str) -> dict[str, str] | None:
    if approval_id:
        infos = getattr(tool, "pending_infos", None)
        if isinstance(infos, list):
            for info in infos:
                if str(info.get("approval_id") or "") == approval_id:
                    return cast(dict[str, str], info)
    info = getattr(tool, "pending_info", None)
    return cast(dict[str, str], info) if isinstance(info, dict) else None
