import ast
import asyncio
import gc
import inspect
import sqlite3
import textwrap
import weakref
from types import SimpleNamespace

import pytest

from core.pipeline.stages.process import ProcessStage, _RuntimeTurnRecorder
from core.platform.message import MessageEvent
from core.runtime.timeline import RuntimeEvent, RuntimeTimelineStore, summarize_text


# ── RuntimeTimelineStore ────────────────────────────────────────────


def test_runtime_timeline_thread_turn_item_event_lifecycle(tmp_path):
    store = RuntimeTimelineStore(tmp_path / "runtime")
    try:
        thread = store.ensure_thread(
            "thread-1",
            model="gpt-test",
            workspace=str(tmp_path),
            title="First thread",
            metadata={"source": "test"},
        )
        turn_id = store.start_turn(
            "thread-1",
            input_text="hello " * 40,
            model="gpt-test",
            workspace=str(tmp_path),
            metadata={"turn": 1},
        )
        item_id = store.create_item(
            "thread-1",
            turn_id,
            kind="tool",
            summary="read file",
            metadata={"path": "a.txt"},
        )
        store.finish_item(item_id, detail="ok", metadata={"duration": 12})
        event = store.append_event(
            "thread-1",
            event_type="tool_end",
            payload={"ok": True},
            turn_id=turn_id,
            item_id=item_id,
        )
        store.finish_turn(turn_id)

        detail = store.thread_detail("thread-1")

        assert thread["metadata"] == {"source": "test"}
        assert detail is not None
        assert detail["thread"]["latest_turn_id"] == turn_id
        assert detail["latest_seq"] == event.seq
        assert detail["turns"][0]["status"] == "completed"
        assert detail["turns"][0]["input_summary"].endswith("...")
        assert detail["items"][0]["status"] == "completed"
        assert detail["items"][0]["detail"] == "ok"
        assert detail["items"][0]["metadata"] == {"path": "a.txt", "duration": 12}
        assert store.events_since(thread_id="thread-1", since_seq=0, limit=1) == [event]
        assert store.events_since(thread_id="thread-1", since_seq=event.seq) == []
        assert store.latest_seq("thread-1") == event.seq
    finally:
        store.close()


def test_runtime_timeline_delete_thread_cascades_events(tmp_path):
    store = RuntimeTimelineStore(tmp_path / "runtime")
    try:
        turn_id = store.start_turn("thread-1", input_text="hi", model="m", workspace=".")
        store.append_event("thread-1", event_type="response_delta", payload={}, turn_id=turn_id)

        assert store.delete_thread("thread-1") is True
        assert store.delete_thread("thread-1") is False
        assert store.get_thread("thread-1") is None
        assert store.events_since(thread_id="thread-1") == []
    finally:
        store.close()


def test_runtime_timeline_limits_event_replay_and_handles_bad_json(tmp_path):
    store = RuntimeTimelineStore(tmp_path / "runtime")
    try:
        store.ensure_thread("thread-1", model="m", workspace=".")
        first = store.append_event("thread-1", event_type="first", payload={"n": 1})
        second = store.append_event("thread-1", event_type="second", payload={"n": 2})

        assert store.events_since(limit=0) == []
        assert store.events_since(since_seq="not-an-int") == [first, second]

        with store._lock, store._conn:
            store._conn.execute(
                """
                INSERT INTO events(timestamp, thread_id, type, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                ("2026-01-01T00:00:00.000Z", "thread-1", "bad_json", "{"),
            )
        assert store.events_since(thread_id="thread-1")[-1].payload == {}
    finally:
        store.close()


def test_runtime_event_wire_payload_adds_metadata_without_mutating_payload():
    event = RuntimeEvent(
        seq=7,
        timestamp="2026-01-01T00:00:00.000Z",
        thread_id="thread-1",
        turn_id="turn-1",
        item_id="item-1",
        event_type="response_delta",
        payload={"delta": "hi"},
    )

    wire = event.to_wire_payload()

    assert wire == {
        "delta": "hi",
        "type": "response_delta",
        "runtime_seq": 7,
        "thread_id": "thread-1",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "turn_id": "turn-1",
        "item_id": "item-1",
    }
    assert event.payload == {"delta": "hi"}
    assert event.to_dict()["event"] == "response_delta"


def test_runtime_timeline_rejects_newer_schema_version(tmp_path):
    db_path = tmp_path / "timeline.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE runtime_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO runtime_meta(key, value) VALUES ('schema_version', '999')")
        conn.commit()
    finally:
        conn.close()

    try:
        RuntimeTimelineStore(db_path)
    except RuntimeError as exc:
        assert "newer than supported" in str(exc)
    else:
        raise AssertionError("newer schema version was accepted")


def test_runtime_timeline_finalizer_closes_unclosed_connection(tmp_path):
    store = RuntimeTimelineStore(tmp_path / "runtime")
    conn = store._conn
    store_ref = weakref.ref(store)

    assert store._connection_finalizer.alive

    del store
    gc.collect()

    assert store_ref() is None
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        conn.execute("SELECT 1")


def test_summarize_text_collapses_whitespace_and_truncates():
    assert summarize_text("  hello\n   world  ") == "hello world"
    assert summarize_text("abcdef", limit=4) == "abc..."


# ── RuntimeTurnRecorder ─────────────────────────────────────────────


def test_process_locked_has_no_nested_callback_functions():
    source = textwrap.dedent(inspect.getsource(ProcessStage._process_locked))
    tree = ast.parse(source)
    nested = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name != "_process_locked"
    ]

    assert nested == []


@pytest.mark.asyncio
async def test_runtime_turn_recorder_persists_and_broadcasts_turn_events(tmp_path):
    sent = []

    async def broadcast(data):
        sent.append(data)

    stage = SimpleNamespace(
        runtime_store=RuntimeTimelineStore(tmp_path / "runtime"),
        workspace=str(tmp_path),
        broadcast_fn=broadcast,
        _loop=asyncio.get_running_loop(),
    )
    event = MessageEvent(
        message_str="hello runtime",
        platform_name="webchat",
        session_id="test",
    )
    recorder = _RuntimeTurnRecorder(
        stage,
        event,
        event.unified_msg_origin,
        "test-model",
    )

    recorder.record_turn_started()
    recorder.on_thinking("think")
    recorder.on_token("hi")
    recorder.on_tool_start("tc_1", "read_file", {"path": "a.txt"})
    recorder.on_tool_end("tc_1", "read_file", {"path": "a.txt"}, "ok")
    await recorder.drain_pending_broadcasts()
    await recorder.finish_success("hi")

    broadcast_types = [item["type"] for item in sent]
    assert broadcast_types == [
        "thinking_delta",
        "thinking_done",
        "response_start",
        "response_delta",
        "tool_start",
        "tool_end",
        "response_done",
    ]

    events = stage.runtime_store.events_since(thread_id=event.unified_msg_origin)
    event_types = [event.event_type for event in events]
    assert event_types == [
        "turn_started",
        "user_message",
        "thinking_delta",
        "thinking_done",
        "response_start",
        "response_delta",
        "tool_start",
        "tool_end",
        "response_done",
        "turn_completed",
    ]
    assert [event.seq for event in events] == sorted(event.seq for event in events)
