"""Chat, agent-mode, tools, and command-approval routes."""

from __future__ import annotations

import asyncio
import base64
import binascii
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quart import jsonify, request

from core import logger
from core.platform.message import display_session_id, normalize_session_id

if TYPE_CHECKING:
    from dashboard.server import Dashboard

_CHAT_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
_MAX_CHAT_IMAGES = 4
_MAX_CHAT_IMAGE_BYTES = 5 * 1024 * 1024


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
        session_id = str(data.get("session_id") or "webchat_default")
        try:
            images = _normalize_chat_images(data.get("images"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if not message and not images:
            return jsonify({"error": "empty message"}), 400
        webchat = dashboard.lifecycle.webchat
        if not webchat:
            return jsonify({"error": "webchat adapter not available"}), 503

        event, future = webchat.create_event(message, session_id, images=images)
        await dashboard.broadcast({"type": "thinking", "session_id": session_id})

        try:
            result = await asyncio.wait_for(future, timeout=300)
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
                    "session_id": display_session_id(event.unified_msg_origin),
                    "tool_events": event._extras.get("tool_events", []),
                    "token_usage": token_usage,
                }
            )
        except TimeoutError:
            return jsonify({"error": "Agent timed out (300s)"}), 504
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
        bash_tool = dashboard._find_bash_tool(session_id)
        if bash_tool and bash_tool.has_pending:
            result = bash_tool.approve_pending()
            await dashboard.broadcast(
                {
                    "type": "command_approved",
                    "session_id": session_id,
                    "result": result,
                }
            )
            return jsonify({"ok": True, "result": result})
        return jsonify({"error": "no pending command"}), 404

    @app.route("/api/reject-command", methods=["POST"])
    async def reject_command():
        data = await request.get_json()
        session_id = normalize_session_id(data.get("session_id", ""))
        bash_tool = dashboard._find_bash_tool(session_id)
        if bash_tool and bash_tool.has_pending:
            result = bash_tool.reject_pending()
            await dashboard.broadcast(
                {
                    "type": "command_rejected",
                    "session_id": session_id,
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
        bash_tool = dashboard._find_bash_tool(session_id)
        if bash_tool and bash_tool.has_pending:
            return jsonify({"pending": True, **bash_tool.pending_info})
        return jsonify({"pending": False})
