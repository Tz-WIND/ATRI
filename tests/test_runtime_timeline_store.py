import gc
import sqlite3
import weakref

import pytest

from core.runtime.timeline import RuntimeEvent, RuntimeTimelineStore, summarize_text


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
