"""DAW/VST embedded agent routes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from quart import jsonify, request

from core import logger
from core.platform.daw_agent import normalize_daw_host_context
from dashboard.routes.chat import _normalize_chat_images, _serialize_response_chain

if TYPE_CHECKING:
    from dashboard.server import Dashboard


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    @app.route("/api/daw-agent/chat", methods=["POST"])
    async def daw_agent_chat():
        data = await request.get_json(silent=True) or {}
        message = str(data.get("message") or "").strip()
        try:
            images = _normalize_chat_images(data.get("images"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if not message and not images:
            return jsonify({"error": "empty message"}), 400

        adapter = getattr(dashboard.lifecycle, "daw_agent", None)
        if not adapter:
            return jsonify({"error": "daw agent adapter not available"}), 503

        project_session_id = str(data.get("project_session_id") or "").strip()
        instance_id = str(data.get("instance_id") or "").strip()
        workspace = str(data.get("workspace") or "atri_studio").strip()
        try:
            host_context = normalize_daw_host_context(
                data.get("host_context"),
                strict=True,
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        model = str(data.get("model") or "").strip()
        model_provider = str(data.get("model_provider") or data.get("provider") or "").strip()

        event, future = adapter.create_event(
            message,
            project_session_id,
            instance_id=instance_id,
            workspace=workspace,
            host_context=host_context,
            images=images,
            model=model,
            model_provider=model_provider,
        )
        await dashboard.broadcast({"type": "thinking", "session_id": event.unified_msg_origin})

        try:
            result = await asyncio.wait_for(future, timeout=300)
            return jsonify(
                {
                    "response": result.get("text", ""),
                    "chain": _serialize_response_chain(result.get("chain")),
                    "session_id": event.unified_msg_origin,
                }
            )
        except TimeoutError:
            _cancel_daw_agent_request(dashboard, adapter, event)
            return jsonify({"error": "Agent timed out (300s)"}), 504
        except Exception as e:
            _cancel_daw_agent_request(dashboard, adapter, event)
            logger.exception("DAW agent chat error: %s", e)
            return jsonify({"error": str(e)}), 500


def _cancel_daw_agent_request(dashboard: Dashboard, adapter: Any, event: Any) -> None:
    cancel_request = getattr(adapter, "cancel_request", None)
    if callable(cancel_request):
        try:
            cancel_request(event)
        except Exception as e:
            logger.warning("Failed to cancel DAW agent request: %s", e)

    lifecycle = getattr(dashboard, "lifecycle", None)
    cancel_operation = getattr(lifecycle, "cancel_operation", None) if lifecycle else None
    if not callable(cancel_operation):
        return
    try:
        cancel_operation(session_id=event.unified_msg_origin)
    except Exception as e:
        logger.warning("Failed to cancel DAW agent operation: %s", e)
