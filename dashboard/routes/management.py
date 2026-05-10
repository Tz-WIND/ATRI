"""Management routes: workspace, adapter, sessions, runtime, and file operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from quart import jsonify, request

from core.platform.message import display_session_id, normalize_session_id
from dashboard.routes._helpers import parse_int, resolve_workspace_path

if TYPE_CHECKING:
    from dashboard.server import Dashboard


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    # ── Workspace ──

    @app.route("/api/workspace", methods=["GET"])
    async def get_workspace():
        return jsonify({"workspace": dashboard.lifecycle.config.get("workspace", ".")})

    @app.route("/api/workspace", methods=["POST"])
    async def update_workspace():
        data = await request.get_json()
        if "workspace" in data:
            dashboard.lifecycle.config["workspace"] = data["workspace"]
            dashboard.lifecycle.save_config()
        return jsonify({"ok": True})

    # ── Adapter (OneBot11) ──

    @app.route("/api/adapter", methods=["GET"])
    async def get_adapter():
        ob = dashboard.lifecycle.config.get("onebot11", {})
        return jsonify(
            {
                "enabled": ob.get("enabled", True),
                "ws_reverse_host": ob.get("ws_reverse_host", "0.0.0.0"),  # noqa: S104
                "ws_reverse_port": ob.get("ws_reverse_port", 6199),
                "ws_reverse_token": "***" if ob.get("ws_reverse_token") else "",
                "status": dashboard.lifecycle.onebot11.status.value
                if dashboard.lifecycle.onebot11
                else "disabled",
            }
        )

    @app.route("/api/adapter", methods=["POST"])
    async def update_adapter():
        data = await request.get_json()
        ob = dashboard.lifecycle.config.setdefault("onebot11", {})
        if "enabled" in data:
            ob["enabled"] = data["enabled"]
        if "ws_reverse_host" in data:
            ob["ws_reverse_host"] = data["ws_reverse_host"]
        if "ws_reverse_port" in data:
            ob["ws_reverse_port"] = int(data["ws_reverse_port"])
        if "ws_reverse_token" in data and data["ws_reverse_token"] != "***":  # noqa: S105
            ob["ws_reverse_token"] = data["ws_reverse_token"]
        dashboard.lifecycle.save_config()
        return jsonify({"ok": True, "note": "Restart required for adapter changes to take effect."})

    # ── Sessions ──

    @app.route("/api/sessions")
    async def list_sessions():
        if dashboard.lifecycle.process_stage:
            sessions = dashboard.lifecycle.process_stage.session_store.list_sessions()
            for s in sessions:
                s["id"] = display_session_id(s["id"])
            return jsonify(sessions)
        return jsonify([])

    @app.route("/api/sessions/<path:session_id>")
    async def get_session(session_id: str):
        if dashboard.lifecycle.process_stage:
            store = dashboard.lifecycle.process_stage.session_store
            internal_id = f"webchat:friend:{session_id}" if ":" not in session_id else session_id
            for candidate in (internal_id, session_id):
                result = store.load(candidate)
                if result:
                    messages, model = result
                    runtime = dashboard._runtime_store()
                    runtime_detail = None
                    if runtime is not None:
                        runtime_detail = runtime.thread_detail(candidate) or runtime.thread_detail(
                            normalize_session_id(candidate)
                        )
                    runtime_turns = runtime_detail.get("turns", []) if runtime_detail else []
                    runtime_items = [
                        item
                        for item in (runtime_detail.get("items", []) if runtime_detail else [])
                        if item.get("kind") == "agent_reasoning" and item.get("detail")
                    ]
                    return jsonify(
                        {
                            "messages": messages,
                            "model": model,
                            "runtime_turns": runtime_turns,
                            "runtime_items": runtime_items,
                        }
                    )
        return jsonify({"messages": [], "model": ""})

    @app.route("/api/sessions/<path:session_id>", methods=["DELETE"])
    async def delete_session(session_id: str):
        if dashboard.lifecycle.process_stage:
            internal_id = normalize_session_id(session_id)
            dashboard.lifecycle.process_stage.reset_session(internal_id)
            if session_id != internal_id:
                dashboard.lifecycle.process_stage.reset_session(session_id)
        return jsonify({"ok": True})

    # ── Runtime timeline ──

    @app.route("/api/runtime/threads")
    async def list_runtime_threads():
        store = dashboard._runtime_store()
        if store is None:
            return jsonify([])
        limit = parse_int(request.args.get("limit"), 50)
        include_archived = request.args.get("include_archived", "").lower() in {
            "1",
            "true",
            "yes",
        }
        threads = store.list_threads(limit=limit, include_archived=include_archived)
        for thread in threads:
            thread["display_id"] = display_session_id(thread["id"])
        return jsonify(threads)

    @app.route("/api/runtime/threads/<path:thread_id>")
    async def get_runtime_thread(thread_id: str):
        store = dashboard._runtime_store()
        if store is None:
            return jsonify({"error": "runtime store not available"}), 503
        internal_id = normalize_session_id(thread_id)
        detail = store.thread_detail(internal_id) or store.thread_detail(thread_id)
        if detail is None:
            return jsonify({"error": "thread not found"}), 404
        detail["thread"]["display_id"] = display_session_id(detail["thread"]["id"])
        return jsonify(detail)

    @app.route("/api/runtime/events")
    async def list_runtime_events():
        store = dashboard._runtime_store()
        if store is None:
            return jsonify({"events": [], "latest_seq": 0})
        session_id = (request.args.get("session_id") or "").strip()
        thread_id = normalize_session_id(session_id) if session_id else None
        since_seq = parse_int(request.args.get("since_seq"), 0)
        limit = parse_int(request.args.get("limit"), 1000)
        events = store.events_since(thread_id=thread_id, since_seq=since_seq, limit=limit)
        return jsonify(
            {
                "events": [event.to_wire_payload() for event in events],
                "latest_seq": store.latest_seq(thread_id),
            }
        )

    @app.route("/api/test-ping")
    async def test_ping():
        return jsonify({"pong": True})

    # ── Files (workspace file management) ──

    @app.route("/api/filelist")
    async def list_files():
        rel = request.args.get("path", "")
        try:
            ws, target = resolve_workspace_path(
                dashboard.lifecycle.config.get("workspace", "."),
                rel,
            )
        except PermissionError:
            return jsonify({"error": "path outside workspace"}), 403
        if not target.exists() or not target.is_dir():
            return jsonify({"entries": [], "path": rel})
        entries = []
        try:
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if item.name.startswith("."):
                    continue
                entries.append(
                    {
                        "name": item.name,
                        "type": "dir" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else 0,
                        "path": str(item.relative_to(ws)).replace("\\", "/"),
                    }
                )
        except PermissionError:
            pass
        return jsonify({"entries": entries, "path": rel})

    @app.route("/api/fileread")
    async def read_file():
        rel = request.args.get("path", "")
        try:
            _, target = resolve_workspace_path(
                dashboard.lifecycle.config.get("workspace", "."),
                rel,
            )
        except PermissionError:
            return jsonify({"error": "path outside workspace"}), 403
        if not target.exists() or not target.is_file():
            return jsonify({"error": "file not found"}), 404
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            return jsonify({"content": content, "path": rel, "name": target.name})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/filewrite", methods=["POST"])
    async def write_file():
        data = await request.get_json()
        rel = data.get("path", "")
        content = data.get("content", "")
        try:
            _, target = resolve_workspace_path(
                dashboard.lifecycle.config.get("workspace", "."),
                rel,
            )
        except PermissionError:
            return jsonify({"error": "path outside workspace"}), 403
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
