"""ATRI Dashboard - OpenCode-style vibecoding WebUI.

Serves REST API, WebSocket for real-time updates, and the SPA frontend.
Chat messages go through the WebChat platform adapter and full pipeline.
"""

from __future__ import annotations

import asyncio
import json
import os
import io
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from quart import Quart, Response, jsonify, request, websocket, send_from_directory

from core import logger
from core.platform.message import normalize_session_id, display_session_id

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle


class Dashboard:
    def __init__(self, lifecycle: "Lifecycle", host: str = "0.0.0.0", port: int = 6185):
        self.lifecycle = lifecycle
        self.host = host
        self.port = port

        static_dir = str(Path(__file__).parent / "static")
        self.app = Quart("atri-dashboard", static_folder=static_dir, static_url_path="/static")
        self.app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
        self._ws_clients: set = set()
        self._register_routes()

        from dashboard.music import bp as music_bp, init_music
        init_music(lifecycle)
        self.app.register_blueprint(music_bp)

        if lifecycle.process_stage:
            lifecycle.process_stage.broadcast_fn = self.broadcast

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
        if lc.process_stage:
            lc.process_stage.update_config(
                model=model,
                api_key=lc.config["api_key"],
                base_url=lc.config.get("base_url"),
            )

    def _reload_skills_prompt(self):
        """Hot-reload the skills prompt on all live agents after a config change."""
        ps = self.lifecycle.process_stage
        if ps is not None:
            ps.reload_skills()

    def _register_routes(self):
        app = self.app

        @app.route("/")
        async def index():
            response = await send_from_directory(app.static_folder, "index.html")
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.route("/api/ping")
        async def api_ping():
            return jsonify({"pong": True, "routes": len(list(app.url_map.iter_rules()))})

        # ── Status ──
        @app.route("/api/status")
        async def api_status():
            lc = self.lifecycle
            return jsonify({
                "status": "running",
                "uptime": int(time.time() - lc.start_time) if lc.start_time else 0,
                "model": lc.config.get("model", ""),
                "active_models": lc.config.get("active_models", []),
                "workspace": lc.config.get("workspace", ""),
                "api_format": lc.config.get("api_format", "openai"),
                "onebot11_status": lc.onebot11.status.value if lc.onebot11 else "disabled",
                "webchat_status": lc.webchat.status.value if lc.webchat else "disabled",
                "session_count": lc.process_stage.agent_count if lc.process_stage else 0,
                "mcp_server_count": len(lc.config.get("mcp_servers", {})),
                "skill_count": len(lc.config.get("skills", {})),
            })

        # ── Model Settings ──
        @app.route("/api/settings", methods=["GET"])
        async def get_settings():
            c = self.lifecycle.config
            return jsonify({
                "model": c.get("model", ""),
                "api_key": "***" if c.get("api_key") else "",
                "base_url": c.get("base_url") or "",
                "api_format": c.get("api_format", "openai"),
                "max_tokens": c.get("max_tokens", 4096),
                "temperature": c.get("temperature", 0.0),
                "max_context_tokens": c.get("max_context_tokens", 128000),
                "max_rounds": c.get("max_rounds", 50),
                "wake_words": c.get("wake_words", []),
                "extra_instructions": c.get("extra_instructions", ""),
                "persona": c.get("persona", ""),
                "providers": c.get("providers", {}),
                "tavily_api_key": "***" if c.get("tavily_api_key") else "",
            })

        @app.route("/api/settings", methods=["POST"])
        async def update_settings():
            data = await request.get_json()
            lc = self.lifecycle
            for key in ["model", "base_url", "api_format", "extra_instructions", "persona"]:
                if key in data:
                    lc.config[key] = data[key]
            if "api_key" in data and data["api_key"] != "***":
                lc.config["api_key"] = data["api_key"]
            for key in ["max_tokens", "max_context_tokens", "max_rounds"]:
                if key in data:
                    lc.config[key] = int(data[key])
            if "temperature" in data:
                lc.config["temperature"] = float(data["temperature"])
            if "wake_words" in data:
                lc.config["wake_words"] = data["wake_words"]
            if "tavily_api_key" in data and data["tavily_api_key"] != "***":
                lc.config["tavily_api_key"] = data["tavily_api_key"]
            if lc.process_stage:
                lc.process_stage.update_config(**{
                    k: v for k, v in data.items()
                    if k in ["model", "api_key", "base_url", "extra_instructions", "persona", "tavily_api_key"]
                })
            lc.save_config()
            return jsonify({"ok": True})

        # ── Model Providers ──
        @app.route("/api/provider/list", methods=["GET"])
        async def list_providers():
            providers = self.lifecycle.config.get("providers", {})
            result = {}
            for name, cfg in providers.items():
                result[name] = {**cfg, "api_key": "***" if cfg.get("api_key") else ""}
            return jsonify(result)

        @app.route("/api/provider/save", methods=["POST"])
        async def save_provider():
            data = await request.get_json()
            name = data.get("name", "").strip()
            if not name:
                return jsonify({"error": "name required"}), 400
            providers = self.lifecycle.config.setdefault("providers", {})
            existing = providers.get(name, {})
            providers[name] = {
                "base_url": data.get("base_url", ""),
                "api_key": data["api_key"] if data.get("api_key") and data["api_key"] != "***" else existing.get("api_key", ""),
                "api_format": data.get("api_format", "openai"),
                "models": existing.get("models", []),
            }
            self.lifecycle.save_config()
            return jsonify({"ok": True})

        @app.route("/api/provider/delete", methods=["POST"])
        async def delete_provider():
            data = await request.get_json()
            name = data.get("name", "")
            providers = self.lifecycle.config.get("providers", {})
            providers.pop(name, None)
            self.lifecycle.save_config()
            return jsonify({"ok": True})

        @app.route("/api/provider/models", methods=["POST"])
        async def get_provider_models():
            """Fetch available models from a provider's API."""
            data = await request.get_json()
            name = data.get("name", "")
            providers = self.lifecycle.config.get("providers", {})
            if name not in providers:
                return jsonify({"error": "provider not found"}), 404
            cfg = providers[name]
            api_key = cfg.get("api_key", "")
            base_url = cfg.get("base_url", "")
            try:
                import httpx as _httpx
                url = (base_url.rstrip("/") + "/models") if base_url else "https://api.openai.com/v1/models"
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                async with _httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    body = resp.json()
                models = []
                for m in body.get("data", []):
                    models.append(m.get("id", ""))
                models.sort()
                providers[name]["models"] = models
                self.lifecycle.save_config()
                return jsonify({"models": models})
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @app.route("/api/provider/activate", methods=["POST"])
        async def activate_model():
            """Add a model to the active models list and optionally select it."""
            data = await request.get_json()
            provider_name = data.get("provider", "")
            model = data.get("model", "")
            if not model:
                return jsonify({"error": "model required"}), 400
            lc = self.lifecycle
            active_models = lc.config.setdefault("active_models", [])
            entry = {"model": model, "provider": provider_name}
            if not any(m["model"] == model and m["provider"] == provider_name for m in active_models):
                active_models.append(entry)
            self._apply_model(provider_name, model)
            lc.save_config()
            return jsonify({"ok": True})

        @app.route("/api/provider/deactivate", methods=["POST"])
        async def deactivate_model():
            """Remove a model from the active models list."""
            data = await request.get_json()
            provider_name = data.get("provider", "")
            model = data.get("model", "")
            lc = self.lifecycle
            active_models = lc.config.setdefault("active_models", [])
            lc.config["active_models"] = [
                m for m in active_models
                if not (m["model"] == model and m["provider"] == provider_name)
            ]
            lc.save_config()
            return jsonify({"ok": True})

        @app.route("/api/provider/select", methods=["POST"])
        async def select_model():
            """Switch to a specific active model for chatting."""
            data = await request.get_json()
            provider_name = data.get("provider", "")
            model = data.get("model", "")
            if not model:
                return jsonify({"error": "model required"}), 400
            self._apply_model(provider_name, model)
            self.lifecycle.save_config()
            return jsonify({"ok": True})

        # ── Workspace ──
        @app.route("/api/workspace", methods=["GET"])
        async def get_workspace():
            return jsonify({"workspace": self.lifecycle.config.get("workspace", ".")})

        @app.route("/api/workspace", methods=["POST"])
        async def update_workspace():
            data = await request.get_json()
            if "workspace" in data:
                self.lifecycle.config["workspace"] = data["workspace"]
                self.lifecycle.save_config()
            return jsonify({"ok": True})

        # ── Adapter (OneBot11) ──
        @app.route("/api/adapter", methods=["GET"])
        async def get_adapter():
            ob = self.lifecycle.config.get("onebot11", {})
            return jsonify({
                "enabled": ob.get("enabled", True),
                "ws_reverse_host": ob.get("ws_reverse_host", "0.0.0.0"),
                "ws_reverse_port": ob.get("ws_reverse_port", 6199),
                "ws_reverse_token": "***" if ob.get("ws_reverse_token") else "",
                "status": self.lifecycle.onebot11.status.value if self.lifecycle.onebot11 else "disabled",
            })

        @app.route("/api/adapter", methods=["POST"])
        async def update_adapter():
            data = await request.get_json()
            ob = self.lifecycle.config.setdefault("onebot11", {})
            if "enabled" in data:
                ob["enabled"] = data["enabled"]
            if "ws_reverse_host" in data:
                ob["ws_reverse_host"] = data["ws_reverse_host"]
            if "ws_reverse_port" in data:
                ob["ws_reverse_port"] = int(data["ws_reverse_port"])
            if "ws_reverse_token" in data and data["ws_reverse_token"] != "***":
                ob["ws_reverse_token"] = data["ws_reverse_token"]
            self.lifecycle.save_config()
            return jsonify({"ok": True, "note": "Restart required for adapter changes to take effect."})

        # ── MCP Servers ──
        @app.route("/api/mcp/servers", methods=["GET"])
        async def list_mcp():
            servers = self.lifecycle.config.get("mcp_servers", {})
            result = []
            for name, cfg in servers.items():
                result.append({"name": name, "active": cfg.get("active", True), **{k: v for k, v in cfg.items() if k != "active"}})
            return jsonify(result)

        @app.route("/api/mcp/servers", methods=["POST"])
        async def add_mcp():
            data = await request.get_json()
            name = data.pop("name", "")
            if not name:
                return jsonify({"error": "name required"}), 400
            servers = self.lifecycle.config.setdefault("mcp_servers", {})
            servers[name] = data
            self.lifecycle.save_config()
            return jsonify({"ok": True})

        @app.route("/api/mcp/servers/<name>", methods=["PUT"])
        async def update_mcp(name: str):
            data = await request.get_json()
            servers = self.lifecycle.config.setdefault("mcp_servers", {})
            if name not in servers:
                return jsonify({"error": "not found"}), 404
            servers[name].update(data)
            self.lifecycle.save_config()
            return jsonify({"ok": True})

        @app.route("/api/mcp/servers/<name>", methods=["DELETE"])
        async def delete_mcp(name: str):
            servers = self.lifecycle.config.get("mcp_servers", {})
            servers.pop(name, None)
            self.lifecycle.save_config()
            return jsonify({"ok": True})

        # ── Skills ──
        @app.route("/api/skills", methods=["GET"])
        async def list_skills():
            sm = self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            if sm is None:
                return jsonify([])
            skills = sm.list_skills(active_only=False)
            return jsonify([
                {
                    "name": s.name,
                    "description": s.description,
                    "path": s.path,
                    "active": s.active,
                }
                for s in skills
            ])

        @app.route("/api/skills/<name>")
        async def get_skill(name: str):
            sm = self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            skills = sm.list_skills(active_only=False)
            for s in skills:
                if s.name == name:
                    skill_path = Path(s.path)
                    content = ""
                    if skill_path.exists():
                        content = skill_path.read_text(encoding="utf-8", errors="replace")
                    return jsonify({
                        "name": s.name,
                        "description": s.description,
                        "path": s.path,
                        "active": s.active,
                        "content": content,
                    })
            return jsonify({"error": "skill not found"}), 404

        @app.route("/api/skills/<name>", methods=["PUT"])
        async def update_skill(name: str):
            data = await request.get_json()
            sm = self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            if "active" in data:
                sm.set_skill_active(name, bool(data["active"]))
            self.lifecycle.save_config()
            self._reload_skills_prompt()
            return jsonify({"ok": True})

        @app.route("/api/skills/<name>", methods=["DELETE"])
        async def delete_skill(name: str):
            sm = self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            sm.delete_skill(name)
            self.lifecycle.save_config()
            self._reload_skills_prompt()
            return jsonify({"ok": True})

        @app.route("/api/skills/upload", methods=["POST"])
        async def upload_skill():
            sm = self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            files = await request.files
            uploaded = files.get("file")
            if uploaded is None:
                return jsonify({"error": "no file uploaded"}), 400
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                    tmp.write(uploaded.read())
                    tmp_path = tmp.name
                names = sm.install_skill_from_zip(tmp_path, overwrite=True)
                self.lifecycle.save_config()
                self._reload_skills_prompt()
                return jsonify({"ok": True, "installed": names})
            except Exception as e:
                return jsonify({"error": str(e)}), 400
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        @app.route("/api/skills/<name>/download")
        async def download_skill(name: str):
            sm = self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            skill_dir = Path(sm.skills_root) / name
            if not skill_dir.exists() or not skill_dir.is_dir():
                return jsonify({"error": "skill not found"}), 404
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in skill_dir.rglob("*"):
                    if file_path.is_file():
                        arcname = str(file_path.relative_to(skill_dir.parent))
                        zf.write(file_path, arcname=arcname)
            buf.seek(0)
            return Response(
                buf.getvalue(),
                mimetype="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
            )

        # ── Sessions ──
        @app.route("/api/sessions")
        async def list_sessions():
            if self.lifecycle.process_stage:
                sessions = self.lifecycle.process_stage.session_store.list_sessions()
                for s in sessions:
                    s["id"] = display_session_id(s["id"])
                return jsonify(sessions)
            return jsonify([])

        @app.route("/api/sessions/<path:session_id>")
        async def get_session(session_id: str):
            if self.lifecycle.process_stage:
                store = self.lifecycle.process_stage.session_store
                internal_id = f"webchat:friend:{session_id}" if ":" not in session_id else session_id
                for candidate in (internal_id, session_id):
                    result = store.load(candidate)
                    if result:
                        messages, model = result
                        return jsonify({"messages": messages, "model": model})
            return jsonify({"messages": [], "model": ""})

        @app.route("/api/sessions/<path:session_id>", methods=["DELETE"])
        async def delete_session(session_id: str):
            if self.lifecycle.process_stage:
                internal_id = normalize_session_id(session_id)
                self.lifecycle.process_stage.reset_session(internal_id)
                # Also try raw ID in case internal key format differs
                if session_id != internal_id:
                    self.lifecycle.process_stage.reset_session(session_id)
            return jsonify({"ok": True})

        @app.route("/api/test-ping")
        async def test_ping():
            return jsonify({"pong": True})

        # ── Files (workspace file management) ──
        @app.route("/api/filelist")
        async def list_files():
            rel = request.args.get("path", "")
            ws = Path(self.lifecycle.config.get("workspace", ".")).resolve()
            target = (ws / rel).resolve()
            if not str(target).startswith(str(ws)):
                return jsonify({"error": "path outside workspace"}), 403
            if not target.exists() or not target.is_dir():
                return jsonify({"entries": [], "path": rel})
            entries = []
            try:
                for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if item.name.startswith("."):
                        continue
                    entries.append({
                        "name": item.name,
                        "type": "dir" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else 0,
                        "path": str(item.relative_to(ws)).replace("\\", "/"),
                    })
            except PermissionError:
                pass
            return jsonify({"entries": entries, "path": rel})

        @app.route("/api/fileread")
        async def read_file():
            rel = request.args.get("path", "")
            ws = Path(self.lifecycle.config.get("workspace", ".")).resolve()
            target = (ws / rel).resolve()
            if not str(target).startswith(str(ws)):
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
            ws = Path(self.lifecycle.config.get("workspace", ".")).resolve()
            target = (ws / rel).resolve()
            if not str(target).startswith(str(ws)):
                return jsonify({"error": "path outside workspace"}), 403
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return jsonify({"ok": True})
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        # ── Chat (via WebChat adapter -> pipeline) ──
        @app.route("/api/chat", methods=["POST"])
        async def chat():
            data = await request.get_json()
            message = data.get("message", "").strip()
            session_id = data.get("session_id", "webchat_default")
            if not message:
                return jsonify({"error": "empty message"}), 400
            webchat = self.lifecycle.webchat
            if not webchat:
                return jsonify({"error": "webchat adapter not available"}), 503

            event, future = webchat.create_event(message, session_id)
            await self.broadcast({"type": "thinking", "session_id": session_id})

            try:
                result = await asyncio.wait_for(future, timeout=300)
                response_text = result.get("text", "")
                token_usage = {}
                if self.lifecycle.process_stage:
                    agent = self.lifecycle.process_stage.get_agent(event.unified_msg_origin)
                    if agent:
                        token_usage = {
                            "prompt": agent.llm.total_prompt_tokens,
                            "completion": agent.llm.total_completion_tokens,
                            "cost": agent.llm.estimated_cost,
                        }
                return jsonify({
                    "response": response_text,
                    "session_id": display_session_id(event.unified_msg_origin),
                    "tool_events": event._extras.get("tool_events", []),
                    "token_usage": token_usage,
                })
            except asyncio.TimeoutError:
                return jsonify({"error": "Agent timed out (300s)"}), 504
            except Exception as e:
                logger.exception(f"WebUI chat error: {e}")
                return jsonify({"error": str(e)}), 500

        # ── Cancel active chat ──
        @app.route("/api/chat/cancel", methods=["POST"])
        async def cancel_chat():
            """Cancel the currently running agent operation for a session."""
            data = await request.get_json(silent=True) or {}
            session_id = data.get("session_id", "")
            cancelled = self.lifecycle.cancel_operation(
                session_id=session_id if session_id else None
            )
            return jsonify({"ok": cancelled})

        # ── Tools info ──
        @app.route("/api/tools")
        async def list_tools():
            from core.tools import create_tools
            ws = self.lifecycle.config.get("workspace", ".")
            tools = create_tools(ws)
            return jsonify([{"name": t.name, "description": t.description} for t in tools])

        # ── Approve dangerous command ──
        @app.route("/api/approve-command", methods=["POST"])
        async def approve_command():
            data = await request.get_json()
            session_id = normalize_session_id(data.get("session_id", ""))
            agent = self.lifecycle.process_stage.get_agent(session_id) if self.lifecycle.process_stage else None
            if agent:
                from core.tools.bash import BashTool
                for tool in agent.tools:
                    if isinstance(tool, BashTool) and tool._pending_approval:
                        result = tool.approve_pending()
                        return jsonify({"ok": True, "result": result})
            return jsonify({"error": "no pending command"}), 404

        # ── WebSocket ──
        @app.websocket("/ws")
        async def ws_handler():
            ws_obj = websocket._get_current_object()
            self._ws_clients.add(ws_obj)
            try:
                while True:
                    data = await websocket.receive()
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))
            except asyncio.CancelledError:
                pass
            finally:
                self._ws_clients.discard(ws_obj)

        # ── SPA fallback (via 404 handler so it never shadows API routes) ──
        @app.errorhandler(404)
        async def spa_fallback(e):
            """Serve index.html for all non-API routes (Vue SPA)."""
            from quart import request as _req
            path = _req.path
            if path.startswith(("/api/", "/ws", "/static/")):
                return jsonify({"error": "not found"}), 404
            response = await send_from_directory(app.static_folder, "index.html")
            response.headers["Cache-Control"] = "no-store"
            return response

    async def broadcast(self, data: dict):
        msg = json.dumps(data)
        for ws in list(self._ws_clients):
            try:
                await ws.send(msg)
            except Exception:
                self._ws_clients.discard(ws)

    async def run(self):
        from hypercorn.asyncio import serve
        from hypercorn.config import Config
        config = Config()
        config.bind = [f"{self.host}:{self.port}"]
        config.accesslog = None
        logger.info(f"\n  ATRI Dashboard ready\n  -> http://localhost:{self.port}\n")
        self.shutdown_event = asyncio.Event()
        await serve(self.app, config, shutdown_trigger=self.shutdown_event.wait)

    async def stop(self):
        if hasattr(self, "shutdown_event"):
            self.shutdown_event.set()
