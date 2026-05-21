import pytest

from core.agent.agent import Agent
from core.agent.llm import ToolCall
from core.tools import mcp


class _FakeMCPClient:
    alive = True
    protocol_version = "test"
    stderr_tail = ""

    def __init__(self):
        self.server_info = {"name": "fake"}

    def start(self):
        return None

    def list_tools(self):
        return [
            {
                "name": "safe_status",
                "description": "Read server status.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "danger_write",
                "description": "Mutate server state.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    def list_resources(self):
        return []

    def list_resource_templates(self):
        return []

    def list_prompts(self):
        return []

    def close(self):
        return None


def test_mcp_safe_names_and_registered_tool_names_are_provider_safe():
    assert mcp._safe_name(" demo server! ") == "demo_server_46f0a6"
    assert mcp._safe_name("!!!", fallback="fallback") == "fallback_9a7b00"
    assert mcp._safe_name("", fallback="fallback") == "fallback"

    registered = mcp.mcp_tool_name("server with spaces", "tool/with/slash")

    assert registered.startswith("mcp_server_with_spaces_")
    assert "/" not in registered
    assert len(registered) <= mcp._TOOL_NAME_LIMIT


def test_mcp_server_transport_infers_defaults_from_config():
    assert mcp._server_transport({"transport": "SSE"}) == "sse"
    assert mcp._server_transport({"command": "node"}) == "stdio"
    assert mcp._server_transport({"url": "https://example.test/mcp"}) == "streamable_http"
    assert mcp._server_transport({}) == "stdio"


def test_mcp_normalize_parameters_repairs_invalid_shapes():
    assert mcp._normalize_parameters("bad") == {"type": "object", "properties": {}}
    assert mcp._normalize_parameters({"properties": [], "required": "x"}) == {
        "type": "object",
        "properties": {},
    }
    assert mcp._normalize_parameters({"type": "object", "properties": {"x": {}}}) == {
        "type": "object",
        "properties": {"x": {}},
    }


def test_mcp_decode_http_rpc_response_supports_json_and_sse():
    assert mcp._decode_http_rpc_response(
        b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}',
        "application/json",
        1,
    ) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

    assert mcp._decode_http_rpc_response(
        b'event: message\ndata: {"jsonrpc":"2.0","id":"2","result":{"ok":true}}\n\n',
        "text/event-stream",
        2,
    ) == {"jsonrpc": "2.0", "id": "2", "result": {"ok": True}}

    with pytest.raises(mcp.MCPError, match="empty HTTP response"):
        mcp._decode_http_rpc_response(b"", "application/json", 1)
    with pytest.raises(mcp.MCPError, match="no matching JSON-RPC response"):
        mcp._decode_http_rpc_response(b'{"id":2}', "application/json", 1)
    with pytest.raises(mcp.MCPError, match="no JSON-RPC message found"):
        mcp._decode_http_rpc_response(b"event: ping\n\n", "text/event-stream", 1)


def test_mcp_decode_rpc_messages_filters_batch_by_request_id():
    assert mcp._decode_rpc_message('[{"id":1},{"id":2,"result":"ok"}]', 2) == {
        "id": 2,
        "result": "ok",
    }
    assert mcp._decode_rpc_message('{"method":"notification"}', None) == {"method": "notification"}
    assert mcp._decode_rpc_message('{"id":1}', 2) is None


def test_mcp_format_result_flattens_content_resources_prompts_and_errors():
    result = mcp._format_mcp_result(
        {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image", "mimeType": "image/png"},
            ],
            "contents": [{"uri": "file:///a.txt", "text": "resource text"}],
            "messages": [{"role": "user", "content": {"type": "text", "text": "prompt"}}],
            "structuredContent": {"n": 1},
        }
    )

    assert "hello" in result
    assert "[image content omitted: image/png]" in result
    assert "file:///a.txt\nresource text" in result
    assert "user: prompt" in result
    assert '"n": 1' in result

    assert (
        mcp._format_mcp_result({"isError": True, "content": [{"type": "text", "text": "bad"}]})
        == "Error: bad"
    )


def test_mcp_create_tools_returns_empty_for_none_or_empty_config(tmp_path):
    assert mcp.create_mcp_tools(str(tmp_path), None) == []
    assert mcp.create_mcp_tools(str(tmp_path), {}) == []


def test_mcp_read_only_allowlist_exposes_only_named_tools_to_non_admin(
    monkeypatch,
    tmp_path,
):
    registry = mcp.MCPRegistry()
    monkeypatch.setattr(
        registry,
        "_create_client",
        lambda name, cfg, workspace: _FakeMCPClient(),
    )
    registry.refresh(
        {
            "demo": {
                "command": "fake",
                "read_only_tools": ["safe_status"],
            }
        },
        workspace=str(tmp_path),
        force=True,
    )
    tools = {tool.name: tool for tool in registry.create_tools(str(tmp_path))}
    agent = Agent.__new__(Agent)
    agent.tools = list(tools.values())
    agent.high_privilege_tools_allowed = False

    schema_names = {schema["function"]["name"] for schema in agent._tool_schemas()}

    assert tools["mcp_demo_safe_status"].metadata()["read_only"] is True
    assert tools["mcp_demo_danger_write"].metadata()["read_only"] is False
    assert "mcp_demo_safe_status" in schema_names
    assert "mcp_demo_danger_write" not in schema_names
    assert agent._exec_tool(
        ToolCall(id="danger", name="mcp_demo_danger_write", arguments={})
    ).startswith("Error: high-privilege tool 'mcp_demo_danger_write' is restricted")
