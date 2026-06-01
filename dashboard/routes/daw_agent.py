"""DAW/VST embedded agent routes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from quart import jsonify, request

from core import logger
from core.platform.daw_agent import normalize_daw_host_context, normalize_daw_workspace
from dashboard import music as music_routes
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
        workspace = normalize_daw_workspace(str(data.get("workspace") or "atri_studio").strip())
        try:
            host_context = normalize_daw_host_context(
                data.get("host_context"),
                strict=True,
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        model = str(data.get("model") or "").strip()
        model_provider = str(data.get("model_provider") or data.get("provider") or "").strip()
        published_host_context = music_routes.bridge_host_context_for_instance(instance_id)
        if published_host_context:
            host_context = normalize_daw_host_context(
                {**published_host_context, **host_context},
                strict=True,
            )

        host_project_sync = None
        if workspace == "host_project" and data.get("sync_host_project") is True:
            export_request = None
            if data.get("request_host_dawproject_export") is True:
                host_name = str(host_context.get("host") or data.get("host") or "studio_one")
                export_request = music_routes.request_host_dawproject_snapshot_export(
                    host=host_name,
                    source="daw_agent",
                    instance_id=instance_id,
                )
            host_project_sync = await music_routes.sync_latest_host_dawproject_for_daw_agent()
            if export_request:
                host_project_sync["export_request"] = {
                    "id": export_request["id"],
                    "host": export_request["host"],
                    "source": export_request["source"],
                    "instance_id": export_request["instance_id"],
                    "output_path": export_request["output_path"],
                    "request_path": export_request["request_path"],
                }
            sync_context = music_routes.host_project_sync_prompt_context(host_project_sync)
            if sync_context:
                host_context = normalize_daw_host_context(
                    {**host_context, "host_project_sync": sync_context},
                    strict=True,
                )

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
                    **({"host_project_sync": host_project_sync} if host_project_sync else {}),
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
