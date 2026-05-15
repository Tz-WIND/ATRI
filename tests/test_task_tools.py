import re
from typing import cast

import pytest

from core.agent.agent import Agent
from core.runtime.tasks import TaskStore
from core.tools.agent_tool import (
    AgentResultTool,
    AgentTool,
    SubAgentRun,
    _background_tasks,
    _tasks_lock,
)
from core.tools.task_result import TaskResultTool


@pytest.fixture(autouse=True)
def clear_background_tasks():
    with _tasks_lock:
        _background_tasks.clear()
    yield
    with _tasks_lock:
        _background_tasks.clear()


def _extract_task_id(output: str) -> str:
    match = re.search(r"`(bg_[^`]+)`", output)
    assert match is not None
    return match.group(1)


def test_agent_background_task_persists_subagent_lifecycle(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "runtime")
    try:
        tool = AgentTool(str(tmp_path), task_store=store)
        tool._parent_agent = cast(Agent, object())

        def fake_run(run, task_spec):
            run.set_status("running")
            run.add_text("visible output")
            run.add_tool_start("call-1", "bash", {"command": "pytest -q"})
            run.add_tool_end("call-1", "bash", {"command": "pytest -q"}, "ok")
            run.finish(f"done: {task_spec['task']}")
            return run.result

        monkeypatch.setattr(tool, "_run_subagent_task", fake_run)

        output = tool.execute(
            task="run tests",
            background=True,
            model="gpt-test",
            provider="test-provider",
        )
        task_id = _extract_task_id(output)
        with _tasks_lock:
            run = _background_tasks[task_id]
        assert run.future is not None
        run.future.result(timeout=2)

        task = store.get_task(task_id)
        assert task is not None
        assert task["kind"] == "sub_agent"
        assert task["status"] == "completed"
        assert task["result"] == "done: run tests"
        assert task["metadata"]["model"] == "gpt-test"
        assert task["metadata"]["provider"] == "test-provider"
        assert task["metadata"]["text"] == "visible output"
        assert [event.event_type for event in store.events(task_id)] == [
            "task_created",
            "task_started",
            "text_delta",
            "tool_start",
            "tool_end",
            "task_finished",
        ]

        report = AgentResultTool(str(tmp_path), task_store=store).execute(task_id)
        assert "Status: done" in report
        assert "Final result:" in report

        with _tasks_lock:
            assert task_id not in _background_tasks
        persisted_report = AgentResultTool(str(tmp_path), task_store=store).execute(task_id)
        assert "Status: completed" in persisted_report
        assert "done: run tests" in persisted_report
    finally:
        store.close()


def test_agent_result_lists_and_reads_persisted_tasks_after_restart(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    try:
        task_id = store.create_task(
            task_id="bg_saved",
            kind="sub_agent",
            title="Saved task",
            input_text="inspect saved state",
            metadata={"model": "gpt-test"},
        )
        store.start_task(task_id)
        store.append_event(
            task_id,
            "tool_end",
            {"tool": "bash", "args": {"command": "pytest"}, "success": True},
        )
        store.finish_task(task_id, result="persisted result")

        result_tool = AgentResultTool(str(tmp_path), task_store=store)

        listing = result_tool.execute()
        assert "`bg_saved`: completed [gpt-test]" in listing

        report = result_tool.execute("bg_saved")
        assert "Background task `bg_saved` [gpt-test]" in report
        assert "Status: completed" in report
        assert "persisted result" in report
    finally:
        store.close()


def test_subagent_text_snapshot_updates_are_throttled(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "runtime")
    try:
        task_id = store.create_task(kind="sub_agent", title="stream", input_text="stream")
        run = SubAgentRun(task_id=task_id, task="stream", task_store=store)
        ticks = [10.0, 10.0, 10.0, 10.2, 10.2, 10.4, 10.4]

        def fake_monotonic():
            if ticks:
                return ticks.pop(0)
            return 10.4

        monkeypatch.setattr("core.tools.agent_tool.time.monotonic", fake_monotonic)

        run.add_text("a")
        run.add_text("b")
        run.add_text("c")

        task = store.get_task(task_id)
        assert task is not None
        assert task["metadata"]["text"] == "a"
        assert [event.event_type for event in store.events(task_id)].count("text_delta") == 3

        run.finish("done")

        finished_task = store.get_task(task_id)
        assert finished_task is not None
        assert finished_task["metadata"]["text"] == "abc"
    finally:
        store.close()


def test_task_result_tool_lists_and_queries_generic_persisted_tasks(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    try:
        sub_agent_id = store.create_task(
            task_id="bg_agent",
            kind="sub_agent",
            title="Background agent",
            input_text="inspect code",
        )
        store.finish_task(sub_agent_id, result="agent done")
        test_id = store.create_task(
            task_id="test_pytest",
            kind="test_command",
            title="pytest",
            input_text="uv run pytest -q",
            metadata={"text": "running pytest"},
        )
        store.record_evidence(test_id, {"command": "uv run pytest -q"})
        store.finish_task(test_id, result="93 passed")

        tool = TaskResultTool(str(tmp_path), task_store=store)

        listing = tool.execute(kind="test_command")
        assert "`test_pytest`: completed test_command - pytest" in listing
        assert "bg_agent" not in listing

        report = tool.execute(task_id="test_pytest")
        assert "Kind: test_command" in report
        assert "running pytest" in report
        assert "Evidence:" in report
        assert "93 passed" in report
    finally:
        store.close()


def test_task_result_tool_handles_missing_store_and_missing_task(tmp_path):
    assert TaskResultTool(str(tmp_path)).execute() == "No persistent task store is configured."

    store = TaskStore(tmp_path / "runtime")
    try:
        assert (
            TaskResultTool(str(tmp_path), task_store=store).execute(task_id="missing")
            == "No persisted task found with id 'missing'"
        )
    finally:
        store.close()
