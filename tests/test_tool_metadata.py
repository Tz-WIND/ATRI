from typing import Any

from core.agent.agent import Agent
from core.agent.llm import ToolCall
from core.agent.mode import AgentModeController
from core.tools import create_tools
from core.tools.base import Tool, ToolCapabilities
from core.tools.mode import AgentModeTool
from core.tools.read import ReadFileTool
from core.tools.write import WriteFileTool


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


class _StateChangingTool(_MetadataTool):
    name = "state_changing_tool"
    capabilities = ToolCapabilities(capability="test.state.change")


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
    assert tools["chem_draw"].metadata()["capability"] == "chemistry.draw"
    assert tools["chem_draw"].metadata()["writes_files"] is True
    assert tools["chem_draw"].metadata()["supports_parallel"] is True
    assert tools["novelai_image"].metadata()["network"] is True
    assert tools["novelai_image"].metadata()["writes_files"] is False
    assert tools["agent_result"].metadata()["read_only"] is True
    assert tools["set_agent_mode"].metadata()["capability"] == "agent.mode"
    assert tools["vst_param_query"].metadata()["capability"] == "music.vst.read"
    assert tools["vst_param_query"].metadata()["read_only"] is True
    assert tools["vst_param_set"].metadata()["capability"] == "music.vst.write"
    assert tools["vst_param_set"].metadata()["writes_files"] is True
    assert tools["vst_param_set"].metadata()["network"] is True
    assert tools["automation_query"].metadata()["capability"] == "music.automation.read"
    assert tools["automation_write"].metadata()["capability"] == "music.automation.write"
    assert tools["automation_global_write"].metadata()["capability"] == "music.automation.write"
    assert tools["automation_diff"].metadata()["capability"] == "music.automation.write"
    assert tools["automation_retarget"].metadata()["capability"] == "music.automation.write"
    assert tools["studio_project_query"].metadata()["capability"] == "music.studio.read"
    assert tools["studio_project_query"].metadata()["read_only"] is True
    assert tools["studio_project_query"].metadata()["supports_parallel"] is True
    assert tools["studio_host_control"].metadata()["capability"] == "music.studio.host"
    assert tools["studio_host_control"].metadata()["requires_approval"] is True
    assert tools["studio_transport"].metadata()["capability"] == "music.studio.transport"
    assert tools["studio_track"].metadata()["capability"] == "music.studio.track"
    assert tools["studio_track"].metadata()["requires_approval"] is True
    assert tools["studio_plugin"].metadata()["capability"] == "music.studio.plugin"
    assert tools["studio_plugin"].metadata()["requires_approval"] is True
    assert tools["studio_audio_import"].metadata()["capability"] == "music.studio.audio"
    assert tools["studio_audio_import"].metadata()["writes_files"] is True
    assert tools["studio_piano_lane_write"].metadata()["capability"] == "music.studio.piano_lane"
    assert tools["studio_piano_lane_write"].metadata()["writes_files"] is True
    assert tools["studio_piano_lane_diff"].metadata()["capability"] == "music.studio.piano_lane"
    assert tools["studio_piano_lane_diff"].metadata()["writes_files"] is True
    assert tools["music_harmony_analyze"].metadata()["capability"] == "music.harmony.analyze"
    assert tools["music_harmony_analyze"].metadata()["writes_files"] is True
    assert tools["studio_sync"].metadata()["capability"] == "music.studio.sync"

    assert all(tool.metadata()["capability"] != "general" for tool in tools.values())


def test_registered_tool_array_schemas_declare_items(tmp_path):
    tools = create_tools(str(tmp_path))
    missing_items: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            if value.get("type") == "array" and "items" not in value:
                missing_items.append(path)
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    for tool in tools:
        visit(tool.schema()["function"]["parameters"], tool.name)

    assert missing_items == []


def test_registered_tool_schemas_use_openai_compatible_top_level_parameters(tmp_path):
    forbidden_top_level_keywords = {"oneOf", "anyOf", "allOf", "enum", "not"}
    invalid_schemas: list[str] = []

    for tool in create_tools(str(tmp_path)):
        parameters = tool.schema()["function"]["parameters"]
        if parameters.get("type") != "object":
            invalid_schemas.append(f"{tool.name}.type")
        for keyword in sorted(forbidden_top_level_keywords):
            if keyword in parameters:
                invalid_schemas.append(f"{tool.name}.{keyword}")

    assert invalid_schemas == []


def test_music_generation_tool_descriptions_explain_harmony_notes_expression_order(tmp_path):
    tools = {tool.name: tool for tool in create_tools(str(tmp_path))}

    assert "Use harmony lane context before note generation" in tools["midi_write"].description
    assert "use after notes exist" in tools["midi_batch_edit"].description
    assert "velocity" in tools["midi_batch_edit"].description
    assert "CC" in tools["midi_batch_edit"].description


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


def test_agent_blocks_high_privilege_tools_when_not_allowed(tmp_path):
    agent = Agent.__new__(Agent)
    agent.tools = [
        ReadFileTool(str(tmp_path)),
        WriteFileTool(str(tmp_path)),
        _MetadataTool(str(tmp_path)),
        _StateChangingTool(str(tmp_path)),
    ]
    agent.high_privilege_tools_allowed = False

    schema_names = {schema["function"]["name"] for schema in agent._tool_schemas()}

    assert "read_file" in schema_names
    assert "metadata_tool" in schema_names
    assert "write_file" not in schema_names
    assert "state_changing_tool" not in schema_names
    assert agent._exec_tool(
        ToolCall(
            id="1",
            name="write_file",
            arguments={"file_path": "blocked.txt", "content": "nope"},
        )
    ).startswith("Error: high-privilege tool 'write_file' is restricted")
    assert agent._exec_tool(
        ToolCall(
            id="2",
            name="state_changing_tool",
            arguments={},
        )
    ).startswith("Error: high-privilege tool 'state_changing_tool' is restricted")
    assert not (tmp_path / "blocked.txt").exists()


def test_agent_blocks_music_state_tools_without_file_or_approval_flags(tmp_path):
    tools = {tool.name: tool for tool in create_tools(str(tmp_path))}
    mutating_tool_names = {
        "midi_write",
        "midi_diff",
        "midi_batch_edit",
        "vst_param_set",
        "automation_write",
        "automation_global_write",
        "automation_diff",
        "automation_retarget",
        "studio_piano_lane_write",
        "studio_piano_lane_diff",
        "music_harmony_analyze",
    }
    read_only_tool_names = {
        "midi_query",
        "midi_inspect",
        "vst_param_query",
        "automation_query",
    }
    agent = Agent.__new__(Agent)
    agent.tools = [tools[name] for name in sorted(mutating_tool_names | read_only_tool_names)]
    agent.high_privilege_tools_allowed = False

    schema_names = {schema["function"]["name"] for schema in agent._tool_schemas()}

    assert mutating_tool_names.isdisjoint(schema_names)
    assert read_only_tool_names <= schema_names
    for tool_name in mutating_tool_names:
        assert agent._exec_tool(ToolCall(id=tool_name, name=tool_name, arguments={})).startswith(
            f"Error: high-privilege tool '{tool_name}' is restricted"
        )


def test_agent_plan_mode_exposes_only_read_only_tools_and_mode_switch(tmp_path):
    mode_controller = AgentModeController("plan")
    agent = Agent.__new__(Agent)
    agent.mode_controller = mode_controller
    agent.tools = [
        ReadFileTool(str(tmp_path)),
        WriteFileTool(str(tmp_path)),
        _MetadataTool(str(tmp_path)),
        _StateChangingTool(str(tmp_path)),
        AgentModeTool(str(tmp_path), mode_controller=mode_controller),
    ]
    agent.high_privilege_tools_allowed = True

    schema_names = {schema["function"]["name"] for schema in agent._tool_schemas()}

    assert "read_file" in schema_names
    assert "metadata_tool" in schema_names
    assert "set_agent_mode" in schema_names
    assert "write_file" not in schema_names
    assert "state_changing_tool" not in schema_names
    assert agent._exec_tool(
        ToolCall(
            id="write",
            name="write_file",
            arguments={"file_path": "blocked.txt", "content": "nope"},
        )
    ).startswith("Error: tool 'write_file' is restricted in PLAN mode")
    assert not (tmp_path / "blocked.txt").exists()

    result = agent._exec_tool(
        ToolCall(
            id="mode",
            name="set_agent_mode",
            arguments={"mode": "agent", "reason": "implementation requested"},
        )
    )

    assert result == "Switched to AGENT mode. Reason: implementation requested"
    assert mode_controller.mode == "agent"
