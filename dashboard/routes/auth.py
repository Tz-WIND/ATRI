"""Authentication routes: status, login, setup, logout."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from quart import jsonify, request

from dashboard.routes._helpers import AUTH_COOKIE, check_rate_limit, hash_password, record_failure

if TYPE_CHECKING:
    from dashboard.server import Dashboard


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    @app.route("/api/auth/status")
    async def auth_status():
        authenticated = (
            False
            if dashboard.auth_setup_required
            else not dashboard.auth_enabled or dashboard._request_authenticated()
        )
        response: dict[str, Any] = {
            "auth_required": dashboard.auth_enabled,
            "setup_required": dashboard.auth_setup_required,
            "authenticated": authenticated,
        }
        if authenticated or dashboard.auth_setup_required:
            response["username"] = dashboard.auth_username
        return jsonify(response)

    @app.route("/api/auth/login", methods=["POST"])
    async def auth_login():
        if dashboard.auth_setup_required:
            return jsonify({"error": "setup required", "setup_required": True}), 428
        client_ip = request.remote_addr or "unknown"
        if check_rate_limit(client_ip):
            return jsonify({"error": "too many attempts, try again later"}), 429
        data = await request.get_json(silent=True) or {}
        username = str(data.get("username", "") or "")
        password = str(data.get("password", "") or "")
        if not dashboard._credentials_ok(username, password):
            record_failure(client_ip)
            return jsonify({"error": "invalid username or password"}), 401
        if not dashboard.auth_password_is_hashed:
            dashboard_cfg = dashboard.lifecycle.config.setdefault("dashboard", {})
            dashboard_cfg["password"] = hash_password(password)
            dashboard.lifecycle.save_config()
            dashboard._sync_auth_from_config()
        token = dashboard._create_auth_session()
        resp = jsonify({"ok": True})
        resp.set_cookie(
            AUTH_COOKIE,
            token,
            httponly=True,
            samesite="Strict",
        )
        return resp

    @app.route("/api/auth/setup", methods=["POST"])
    async def auth_setup():
        if not dashboard.auth_setup_required:
            return jsonify({"error": "setup is not required"}), 409
        client_ip = request.remote_addr or "unknown"
        if check_rate_limit(client_ip):
            return jsonify({"error": "too many attempts, try again later"}), 429
        data = await request.get_json(silent=True) or {}
        username = str(data.get("username", "") or "").strip()
        password = str(data.get("password", "") or "")
        if not username:
            return jsonify({"error": "username required"}), 400
        if not password:
            return jsonify({"error": "password required"}), 400

        dashboard_cfg = dashboard.lifecycle.config.setdefault("dashboard", {})
        dashboard_cfg["username"] = username
        dashboard_cfg["password"] = hash_password(password)
        dashboard.lifecycle.save_config()
        dashboard._sync_auth_from_config()
        token = dashboard._create_auth_session()

        resp = jsonify({"ok": True})
        resp.set_cookie(
            AUTH_COOKIE,
            token,
            httponly=True,
            samesite="Strict",
        )
        return resp

    @app.route("/api/auth/logout", methods=["POST"])
    async def auth_logout():
        dashboard._revoke_auth_session(dashboard._provided_session_token())
        resp = jsonify({"ok": True})
        resp.delete_cookie(AUTH_COOKIE)
        return resp
