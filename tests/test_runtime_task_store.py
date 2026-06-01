import sqlite3

import pytest

from core.runtime.tasks import TaskEvent, TaskStore


def test_runtime_task_store_lifecycle_events_evidence_and_artifacts(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    try:
        task_id = store.create_task(
            kind="sub_agent",
            title="Investigate failure",
            input_text="run tests and inspect logs",
            workspace=str(tmp_path),
            metadata={"model": "gpt-test"},
        )

        assert store.start_task(task_id) is True
        store.update_task(task_id, metadata={"text": "working"})
        store.record_evidence(task_id, {"tests": "pytest -q"})
        store.add_artifact(task_id, {"path": "reports/pytest.txt", "kind": "log"})
        event = store.append_event(task_id, "tool_end", {"tool": "bash", "success": True})
        assert store.finish_task(task_id, result="done") is True

        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert task["result"] == "done"
        assert task["metadata"] == {"model": "gpt-test", "text": "working"}
        assert task["evidence"] == {"tests": "pytest -q"}
        assert task["artifacts"] == [{"path": "reports/pytest.txt", "kind": "log"}]
        assert task["input_summary"] == "run tests and inspect logs"
        assert task["duration_ms"] is not None

        events = store.events(task_id)
        assert [item.event_type for item in events] == [
            "task_created",
            "task_started",
            "task_evidence",
            "task_artifact",
            "tool_end",
            "task_finished",
        ]
        assert event in events
        assert store.events(task_id, since_seq=event.seq)[-1].event_type == "task_finished"
        assert store.list_tasks(kind="sub_agent")[0]["id"] == task_id
    finally:
        store.close()


def test_runtime_task_store_marks_incomplete_tasks_interrupted(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    try:
        queued = store.create_task(kind="test_command", title="tests", input_text="pytest")
        running = store.create_task(kind="sub_agent", title="agent", input_text="work")
        store.start_task(running)
        completed = store.create_task(kind="sub_agent", title="done", input_text="done")
        store.finish_task(completed, result="ok")

        assert store.mark_incomplete_as_interrupted(reason="process restarted") == 2

        queued_task = store.get_task(queued)
        running_task = store.get_task(running)
        completed_task = store.get_task(completed)
        assert queued_task is not None
        assert running_task is not None
        assert completed_task is not None
        assert queued_task["status"] == "interrupted"
        assert running_task["status"] == "interrupted"
        assert completed_task["status"] == "completed"
        assert running_task["metadata"]["interrupted_reason"] == "process restarted"
    finally:
        store.close()


def test_runtime_task_store_marks_incomplete_tasks_interrupted_by_kind(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    try:
        graph = store.create_task(kind="graph_extraction", title="graph", input_text="extract")
        other = store.create_task(kind="sub_agent", title="agent", input_text="work")
        store.start_task(graph)
        store.start_task(other)

        assert (
            store.mark_incomplete_as_interrupted(
                reason="graph worker shut down before the task finished",
                kind="graph_extraction",
            )
            == 1
        )

        graph_task = store.get_task(graph)
        other_task = store.get_task(other)
        assert graph_task is not None
        assert other_task is not None
        assert graph_task["status"] == "interrupted"
        assert other_task["status"] == "running"
    finally:
        store.close()


def test_runtime_task_store_rejects_newer_schema_version(tmp_path):
    db_path = tmp_path / "tasks.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE task_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO task_meta(key, value) VALUES ('schema_version', '999')")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="newer than supported"):
        TaskStore(db_path)


def test_runtime_task_store_honors_explicit_db_path(tmp_path):
    db_path = tmp_path / "mystore.db"
    store = TaskStore(db_path)
    try:
        assert store.db_path == db_path
        task_id = store.create_task(kind="sub_agent", title="task")
        assert store.get_task(task_id) is not None
    finally:
        store.close()

    assert db_path.exists()
    assert not (tmp_path / "tasks.sqlite3").exists()


def test_task_event_to_dict():
    event = TaskEvent(
        seq=3,
        task_id="task-1",
        timestamp="2026-01-01T00:00:00.000Z",
        event_type="task_finished",
        payload={"status": "completed"},
    )

    assert event.to_dict() == {
        "seq": 3,
        "task_id": "task-1",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "event": "task_finished",
        "payload": {"status": "completed"},
    }
