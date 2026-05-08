import asyncio

import pytest

from core.platform.base import PlatformStatus
from core.platform.message import Plain
from core.platform.webchat import WebChatAdapter


@pytest.mark.asyncio
async def test_webchat_adapter_create_event_commits_to_queue_and_resolves_text_response():
    queue = asyncio.Queue()
    adapter = WebChatAdapter(queue)

    event, future = adapter.create_event("hello", "session-1")

    assert adapter.status == PlatformStatus.RUNNING
    assert await queue.get() is event
    assert event.message_str == "hello"
    assert event.session_id == "session-1"
    assert event.platform_name == "webchat"
    assert future.done() is False

    await adapter.send_message(event, "response")

    assert future.result() == {"text": "response", "chain": None}


@pytest.mark.asyncio
async def test_webchat_adapter_resolves_chain_response_and_cancels_pending_on_terminate():
    queue = asyncio.Queue()
    adapter = WebChatAdapter(queue)
    event, future = adapter.create_event("hello", "session-1")

    await adapter.send_message_chain(event, [Plain(text="a"), Plain(text="b")])

    assert future.result() == {"text": "a\nb", "chain": [Plain(text="a"), Plain(text="b")]}

    _event, pending = adapter.create_event("second", "session-1")
    await adapter.terminate()

    assert pending.cancelled() is True
    assert adapter.status == PlatformStatus.STOPPED
