import ast
import asyncio
import inspect
import textwrap
from types import SimpleNamespace

import pytest

from core.pipeline.stages.process import ProcessStage, _RuntimeTurnRecorder
from core.platform.message import MessageEvent
from core.runtime import RuntimeTimelineStore


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
