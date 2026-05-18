from typing import Any

from core.agent.agent import Agent
from core.agent.llm import ToolCall
from core.tools import create_tools
from core.tools.base import Tool, ToolCapabilities
from core.tools.read import ReadFileTool


class _MetadataTool(Tool):
    name = "metadata_tool"
    description = "metadata test tool"
    parameters: dict[str, Any] = {  # noqa: RUF012
        "required": ["path"],
        "properties": {
            "path": {"description": "Path", "type": "string"},
        },
        "type": "object",
    }
    capabilities = ToolCapabilities(
        capability="test.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(self, **kwargs: Any) -> str:
        return "ok"


class _ParallelTool(_MetadataTool):
    name = "parallel_tool"


class _SerialTool(_MetadataTool):
    name = "serial_tool"
    capabilities = ToolCapabilities(capability="test.write", writes_files=True)


def test_tool_schema_is_stable_cached_and_metadata_opt_in(tmp_path):
    tool = _MetadataTool(str(tmp_path))

    schema = tool.schema()

    assert list(schema) == ["type", "function"]
    assert list(schema["function"]) == ["name", "description", "parameters"]
    assert list(schema["function"]["parameters"]) == ["type", "properties", "required"]
    assert "metadata" not in schema

    schema["function"]["parameters"]["properties"]["path"]["description"] = "mutated"
    assert tool.schema()["function"]["parameters"]["properties"]["path"]["description"] == "Path"

    enriched = tool.schema(include_metadata=True)
    assert enriched["metadata"] == {
        "name": "metadata_tool",
        "capability": "test.read",
        "read_only": True,
        "writes_files": False,
        "executes_shell": False,
        "network": False,
        "requires_approval": False,
        "supports_parallel": True,
    }


def test_registered_tools_expose_capability_metadata(tmp_path):
    tools = {tool.name: tool for tool in create_tools(str(tmp_path))}

    assert tools["read_file"].metadata()["read_only"] is True
    assert tools["read_file"].metadata()["supports_parallel"] is True
    assert tools["write_file"].metadata()["writes_files"] is True
    assert tools["bash"].metadata()["executes_shell"] is True
    assert tools["bash"].metadata()["requires_approval"] is True
    assert tools["web_search"].metadata()["network"] is True
    assert tools["novelai_image"].metadata()["network"] is True
    assert tools["novelai_image"].metadata()["writes_files"] is False
    assert tools["agent_result"].metadata()["read_only"] is True
    assert tools["set_agent_mode"].metadata()["capability"] == "agent.mode"

    assert all(tool.metadata()["capability"] != "general" for tool in tools.values())


def test_agent_parallel_gate_uses_tool_capabilities(tmp_path):
    agent = Agent.__new__(Agent)
    agent.tools = [_ParallelTool(str(tmp_path)), _SerialTool(str(tmp_path))]

    assert agent._all_tools_support_parallel([ToolCall(id="1", name="parallel_tool", arguments={})])
    assert not agent._all_tools_support_parallel(
        [
            ToolCall(id="1", name="parallel_tool", arguments={}),
            ToolCall(id="2", name="serial_tool", arguments={}),
        ]
    )
    assert not agent._all_tools_support_parallel(
        [ToolCall(id="1", name="missing_tool", arguments={})]
    )


def test_builtin_tool_schema_can_include_metadata(tmp_path):
    schema = ReadFileTool(str(tmp_path)).schema(include_metadata=True)

    assert schema["metadata"]["capability"] == "filesystem.read"
    assert schema["metadata"]["read_only"] is True
    assert schema["metadata"]["supports_parallel"] is True
