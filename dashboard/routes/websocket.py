"""WebSocket handler and SPA fallback."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from quart import jsonify, send_from_directory, websocket

from core.platform.message import normalize_session_id
from dashboard.routes._helpers import AUTH_COOKIE, cookie_value, parse_int, websocket_from_loopback

if TYPE_CHECKING:
    from dashboard.server import Dashboard


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    @app.websocket("/ws")
    async def ws_handler():
        if dashboard.auth_setup_required:
            await websocket.close(1008)
            return
        if dashboard.auth_enabled:
            session_token = websocket.headers.get("X-ATRI-Session", "") or cookie_value(
                websocket.headers.get("Cookie", ""), AUTH_COOKIE
            )
            is_daw_surface = websocket.args.get("surface") == "daw-agent"
            if not dashboard._session_ok(session_token) and not (
                is_daw_surface and websocket_from_loopback(websocket)
            ):
                await websocket.close(1008)
                return
        ws_obj = websocket._get_current_object()  # type: ignore[attr-defined]
        dashboard._ws_clients.add(ws_obj)
        try:
            while True:
                data = await websocket.receive()
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
                elif msg.get("type") == "runtime_replay":
                    store = dashboard._runtime_store()
                    if store is None:
                        continue
                    session_id = str(msg.get("session_id") or "").strip()
                    if not session_id:
                        continue
                    thread_id = normalize_session_id(session_id)
                    since_seq = parse_int(msg.get("since_seq"), 0)
                    limit = parse_int(msg.get("limit"), 1000)
                    events = store.events_since(
                        thread_id=thread_id,
                        since_seq=since_seq,
                        limit=limit,
                    )
                    for runtime_event in events:
                        await websocket.send(json.dumps(runtime_event.to_wire_payload()))
                elif msg.get("type") == "studio_command":
                    cmd = str(msg.get("cmd") or "").strip()
                    request_id = str(msg.get("request_id") or "")
                    if cmd != "open_plugin_editor":
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "studio_command_result",
                                    "cmd": cmd,
                                    "request_id": request_id,
                                    "ok": False,
                                    "error": "unsupported studio command",
                                }
                            )
                        )
                        continue

                    from dashboard.music import open_plugin_editor_for_track

                    track_id = parse_int(msg.get("track_id"), 0)
                    slot_id = str(msg.get("slot_id") or "instrument")
                    result, status = await open_plugin_editor_for_track(track_id, slot_id=slot_id)
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "studio_command_result",
                                "cmd": cmd,
                                "request_id": request_id,
                                "status": status,
                                **result,
                            }
                        )
                    )
        except asyncio.CancelledError:
            pass
        finally:
            dashboard._ws_clients.discard(ws_obj)

    @app.websocket("/ws/audio")
    async def ws_audio_handler():
        """WebSocket endpoint for streaming low-latency audio from the Rust host."""
        if dashboard.auth_setup_required:
            await websocket.close(1008)
            return
        if dashboard.auth_enabled:
            session_token = websocket.headers.get("X-ATRI-Session", "") or cookie_value(
                websocket.headers.get("Cookie", ""), AUTH_COOKIE
            )
            if not dashboard._session_ok(session_token):
                await websocket.close(1008)
                return
        ws_obj = websocket._get_current_object()  # type: ignore[attr-defined]
        await dashboard.register_audio_client(ws_obj)
        try:
            while True:
                data = await websocket.receive()
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send(json.dumps({"type": "pong"}))
                elif msg.get("type") == "transport_control":
                    # Forward transport commands from frontend to host
                    action = msg.get("action", "")
                    from core.host import get_host_manager

                    host = get_host_manager()
                    if host.is_running:
                        await host.send_command(action)
        except asyncio.CancelledError:
            pass
        finally:
            await dashboard.discard_audio_client(ws_obj)

    # ── SPA fallback (via 404 handler so it never shadows API routes) ──

    @app.errorhandler(404)
    async def spa_fallback(e):
        """Serve index.html for all non-API routes (Vue SPA)."""
        from quart import request as _req

        path = _req.path
        if path.startswith(("/api/", "/ws", "/static/")):
            return jsonify({"error": "not found"}), 404
        response = await send_from_directory(app.static_folder or "static", "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response
