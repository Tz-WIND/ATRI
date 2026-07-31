import pytest

from core.agent.agent import Agent
from core.agent.mode import AgentModeController, normalize_agent_mode
from core.tools.mode import AgentModeTool


class _ModeTestLLM:
    model = "test-model"

    def clone(self, model=None):
        return _ModeTestLLM()


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


@pytest.mark.parametrize(
    ("current_mode", "next_mode", "write_visible"),
    [("plan", "agent", False), ("agent", "plan", True)],
)
def test_mode_tool_reports_frozen_current_turn_permissions(
    tmp_path, current_mode, next_mode, write_visible
):
    controller = AgentModeController(current_mode)
    agent = Agent(
        llm=_ModeTestLLM(),
        workspace=str(tmp_path),
        mode_controller=controller,
    )
    agent.bind_turn(current_mode)
    tool = next(item for item in agent.tools if isinstance(item, AgentModeTool))

    result = tool.execute(mode=next_mode, reason="requested")

    assert controller.mode == next_mode
    assert agent.effective_mode == current_mode
    assert "next turn" in result.lower()
    assert f"Current turn remains {current_mode.upper()} mode" in result
    assert ("write_file" in {item.name for item in agent._available_tools()}) is write_visible


def test_normalize_agent_mode_rejects_invalid_value():
    with pytest.raises(ValueError, match="mode must be one of"):
        normalize_agent_mode("execute")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plan", "plan"),
        ("AGENT", "agent"),
        ("deepresearch", "deepresearch"),
        ("DEEPRESEARCH", "deepresearch"),
    ],
)
def test_normalize_agent_mode_accepts_three_modes(value, expected):
    assert normalize_agent_mode(value) == expected
