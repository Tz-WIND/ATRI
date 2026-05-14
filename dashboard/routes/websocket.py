"""WebSocket handler and SPA fallback."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from quart import jsonify, send_from_directory, websocket

from core.platform.message import normalize_session_id
from dashboard.routes._helpers import AUTH_COOKIE, cookie_value, parse_int

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
            if not dashboard._session_ok(session_token):
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
        # Track audio clients separately for binary audio streaming
        if not hasattr(dashboard, "_audio_clients"):
            dashboard._audio_clients = set()
        dashboard._audio_clients.add(ws_obj)
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
            dashboard._audio_clients.discard(ws_obj)

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
