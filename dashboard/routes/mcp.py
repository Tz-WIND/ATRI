"""MCP (Model Context Protocol) server management routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from quart import jsonify, request

if TYPE_CHECKING:
    from dashboard.server import Dashboard


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    @app.route("/api/mcp/servers", methods=["GET"])
    async def list_mcp():
        servers = dashboard.lifecycle.config.get("mcp_servers", {})
        snapshot = await dashboard._reload_mcp_tools(force=False)
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
        return jsonify(await dashboard._reload_mcp_tools(force=False))

    @app.route("/api/mcp/reload", methods=["POST"])
    async def reload_mcp():
        return jsonify(await dashboard._reload_mcp_tools(force=True))

    @app.route("/api/mcp/servers", methods=["POST"])
    async def add_mcp():
        data = await request.get_json(silent=True) or {}
        name = str(data.pop("name", "") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        servers = dashboard.lifecycle.config.setdefault("mcp_servers", {})
        servers[name] = data
        dashboard.lifecycle.save_config()
        snapshot = await dashboard._reload_mcp_tools(force=False)
        return jsonify({"ok": True, "mcp": snapshot})

    @app.route("/api/mcp/servers/<name>", methods=["PUT"])
    async def update_mcp(name: str):
        data = await request.get_json(silent=True) or {}
        servers = dashboard.lifecycle.config.setdefault("mcp_servers", {})
        if name not in servers:
            return jsonify({"error": "not found"}), 404
        servers[name].update(data)
        dashboard.lifecycle.save_config()
        snapshot = await dashboard._reload_mcp_tools(force=False)
        return jsonify({"ok": True, "mcp": snapshot})

    @app.route("/api/mcp/servers/<name>", methods=["DELETE"])
    async def delete_mcp(name: str):
        servers = dashboard.lifecycle.config.get("mcp_servers", {})
        servers.pop(name, None)
        dashboard.lifecycle.save_config()
        snapshot = await dashboard._reload_mcp_tools(force=False)
        return jsonify({"ok": True, "mcp": snapshot})

    @app.route("/api/mcp/servers/<name>/validate", methods=["POST"])
    async def validate_mcp(name: str):
        servers = dashboard.lifecycle.config.get("mcp_servers", {})
        cfg = servers.get(name)
        if cfg is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(await dashboard._validate_mcp_server(name, cfg))

    @app.route("/api/mcp/servers/<name>/reload", methods=["POST"])
    async def reload_mcp_server(name: str):
        snapshot = await dashboard._reload_mcp_tools(force=True)
        for server in snapshot.get("servers", []):
            if server.get("name") == name:
                return jsonify(server)
        return jsonify({"error": "not found"}), 404
