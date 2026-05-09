import pytest

from core.agent.mode import AgentModeController, normalize_agent_mode
from core.tools.mode import AgentModeTool


def test_mode_controller_normalizes_and_reports_changes():
    seen = []
    controller = AgentModeController("PLAN", on_change=lambda *args: seen.append(args))

    assert controller.mode == "plan"
    assert controller.set_mode("agent", source="test", reason="go")[1] is True
    assert controller.mode == "agent"
    assert seen == [("agent", "test", "go")]

    assert controller.set_mode("AGENT", source="test")[1] is False
    assert len(seen) == 1


def test_mode_tool_switches_shared_controller(tmp_path):
    controller = AgentModeController("agent")
    tool = AgentModeTool(str(tmp_path), mode_controller=controller)

    result = tool.execute(mode="plan", reason="inspect first")

    assert controller.mode == "plan"
    assert result == "Switched to PLAN mode. Reason: inspect first"


def test_normalize_agent_mode_rejects_invalid_value():
    with pytest.raises(ValueError, match="mode must be one of"):
        normalize_agent_mode("execute")
