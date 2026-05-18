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
from core.config_schema import CONFIG_SCHEMA
from dashboard.routes._helpers import (
    AUTH_COOKIE,
    AUTH_EXEMPT_API_PATHS,
    DASHBOARD_CSP,
    PBKDF2_PREFIX,
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
        self._sync_auth_from_config()
        self.auth_session_token = secrets.token_urlsafe(32)
        self._mcp_push_fingerprint = ""
        self._loop = asyncio.get_running_loop()

        static_dir = str(Path(__file__).parent / "static")
        self.app = Quart("atri-dashboard", static_folder=static_dir, static_url_path="/static")
        self.app.config["MAX_CONTENT_LENGTH"] = DASHBOARD_MAX_CONTENT_LENGTH
        self._ws_clients: set = set()
        self._audio_clients: set = set()
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
        """Refresh auth state from config.

        NOTE: callers that change the stored username/password MUST regenerate
        auth_session_token afterwards — this method intentionally does NOT
        touch the session token so that simple config reloads don't log
        everyone out.
        """
        dashboard_cfg = self.lifecycle.config.get("dashboard", {})
        dashboard_enabled = bool(dashboard_cfg.get("enabled", True))
        self.auth_username = str(dashboard_cfg.get("username", "") or "")
        self.auth_password = str(dashboard_cfg.get("password", "") or "")
        self.auth_password_is_hashed = self.auth_password.startswith(PBKDF2_PREFIX)
        self.auth_setup_required = bool(
            dashboard_enabled and (not self.auth_username or not self.auth_password)
        )
        self.auth_enabled = bool(dashboard_enabled and not self.auth_setup_required)

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
        if lc.process_stage:
            lc.process_stage.update_config(
                model=model,
                api_key=lc.config["api_key"],
                base_url=lc.config.get("base_url"),
                api_format=lc.config.get("api_format", "openai"),
                active_models=lc.config.get("active_models", []),
                providers=lc.config.get("providers", {}),
            )

    def _clear_current_model(self):
        """Clear the selected chat model when no configured model remains."""
        lc = self.lifecycle
        lc.config["model"] = ""
        lc.config["api_key"] = ""
        lc.config["base_url"] = None
        lc.config["api_format"] = "openai"
        self._push_model_config()

    def _push_model_config(self):
        lc = self.lifecycle
        if lc.process_stage:
            lc.process_stage.update_config(
                model=lc.config.get("model", ""),
                api_key=lc.config.get("api_key", ""),
                base_url=lc.config.get("base_url"),
                api_format=lc.config.get("api_format", "openai"),
                active_models=lc.config.get("active_models", []),
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
        return bool(self.auth_enabled and token) and hmac.compare_digest(
            token,
            self.auth_session_token,
        )

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

    def _find_bash_tool(self, session_id: str):
        """Find the BashTool instance for a given session's agent."""
        from core.tools.bash import BashTool

        if not self.lifecycle.process_stage:
            return None
        agent = self.lifecycle.process_stage.get_agent(session_id)
        if not agent:
            return None
        for tool in agent.tools:
            if isinstance(tool, BashTool):
                return tool
        return None

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
            try:
                await ws.send(header)
                await ws.send(pcm_bytes)
            except Exception:
                audio_clients.discard(ws)

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
