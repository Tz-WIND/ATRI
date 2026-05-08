import json

import pytest

from core.agent.context import TOOL_OUTPUT_COMPRESSED_MARKER, ContextManager, ToolResultStore
from core.agent.session import SessionStore
from core.tools.retrieve_tool_result import RetrieveToolResultTool


def test_session_store_save_load_list_and_delete(tmp_path):
    store = SessionStore(tmp_path)
    messages = [
        {"role": "system", "content": "setup"},
        {"role": "user", "content": "hello session"},
    ]

    assert store.save(messages, "gpt-test", "webchat:friend:user/1", extra={"x": 1}) == (
        "webchat:friend:user/1"
    )

    assert store.load("webchat:friend:user/1") == (messages, "gpt-test")
    listed = store.list_sessions()
    assert listed == [
        {
            "id": "webchat:friend:user/1",
            "model": "gpt-test",
            "saved_at": listed[0]["saved_at"],
            "message_count": 2,
            "preview": "hello session",
        }
    ]
    assert store.delete("webchat:friend:user/1") is True
    assert store.delete("webchat:friend:user/1") is False
    assert store.load("webchat:friend:user/1") is None


def test_session_store_rejects_path_traversal_session_ids(tmp_path):
    store = SessionStore(tmp_path)

    with pytest.raises(ValueError, match="Invalid session ID"):
        store.save([], "model", "../escape")


def test_session_store_ignores_corrupt_session_files(tmp_path):
    store = SessionStore(tmp_path)
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    (tmp_path / "missing-model.json").write_text(json.dumps({"messages": []}), encoding="utf-8")

    assert store.load("bad") is None
    assert store.load("missing-model") is None
    assert store.list_sessions() == [
        {
            "id": "missing-model",
            "model": "?",
            "saved_at": "?",
            "message_count": 0,
            "preview": "",
        }
    ]


def test_tool_result_store_spills_large_outputs_and_retrieves_modes(tmp_path):
    store = ToolResultStore(tmp_path, spill_chars=20, spill_lines=3, head_chars=12)
    output = "\n".join(
        [
            "line one",
            "warning: something happened",
            "line three",
            "error: failure",
            "line five",
        ]
    )

    prepared = store.prepare(tool="grep", tool_call_id="tc_1", args={"pattern": "x"}, output=output)

    assert prepared.stored is not None
    assert prepared.content.startswith(TOOL_OUTPUT_COMPRESSED_MARKER)
    assert prepared.stored.result_id in prepared.content
    assert "warning: something happened" in store.retrieve(prepared.stored.result_id)
    assert store.retrieve(prepared.stored.result_id, mode="head", max_lines=2) == (
        "1\tline one\n2\twarning: something happened"
    )
    assert store.retrieve(prepared.stored.result_id, mode="tail", max_lines=1) == "5\tline five"
    assert (
        store.retrieve(
            prepared.stored.result_id,
            mode="lines",
            start_line=2,
            end_line=3,
        )
        == "2\twarning: something happened\n3\tline three"
    )
    assert store.retrieve(prepared.stored.result_id, mode="query", query="ERROR") == (
        "4\terror: failure"
    )


def test_tool_result_store_rejects_invalid_retrieval_requests(tmp_path):
    store = ToolResultStore(tmp_path)
    record = store.store(tool="read", tool_call_id="tc_1", args={}, output="one\ntwo")

    with pytest.raises(ValueError, match="Invalid tool result id"):
        store.retrieve("../bad")
    with pytest.raises(ValueError, match="start_line is required"):
        store.retrieve(record.result_id, mode="lines")
    with pytest.raises(ValueError, match="query is required"):
        store.retrieve(record.result_id, mode="query")
    with pytest.raises(ValueError, match="mode must be one of"):
        store.retrieve(record.result_id, mode="unknown")


def test_retrieve_tool_result_tool_formats_user_visible_errors(tmp_path):
    tool = RetrieveToolResultTool(str(tmp_path), tool_result_store=ToolResultStore(tmp_path))

    assert tool.execute("../bad").startswith("Error: Invalid tool result id.")
    assert tool.execute("tr_0000000000000000").startswith("Error: Tool result not found")


def test_context_manager_snips_large_multiline_tool_outputs():
    messages = [
        {"role": "tool", "content": "\n".join(f"line {i}" for i in range(20))},
        {"role": "tool", "content": TOOL_OUTPUT_COMPRESSED_MARKER + "\nalready compact"},
        {"role": "assistant", "content": "unchanged"},
    ]
    messages[0]["content"] = messages[0]["content"] * 100

    assert ContextManager._snip_tool_outputs(messages) is True
    assert "snipped to save context" in messages[0]["content"]
    assert messages[1]["content"].endswith("already compact")
