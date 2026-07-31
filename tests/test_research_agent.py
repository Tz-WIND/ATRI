import threading
import time
from pathlib import Path

import pytest

from core.agent.agent import Agent
from core.agent.llm import ToolCall
from core.agent.mode import AgentModeController
from core.research import ResearchPolicy, ResearchSession
from core.runtime.todos import TodoStore
from core.tools import create_tools
from core.tools.agent_tool import AgentTool


class _NoopLLM:
    model = "test-model"

    def clone(self, model=None):
        return _NoopLLM()


@pytest.fixture
def session(tmp_path):
    return ResearchSession(
        policy=ResearchPolicy.from_config({}, tmp_path),
        turn_id="turn-1",
        session_id="session-1",
        original_user_request="research the evidence",
    )


@pytest.fixture
def agent(tmp_path):
    return Agent(
        llm=_NoopLLM(),
        workspace=str(tmp_path),
        mode_controller=AgentModeController("agent"),
    )


def test_deepresearch_only_exposes_readonly_and_research_controls(agent, session):
    agent.bind_turn("deepresearch", session=session)

    names = {tool.name for tool in agent._available_tools()}

    assert {
        "rag_search",
        "graphrag_search",
        "web_search",
        "web_fetch",
        "research_checkpoint",
        "research_evidence",
        "agent",
        "export_research_report",
    } <= names
    assert {
        "bash",
        "terminal",
        "write_file",
        "edit_file",
        "set_agent_mode",
        "agent_result",
        "task_result",
    }.isdisjoint(names)


def test_research_controls_are_hidden_outside_deepresearch(agent):
    names = {tool.name for tool in agent._available_tools()}
    assert {"research_checkpoint", "research_evidence", "export_research_report"}.isdisjoint(names)
    assert {"rag_search", "graphrag_search"} <= names


def test_running_turn_keeps_frozen_mode_when_global_mode_changes(agent, session):
    agent.bind_turn("deepresearch", session=session)
    agent.mode_controller.set_mode("agent")

    assert agent.effective_mode == "deepresearch"
    assert "set_agent_mode" not in {tool.name for tool in agent._available_tools()}

    agent.unbind_turn()
    assert agent.effective_mode == "agent"


def test_stale_turn_cleanup_cannot_unbind_newer_turn(agent, session, tmp_path):
    newer_session = ResearchSession(
        policy=ResearchPolicy.from_config({}, tmp_path),
        turn_id="turn-2",
        session_id="session-1",
        original_user_request="newer research",
    )
    stale_token = agent.bind_turn("deepresearch", session=session)
    current_token = agent.bind_turn("deepresearch", session=newer_session)

    assert stale_token != current_token
    assert agent.unbind_turn(stale_token) is False
    assert agent.effective_mode == "deepresearch"
    assert agent.current_research_session() is newer_session

    assert agent.unbind_turn(current_token) is True
    assert agent.effective_mode == "agent"
    assert agent.current_research_session() is None


def test_deepresearch_exec_guard_cannot_be_bypassed_by_direct_tool_call(agent, session):
    agent.bind_turn("deepresearch", session=session)

    result = agent._exec_tool(
        ToolCall(
            id="write",
            name="write_file",
            arguments={"file_path": "blocked.txt", "content": "no"},
        )
    )

    assert "restricted in DEEP RESEARCH mode" in result
    assert not (Path(agent.workspace) / "blocked.txt").exists()


def test_deepresearch_cannot_access_or_directly_execute_todo(tmp_path, session):
    store = TodoStore(tmp_path / "runtime")
    guarded = Agent(
        llm=_NoopLLM(),
        workspace=str(tmp_path),
        mode_controller=AgentModeController("plan"),
        todo_store=store,
        todo_session_id="session-1",
    )
    try:
        guarded.bind_turn("deepresearch", session=session)

        assert "todo" not in {tool.name for tool in guarded._available_tools()}
        result = guarded._exec_tool(
            ToolCall(
                id="todo-write",
                name="todo",
                arguments={"action": "set", "items": ["must stay empty"]},
            )
        )

        assert "restricted in DEEP RESEARCH mode" in result
        assert store.snapshot("session-1")["items"] == []
    finally:
        store.close()


def test_plan_mode_keeps_session_todo_available(tmp_path):
    store = TodoStore(tmp_path / "runtime")
    planning = Agent(
        llm=_NoopLLM(),
        workspace=str(tmp_path),
        mode_controller=AgentModeController("plan"),
        todo_store=store,
        todo_session_id="session-1",
    )
    try:
        assert "todo" in {tool.name for tool in planning._available_tools()}
    finally:
        store.close()


def test_agent_cancel_propagates_to_research_session(agent, session, monkeypatch):
    cancelled = []
    monkeypatch.setattr(session, "cancel", lambda **kwargs: cancelled.append(kwargs))
    agent.bind_turn("deepresearch", session=session)

    agent.cancel()

    assert cancelled == [{"reason": "agent_cancelled"}]


def test_research_agent_rejects_background_and_too_many_tasks(agent, session):
    agent.bind_turn("deepresearch", session=session)
    tool = next(tool for tool in agent.tools if isinstance(tool, AgentTool))

    assert "background" in tool.execute(task="one", background=True).lower()
    assert "at most 3" in tool.execute(tasks=["1", "2", "3", "4"]).lower()
    assert session.budget.snapshot()["total_subagents"] == 0


def test_research_agent_reserves_shared_slots_and_assigns_branches(agent, session, monkeypatch):
    agent.bind_turn("deepresearch", session=session)
    tool = next(tool for tool in agent.tools if isinstance(tool, AgentTool))
    captured = []
    monkeypatch.setattr(
        tool,
        "_run_parallel_blocking",
        lambda tasks: captured.extend(tasks) or "research branches complete",
    )

    result = tool.execute(tasks=["verify source A", "verify source B"])

    assert result == "research branches complete"
    assert len({item["branch_id"] for item in captured}) == 2
    snapshot = session.budget.snapshot()
    assert snapshot["total_subagents"] == 2
    assert snapshot["active_subagents"] == 0


def test_research_child_has_no_recursive_or_export_tools(agent, session):
    agent.bind_turn("deepresearch", session=session)
    tool = next(tool for tool in agent.tools if isinstance(tool, AgentTool))

    child = tool._create_child_agent(
        {"task": "branch", "model": None, "provider": None, "branch_id": "branch-1"}
    )
    child.bind_turn("deepresearch", session=session, branch_id="branch-1")
    names = {item.name for item in child._available_tools()}

    assert {"rag_search", "graphrag_search", "research_evidence"} <= names
    assert {
        "agent",
        "agent_result",
        "task_result",
        "set_agent_mode",
        "export_research_report",
        "research_checkpoint",
    }.isdisjoint(names)
    assert child.current_research_session() is session
    assert "parent agent controls research phases" in child._build_system().lower()

    direct_result = child._exec_tool(
        ToolCall(
            id="checkpoint",
            name="research_checkpoint",
            arguments={"phase": "synthesizing"},
        )
    )
    assert "unknown tool" in direct_result


def test_create_tools_keeps_agent_tool_available_for_research(tmp_path):
    tools = create_tools(str(tmp_path))
    assert any(isinstance(tool, AgentTool) for tool in tools)


def test_research_agent_waits_for_cancelled_branch_without_overwriting_status(
    agent, session, monkeypatch
):
    agent.bind_turn("deepresearch", session=session)
    tool = next(tool for tool in agent.tools if isinstance(tool, AgentTool))
    started = threading.Event()
    released = threading.Event()
    finished = threading.Event()

    class _CooperativeChild:
        def chat(self, *args, **kwargs):
            started.set()
            released.wait(2)
            time.sleep(0.05)
            finished.set()
            return "[Interrupted by user]"

        def cancel(self):
            released.set()

    def synthesis_timeout():
        assert started.wait(1)
        return 0.01

    monkeypatch.setattr(tool, "_create_child_agent", lambda task_spec: _CooperativeChild())
    monkeypatch.setattr(session.budget, "seconds_until_synthesis", synthesis_timeout)
    session.register_branch("branch-slow", "slow")

    report = tool._run_parallel_blocking([{"task": "slow", "branch_id": "branch-slow"}])

    assert started.is_set()
    assert released.is_set()
    assert finished.is_set()
    assert session.branches["branch-slow"] == {
        "task": "slow",
        "status": "cancelled",
        "error": "synthesis deadline",
    }
    assert "Status: error" in report
    assert "Sub-agent stopped at the synthesis deadline" in report
    assert "Final result:" not in report
