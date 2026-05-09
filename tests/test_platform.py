import asyncio

import pytest

from core.platform.base import PlatformStatus
from core.platform.message import (
    At,
    File,
    Image,
    MessageEvent,
    MessageType,
    Plain,
    Sender,
    display_session_id,
    normalize_session_id,
)
from core.platform.webchat import WebChatAdapter

# ── MessageEvent ────────────────────────────────────────────────────


def test_message_event_tracks_result_text_and_chain():
    event = MessageEvent()

    event.set_result("hello")
    assert event.get_result_text() == "hello"
    assert event.get_result_chain() == [Plain(text="hello")]

    event.set_result_chain([Plain(text="a"), Image(url="x"), Plain(text="b")])
    assert event.get_result_text() == "ab"
    assert event.get_result_chain()[1] == Image(url="x")


def test_message_event_origin_sender_and_stop_state():
    event = MessageEvent(
        message_type=MessageType.GROUP_MESSAGE,
        sender=Sender(user_id="42", nickname="Tester"),
        session_id="group-1",
        platform_name="onebot11",
    )

    assert event.unified_msg_origin == "onebot11:group:group-1"
    assert event.is_private() is False
    assert event.get_sender_name() == "Tester"
    assert event.is_stopped() is False

    event.stop()

    assert event.is_stopped() is True


def test_message_outline_combines_plain_and_structured_components():
    event = MessageEvent(
        message_chain=[
            At(qq="123", name="atri"),
            Plain(text="hello"),
            Image(url="https://example.test/a.png"),
            File(name="report.txt"),
        ]
    )

    outline = event.get_message_outline()

    assert "[@atri]" in outline
    assert "hello" in outline
    assert "report.txt" in outline


def test_session_id_display_normalization_round_trip_for_webchat_ids():
    assert normalize_session_id("abc") == "webchat:friend:abc"
    assert normalize_session_id("onebot11:group:42") == "onebot11:group:42"
    assert display_session_id("webchat:friend:abc") == "abc"
    assert display_session_id("onebot11:group:42") == "onebot11:group:42"


# ── WebChatAdapter ──────────────────────────────────────────────────


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
async def test_webchat_adapter_create_event_supports_image_attachments():
    queue = asyncio.Queue()
    adapter = WebChatAdapter(queue)

    event, _future = adapter.create_event(
        "",
        "session-1",
        images=[
            {
                "url": "data:image/png;base64,aGVsbG8=",
                "file": "screen.png",
                "mime_type": "image/png",
                "size": 5,
            }
        ],
    )

    assert await queue.get() is event
    assert event.message_str == "[1 image attachment(s)]"
    assert event.message_chain == [
        Image(
            url="data:image/png;base64,aGVsbG8=",
            file="screen.png",
            mime_type="image/png",
            size=5,
        )
    ]


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
