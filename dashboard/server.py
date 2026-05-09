"""ATRI Dashboard - OpenCode-style vibecoding WebUI.

Serves REST API, WebSocket for real-time updates, and the SPA frontend.
Chat messages go through the WebChat platform adapter and full pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import secrets
import tempfile
import time
import zipfile
from collections import defaultdict
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from quart import Quart, Response, jsonify, request, send_from_directory, websocket

from core import logger
from core.config_schema import CONFIG_SCHEMA
from core.platform.message import display_session_id, normalize_session_id

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle


_PROCESS_STAGE_SETTING_KEYS = {
    "model",
    "api_key",
    "base_url",
    "api_format",
    "active_models",
    "providers",
    "max_tokens",
    "max_context_tokens",
    "max_rounds",
    "temperature",
    "extra_instructions",
    "persona",
    "agent_mode",
    "skills_root",
    "skill_search_roots",
    "tavily_api_key",
    "mcp_servers",
}

_AUTH_COOKIE = "atri_dashboard_session"

# Rate limiting: track failed login/setup attempts per IP
_RATE_LIMIT_WINDOW = 300  # 5 minutes
_RATE_LIMIT_MAX_FAILURES = 10
_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)

_PBKDF2_PREFIX = "pbkdf2:"
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_HASH = "sha256"
_PBKDF2_SALT_BYTES = 16


def _check_rate_limit(ip: str) -> bool:
    """Return True if the IP is rate-limited."""
    now = time.time()
    bucket = _rate_limit_buckets[ip]
    # Prune old entries
    bucket[:] = [t for t in bucket if now - t < _RATE_LIMIT_WINDOW]
    return len(bucket) >= _RATE_LIMIT_MAX_FAILURES


def _record_failure(ip: str) -> None:
    _rate_limit_buckets[ip].append(time.time())


def _hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256, returning a storable string."""
    salt = os.urandom(_PBKDF2_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_HASH, password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_PREFIX}{salt.hex()}${dk.hex()}"


def _verify_password(stored: str, candidate: str) -> bool:
    """Verify a password against a hashed or legacy plaintext storage string."""
    if not stored or not candidate:
        return False
    if stored.startswith(_PBKDF2_PREFIX):
        try:
            prefixed_salt, dk_hex = stored.split("$", 1)
        except ValueError:
            return False
        salt_hex = prefixed_salt[len(_PBKDF2_PREFIX) :]
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        dk = hashlib.pbkdf2_hmac(_PBKDF2_HASH, candidate.encode(), salt, _PBKDF2_ITERATIONS)
        return hmac.compare_digest(dk, expected)
    # Legacy plaintext comparison — upgrade on next successful login
    return hmac.compare_digest(stored, candidate)


_AUTH_EXEMPT_API_PATHS = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/logout",
    "/api/config/schema",
}


def _mask_providers(providers: dict) -> dict:
    result = {}
    for name, cfg in (providers or {}).items():
        if not isinstance(cfg, dict):
            continue
        result[name] = {**cfg, "api_key": "***" if cfg.get("api_key") else ""}
    return result


def _resolve_workspace_path(workspace: str, rel_path: str) -> tuple[Path, Path]:
    ws = Path(workspace or ".").resolve()
    target = (ws / (rel_path or "")).resolve()
    try:
        target.relative_to(ws)
    except ValueError as e:
        raise PermissionError("path outside workspace") from e
    return ws, target


def _cookie_value(cookie_header: str, name: str) -> str:
    if not cookie_header:
        return ""
    try:
        cookies = SimpleCookie(cookie_header)
    except Exception:
        return ""
    morsel = cookies.get(name)
    return morsel.value if morsel else ""


def _model_url_candidates(base_url: str) -> list[str]:
    """Build likely model-list URLs from an OpenAI-compatible or Anthropic base URL."""
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return ["https://api.openai.com/v1/models"]
    if "://" not in cleaned:
        cleaned = "https://" + cleaned

    parsed = urlsplit(cleaned)
    path = parsed.path.rstrip("/")
    lower_path = path.lower()

    if lower_path.endswith("/models"):
        return [cleaned]

    if lower_path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
        cleaned = urlunsplit(parsed._replace(path=path))
    elif lower_path.endswith("/messages"):
        path = path[: -len("/messages")]
        cleaned = urlunsplit(parsed._replace(path=path))

    candidates = [cleaned.rstrip("/") + "/models"]
    if "/v1" not in lower_path:
        candidates.append(cleaned.rstrip("/") + "/v1/models")
    return list(dict.fromkeys(candidates))


def _headers_for_model_fetch(api_format: str, api_key: str) -> dict:
    if api_format == "anthropic":
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        return headers
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _model_fetch_candidates(
    base_url: str,
    api_format: str,
    api_key: str,
) -> list[tuple[str, dict, str]]:
    """Build model-list attempts as (url, headers, pagination_format).

    Some providers expose Anthropic-compatible Messages endpoints but keep model
    discovery on their OpenAI-compatible root endpoint. DeepSeek is one such
    provider: chat uses /anthropic, while model discovery is /models.
    """
    candidates: list[tuple[str, dict, str]] = []

    def add(url: str, headers: dict, fetch_format: str):
        key = (url, fetch_format)
        if not any((u, f) == key for u, _, f in candidates):
            candidates.append((url, headers, fetch_format))

    for url in _model_url_candidates(base_url):
        add(url, _headers_for_model_fetch(api_format, api_key), api_format)

    if api_format == "anthropic":
        cleaned = (base_url or "").strip().rstrip("/")
        if cleaned and "://" not in cleaned:
            cleaned = "https://" + cleaned
        parsed = urlsplit(cleaned)
        parts = [part for part in parsed.path.split("/") if part]
        if "anthropic" in [part.lower() for part in parts]:
            stripped_parts = [part for part in parts if part.lower() != "anthropic"]
            stripped_path = "/" + "/".join(stripped_parts) if stripped_parts else ""
            stripped = urlunsplit(parsed._replace(path=stripped_path)).rstrip("/")
            openai_headers = _headers_for_model_fetch("openai", api_key)
            for url in _model_url_candidates(stripped):
                add(url, openai_headers, "openai")

    return candidates


def _extract_model_ids(body) -> list[str]:
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        items = body.get("data") or body.get("models") or body.get("result") or []
    else:
        items = []

    models: list[str] = []
    for item in items:
        model_id = ""
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            for key in ("id", "name", "model"):
                value = item.get(key)
                if isinstance(value, str):
                    model_id = value
                    break
        if model_id:
            models.append(model_id)
    return sorted(set(models))


async def _fetch_model_ids(client, url: str, headers: dict, api_format: str) -> list[str]:
    models: list[str] = []
    params: dict | None = {"limit": 100} if api_format == "anthropic" else None

    for _ in range(20):
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        body = resp.json()
        models.extend(_extract_model_ids(body))

        if api_format != "anthropic" or not isinstance(body, dict) or not body.get("has_more"):
            break
        after_id = body.get("last_id")
        if not isinstance(after_id, str) or not after_id.strip():
            break
        params = {"limit": 100, "after_id": after_id}

    return sorted(set(models))


class Dashboard:
    def __init__(self, lifecycle: Lifecycle, host: str = "127.0.0.1", port: int = 6185):
        self.lifecycle = lifecycle
        self.host = host
        self.port = port
        self._sync_auth_from_config()
        self.auth_session_token = secrets.token_urlsafe(32)
        self._mcp_push_fingerprint = ""

        static_dir = str(Path(__file__).parent / "static")
        self.app = Quart("atri-dashboard", static_folder=static_dir, static_url_path="/static")
        self.app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
        self._ws_clients: set = set()
        self._register_routes()

        from dashboard.music import bp as music_bp
        from dashboard.music import init_music

        init_music(lifecycle)
        self.app.register_blueprint(music_bp)

        if lifecycle.process_stage:
            lifecycle.process_stage.broadcast_fn = self.broadcast

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
        self.auth_password_is_hashed = self.auth_password.startswith(_PBKDF2_PREFIX)
        self.auth_setup_required = bool(
            dashboard_enabled and (not self.auth_username or not self.auth_password)
        )
        self.auth_enabled = bool(dashboard_enabled and not self.auth_setup_required)

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
        return request.headers.get("X-ATRI-Session", "") or request.cookies.get(_AUTH_COOKIE, "")

    def _session_ok(self, token: str) -> bool:
        return bool(self.auth_enabled and token) and hmac.compare_digest(
            token,
            self.auth_session_token,
        )

    def _credentials_ok(self, username: str, password: str) -> bool:
        return (
            self.auth_enabled
            and hmac.compare_digest(username, self.auth_username)
            and _verify_password(self.auth_password, password)
        )

    def _request_authenticated(self) -> bool:
        return self._session_ok(self._provided_session_token())

    # ── Route registration ──

    def _register_routes(self):
        """Register all API routes — delegates to sub-methods by area."""
        self._register_core_handlers()
        self._register_auth_routes()
        self._register_model_routes()
        self._register_management_routes()
        self._register_chat_routes()
        self._register_ws_and_fallback()

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
            if path in _AUTH_EXEMPT_API_PATHS:
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
                (
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:"
                ),
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

    def _register_auth_routes(self):
        app = self.app

        @app.route("/api/auth/status")
        async def auth_status():
            authenticated = (
                False
                if self.auth_setup_required
                else not self.auth_enabled or self._request_authenticated()
            )
            response: dict[str, Any] = {
                "auth_required": self.auth_enabled,
                "setup_required": self.auth_setup_required,
                "authenticated": authenticated,
            }
            if authenticated or self.auth_setup_required:
                response["username"] = self.auth_username
            return jsonify(response)

        @app.route("/api/auth/login", methods=["POST"])
        async def auth_login():
            if self.auth_setup_required:
                return jsonify({"error": "setup required", "setup_required": True}), 428
            client_ip = request.remote_addr or "unknown"
            if _check_rate_limit(client_ip):
                return jsonify({"error": "too many attempts, try again later"}), 429
            data = await request.get_json(silent=True) or {}
            username = str(data.get("username", "") or "")
            password = str(data.get("password", "") or "")
            if not self._credentials_ok(username, password):
                _record_failure(client_ip)
                return jsonify({"error": "invalid username or password"}), 401
            if not self.auth_password_is_hashed:
                dashboard_cfg = self.lifecycle.config.setdefault("dashboard", {})
                dashboard_cfg["password"] = _hash_password(password)
                self.lifecycle.save_config()
                self._sync_auth_from_config()
            resp = jsonify({"ok": True})
            resp.set_cookie(
                _AUTH_COOKIE,
                self.auth_session_token,
                httponly=True,
                samesite="Strict",
            )
            return resp

        @app.route("/api/auth/setup", methods=["POST"])
        async def auth_setup():
            if not self.auth_setup_required:
                return jsonify({"error": "setup is not required"}), 409
            client_ip = request.remote_addr or "unknown"
            if _check_rate_limit(client_ip):
                return jsonify({"error": "too many attempts, try again later"}), 429
            data = await request.get_json(silent=True) or {}
            username = str(data.get("username", "") or "").strip()
            password = str(data.get("password", "") or "")
            if not username:
                return jsonify({"error": "username required"}), 400
            if not password:
                return jsonify({"error": "password required"}), 400

            dashboard_cfg = self.lifecycle.config.setdefault("dashboard", {})
            dashboard_cfg["username"] = username
            dashboard_cfg["password"] = _hash_password(password)
            self.lifecycle.save_config()
            self._sync_auth_from_config()
            self.auth_session_token = secrets.token_urlsafe(32)

            resp = jsonify({"ok": True})
            resp.set_cookie(
                _AUTH_COOKIE,
                self.auth_session_token,
                httponly=True,
                samesite="Strict",
            )
            return resp

        @app.route("/api/auth/logout", methods=["POST"])
        async def auth_logout():
            resp = jsonify({"ok": True})
            resp.delete_cookie(_AUTH_COOKIE)
            return resp

    def _register_model_routes(self):
        app = self.app

        # ── Status ──
        @app.route("/api/status")
        async def api_status():
            lc = self.lifecycle
            return jsonify(
                {
                    "status": "running",
                    "uptime": int(time.time() - lc.start_time) if lc.start_time else 0,
                    "model": lc.config.get("model", ""),
                    "active_models": lc.config.get("active_models", []),
                    "workspace": lc.config.get("workspace", ""),
                    "api_format": lc.config.get("api_format", "openai"),
                    "agent_mode": (
                        lc.process_stage.agent_mode
                        if lc.process_stage
                        else lc.config.get("agent_mode", "agent")
                    ),
                    "onebot11_status": lc.onebot11.status.value if lc.onebot11 else "disabled",
                    "webchat_status": lc.webchat.status.value if lc.webchat else "disabled",
                    "session_count": lc.process_stage.agent_count if lc.process_stage else 0,
                    "mcp_server_count": len(lc.config.get("mcp_servers", {})),
                    "skill_count": len(lc.config.get("skills", {})),
                }
            )

        # ── Model Settings ──
        @app.route("/api/settings", methods=["GET"])
        async def get_settings():
            c = self.lifecycle.config
            return jsonify(
                {
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
                    "agent_mode": c.get("agent_mode", "agent"),
                    "skills_root": c.get("skills_root", "skills"),
                    "skill_search_roots": c.get("skill_search_roots", []),
                    "providers": _mask_providers(c.get("providers", {})),
                    "tavily_api_key": "***" if c.get("tavily_api_key") else "",
                }
            )

        @app.route("/api/settings", methods=["POST"])
        async def update_settings():
            data = await request.get_json()
            lc = self.lifecycle
            if "agent_mode" in data:
                from core.agent.mode import normalize_agent_mode

                try:
                    data["agent_mode"] = normalize_agent_mode(data["agent_mode"])
                except ValueError as e:
                    return jsonify({"error": str(e)}), 400
            for key in [
                "model",
                "base_url",
                "api_format",
                "extra_instructions",
                "persona",
                "agent_mode",
            ]:
                if key in data:
                    lc.config[key] = data[key]
            if "skills_root" in data:
                lc.config["skills_root"] = data["skills_root"]
            if "skill_search_roots" in data:
                lc.config["skill_search_roots"] = data["skill_search_roots"]
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
                lc.process_stage.update_config(
                    **{k: v for k, v in data.items() if k in _PROCESS_STAGE_SETTING_KEYS}
                )
            lc.save_config()
            return jsonify({"ok": True})

        # ── Model Providers ──
        @app.route("/api/provider/list", methods=["GET"])
        async def list_providers():
            return jsonify(_mask_providers(self.lifecycle.config.get("providers", {})))

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
                "api_key": data["api_key"]
                if data.get("api_key") and data["api_key"] != "***"
                else existing.get("api_key", ""),
                "api_format": data.get("api_format", "openai"),
                "models": existing.get("models", []),
            }
            if self.lifecycle.process_stage:
                self.lifecycle.process_stage.update_config(
                    providers=providers,
                    active_models=self.lifecycle.config.get("active_models", []),
                )
            self.lifecycle.save_config()
            return jsonify({"ok": True})

        @app.route("/api/provider/delete", methods=["POST"])
        async def delete_provider():
            data = await request.get_json()
            name = data.get("name", "")
            lc = self.lifecycle
            providers = lc.config.setdefault("providers", {})
            removed_provider = providers.pop(name, None)
            active_models = lc.config.setdefault("active_models", [])
            removed_entries = [
                m for m in active_models if isinstance(m, dict) and m.get("provider", "") == name
            ]
            lc.config["active_models"] = [
                m
                for m in active_models
                if not (isinstance(m, dict) and m.get("provider", "") == name)
            ]
            current_model = lc.config.get("model", "")
            current_was_removed = any(m.get("model", "") == current_model for m in removed_entries)
            current_still_active = any(
                self._active_model_entry_available(m) and m.get("model", "") == current_model
                for m in lc.config.get("active_models", [])
            )
            if current_was_removed and (
                not current_still_active or self._current_uses_provider_config(removed_provider)
            ):
                self._select_first_active_model_or_clear()
            else:
                self._push_model_config()
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
            if data.get("api_key") and data["api_key"] != "***":
                api_key = data["api_key"]
            base_url = data.get("base_url", cfg.get("base_url", ""))
            api_format = data.get("api_format", cfg.get("api_format", "openai"))
            try:
                import httpx as _httpx

                last_error = None
                default_base_url = (
                    "https://api.anthropic.com/v1" if api_format == "anthropic" else ""
                )
                effective_base_url = base_url or default_base_url
                async with _httpx.AsyncClient(timeout=15) as client:
                    for url, headers, fetch_format in _model_fetch_candidates(
                        effective_base_url,
                        api_format,
                        api_key,
                    ):
                        try:
                            models = await _fetch_model_ids(
                                client,
                                url,
                                headers,
                                fetch_format,
                            )
                            break
                        except Exception as e:
                            last_error = e
                    else:
                        raise last_error or RuntimeError("failed to fetch models")

                providers[name]["models"] = models
                providers[name]["base_url"] = effective_base_url
                providers[name]["api_format"] = api_format
                if data.get("api_key") and data["api_key"] != "***":
                    providers[name]["api_key"] = api_key
                if self.lifecycle.process_stage:
                    self.lifecycle.process_stage.update_config(
                        providers=providers,
                        active_models=self.lifecycle.config.get("active_models", []),
                    )
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
            if not any(
                m["model"] == model and m["provider"] == provider_name for m in active_models
            ):
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
            removed_entries = [
                m
                for m in active_models
                if (
                    isinstance(m, dict)
                    and m.get("model", "") == model
                    and m.get("provider", "") == provider_name
                )
            ]
            lc.config["active_models"] = [
                m
                for m in active_models
                if not (
                    isinstance(m, dict)
                    and m.get("model", "") == model
                    and m.get("provider", "") == provider_name
                )
            ]
            current_model = lc.config.get("model", "")
            current_still_active = any(
                self._active_model_entry_available(m) and m.get("model", "") == current_model
                for m in lc.config.get("active_models", [])
            )
            provider_cfg = lc.config.get("providers", {}).get(provider_name)
            if (
                removed_entries
                and current_model == model
                and (not current_still_active or self._current_uses_provider_config(provider_cfg))
            ):
                self._select_first_active_model_or_clear()
            else:
                self._push_model_config()
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

    def _register_management_routes(self):
        app = self.app

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
            return jsonify(
                {
                    "enabled": ob.get("enabled", True),
                    "ws_reverse_host": ob.get("ws_reverse_host", "0.0.0.0"),  # noqa: S104
                    "ws_reverse_port": ob.get("ws_reverse_port", 6199),
                    "ws_reverse_token": "***" if ob.get("ws_reverse_token") else "",
                    "status": self.lifecycle.onebot11.status.value
                    if self.lifecycle.onebot11
                    else "disabled",
                }
            )

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
            if "ws_reverse_token" in data and data["ws_reverse_token"] != "***":  # noqa: S105
                ob["ws_reverse_token"] = data["ws_reverse_token"]
            self.lifecycle.save_config()
            return jsonify(
                {"ok": True, "note": "Restart required for adapter changes to take effect."}
            )

        # ── MCP Servers ──
        @app.route("/api/mcp/servers", methods=["GET"])
        async def list_mcp():
            servers = self.lifecycle.config.get("mcp_servers", {})
            snapshot = await self._reload_mcp_tools(force=False)
            states = {item.get("name"): item for item in snapshot.get("servers", [])}
            result = []
            for name, cfg in servers.items():
                state = states.get(name, {})
                result.append(
                    {
                        "name": name,
                        "active": cfg.get("active", True),
                        **{k: v for k, v in cfg.items() if k != "active"},
                        "status": state.get("status", "inactive"),
                        "error": state.get("error", ""),
                        "tools": state.get("tools", []),
                        "resources": state.get("resources", []),
                        "resource_templates": state.get("resource_templates", []),
                        "prompts": state.get("prompts", []),
                        "protocol_version": state.get("protocol_version", ""),
                        "server_info": state.get("server_info", {}),
                    }
                )
            return jsonify(result)

        @app.route("/api/mcp/status", methods=["GET"])
        async def mcp_status():
            return jsonify(await self._reload_mcp_tools(force=False))

        @app.route("/api/mcp/reload", methods=["POST"])
        async def reload_mcp():
            return jsonify(await self._reload_mcp_tools(force=True))

        @app.route("/api/mcp/servers", methods=["POST"])
        async def add_mcp():
            data = await request.get_json(silent=True) or {}
            name = str(data.pop("name", "") or "").strip()
            if not name:
                return jsonify({"error": "name required"}), 400
            servers = self.lifecycle.config.setdefault("mcp_servers", {})
            servers[name] = data
            self.lifecycle.save_config()
            snapshot = await self._reload_mcp_tools(force=False)
            return jsonify({"ok": True, "mcp": snapshot})

        @app.route("/api/mcp/servers/<name>", methods=["PUT"])
        async def update_mcp(name: str):
            data = await request.get_json(silent=True) or {}
            servers = self.lifecycle.config.setdefault("mcp_servers", {})
            if name not in servers:
                return jsonify({"error": "not found"}), 404
            servers[name].update(data)
            self.lifecycle.save_config()
            snapshot = await self._reload_mcp_tools(force=False)
            return jsonify({"ok": True, "mcp": snapshot})

        @app.route("/api/mcp/servers/<name>", methods=["DELETE"])
        async def delete_mcp(name: str):
            servers = self.lifecycle.config.get("mcp_servers", {})
            servers.pop(name, None)
            self.lifecycle.save_config()
            snapshot = await self._reload_mcp_tools(force=False)
            return jsonify({"ok": True, "mcp": snapshot})

        @app.route("/api/mcp/servers/<name>/validate", methods=["POST"])
        async def validate_mcp(name: str):
            servers = self.lifecycle.config.get("mcp_servers", {})
            cfg = servers.get(name)
            if cfg is None:
                return jsonify({"error": "not found"}), 404
            return jsonify(await self._validate_mcp_server(name, cfg))

        @app.route("/api/mcp/servers/<name>/reload", methods=["POST"])
        async def reload_mcp_server(name: str):
            snapshot = await self._reload_mcp_tools(force=True)
            for server in snapshot.get("servers", []):
                if server.get("name") == name:
                    return jsonify(server)
            return jsonify({"error": "not found"}), 404

        # ── Skills ──
        @app.route("/api/skills", methods=["GET"])
        async def list_skills():
            sm = (
                self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            )
            if sm is None:
                return jsonify([])
            skills = sm.list_skills(active_only=False)
            return jsonify(
                [
                    {
                        "name": s.name,
                        "description": s.description,
                        "path": s.path,
                        "active": s.active,
                        "root": s.root,
                        "source": s.source,
                        "format": s.format,
                        "companion_files": s.companion_files,
                        "warnings": s.warnings,
                        "can_delete": s.can_delete,
                    }
                    for s in skills
                ]
            )

        @app.route("/api/skills/<name>")
        async def get_skill(name: str):
            sm = (
                self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            )
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            try:
                loaded = sm.load_skill(name, active_only=False)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            except KeyError:
                return jsonify({"error": "skill not found"}), 404
            s = loaded.info
            return jsonify(
                {
                    "name": s.name,
                    "description": s.description,
                    "path": s.path,
                    "active": s.active,
                    "root": s.root,
                    "source": s.source,
                    "format": s.format,
                    "companion_files": s.companion_files,
                    "warnings": s.warnings,
                    "can_delete": s.can_delete,
                    "content": loaded.content,
                }
            )

        @app.route("/api/skills/<name>", methods=["PUT"])
        async def update_skill(name: str):
            data = await request.get_json()
            sm = (
                self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            )
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            try:
                if "active" in data:
                    sm.set_skill_active(name, bool(data["active"]))
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            self.lifecycle.save_config()
            self._reload_skills_prompt()
            return jsonify({"ok": True})

        @app.route("/api/skills/<name>", methods=["DELETE"])
        async def delete_skill(name: str):
            sm = (
                self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            )
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            try:
                sm.delete_skill(name)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            except PermissionError as e:
                return jsonify({"error": str(e)}), 403
            self.lifecycle.save_config()
            self._reload_skills_prompt()
            return jsonify({"ok": True})

        @app.route("/api/skills/upload", methods=["POST"])
        async def upload_skill():
            sm = (
                self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            )
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
            sm = (
                self.lifecycle.process_stage.skill_manager if self.lifecycle.process_stage else None
            )
            if sm is None:
                return jsonify({"error": "skill manager not available"}), 503
            try:
                skill = sm.get_skill(name, active_only=False)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            if skill is None:
                return jsonify({"error": "skill not found"}), 404
            skill_dir = Path(skill.path).parent
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
                internal_id = (
                    f"webchat:friend:{session_id}" if ":" not in session_id else session_id
                )
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

        # 鈹€鈹€ Runtime timeline 鈹€鈹€
        @app.route("/api/runtime/threads")
        async def list_runtime_threads():
            store = self._runtime_store()
            if store is None:
                return jsonify([])
            limit = _parse_int(request.args.get("limit"), 50)
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
            store = self._runtime_store()
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
            store = self._runtime_store()
            if store is None:
                return jsonify({"events": [], "latest_seq": 0})
            session_id = (request.args.get("session_id") or "").strip()
            thread_id = normalize_session_id(session_id) if session_id else None
            since_seq = _parse_int(request.args.get("since_seq"), 0)
            limit = _parse_int(request.args.get("limit"), 1000)
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
                ws, target = _resolve_workspace_path(
                    self.lifecycle.config.get("workspace", "."),
                    rel,
                )
            except PermissionError:
                return jsonify({"error": "path outside workspace"}), 403
            if not target.exists() or not target.is_dir():
                return jsonify({"entries": [], "path": rel})
            entries = []
            try:
                for item in sorted(
                    target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
                ):
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
                _, target = _resolve_workspace_path(
                    self.lifecycle.config.get("workspace", "."),
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
                _, target = _resolve_workspace_path(
                    self.lifecycle.config.get("workspace", "."),
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

    def _register_chat_routes(self):
        app = self.app

        @app.route("/api/agent-mode", methods=["GET"])
        async def get_agent_mode():
            mode = (
                self.lifecycle.process_stage.agent_mode
                if self.lifecycle.process_stage
                else self.lifecycle.config.get("agent_mode", "agent")
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
            self.lifecycle.config["agent_mode"] = mode
            if self.lifecycle.process_stage:
                mode = self.lifecycle.process_stage.set_agent_mode(
                    mode,
                    source="user",
                    reason=reason,
                )
            self.lifecycle.save_config()
            return jsonify({"mode": mode})

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
                token_usage: dict[str, Any] = {}
                if self.lifecycle.process_stage:
                    agent = self.lifecycle.process_stage.get_agent(event.unified_msg_origin)
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
            tools = await asyncio.to_thread(
                create_tools,
                ws,
                mcp_servers=self.lifecycle.config.get("mcp_servers", {}),
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

        # ── Dangerous command approval ──
        @app.route("/api/approve-command", methods=["POST"])
        async def approve_command():
            data = await request.get_json()
            session_id = normalize_session_id(data.get("session_id", ""))
            bash_tool = self._find_bash_tool(session_id)
            if bash_tool and bash_tool.has_pending:
                result = bash_tool.approve_pending()
                await self.broadcast(
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
            bash_tool = self._find_bash_tool(session_id)
            if bash_tool and bash_tool.has_pending:
                result = bash_tool.reject_pending()
                await self.broadcast(
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
            bash_tool = self._find_bash_tool(session_id)
            if bash_tool and bash_tool.has_pending:
                return jsonify({"pending": True, **bash_tool.pending_info})
            return jsonify({"pending": False})

    def _register_ws_and_fallback(self):
        app = self.app

        # ── WebSocket ──
        @app.websocket("/ws")
        async def ws_handler():
            if self.auth_setup_required:
                await websocket.close(1008)
                return
            if self.auth_enabled:
                session_token = websocket.headers.get("X-ATRI-Session", "") or _cookie_value(
                    websocket.headers.get("Cookie", ""), _AUTH_COOKIE
                )
                if not hmac.compare_digest(session_token, self.auth_session_token):
                    await websocket.close(1008)
                    return
            ws_obj = websocket._get_current_object()  # type: ignore[attr-defined]
            self._ws_clients.add(ws_obj)
            try:
                while True:
                    data = await websocket.receive()
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send(json.dumps({"type": "pong"}))
                    elif msg.get("type") == "runtime_replay":
                        store = self._runtime_store()
                        if store is None:
                            continue
                        session_id = str(msg.get("session_id") or "").strip()
                        if not session_id:
                            continue
                        thread_id = normalize_session_id(session_id)
                        since_seq = _parse_int(msg.get("since_seq"), 0)
                        limit = _parse_int(msg.get("limit"), 1000)
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
                self._ws_clients.discard(ws_obj)

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


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
