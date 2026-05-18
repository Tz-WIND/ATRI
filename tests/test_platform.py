import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from core.platform.onebot11 import OneBot11Adapter
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


def _onebot_group_event(user_id: str, nickname: str, message: list[dict]):
    return SimpleNamespace(
        sender={"user_id": user_id, "nickname": nickname},
        message_type="group",
        message=message,
        group_id=42,
        self_id=999,
    )


def _onebot_private_event(user_id: str, nickname: str, message: list[dict]):
    return SimpleNamespace(
        sender={"user_id": user_id, "nickname": nickname},
        message_type="private",
        message=message,
        self_id=999,
    )


@pytest.mark.asyncio
async def test_onebot11_adapter_attaches_recent_group_messages_before_current_request():
    queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
    adapter = OneBot11Adapter(
        {
            "group_recent_messages": {
                "enabled": True,
                "max_messages": 2,
            }
        },
        queue,
    )

    await adapter._convert_message(
        _onebot_group_event(
            "1001",
            "Alice",
            [{"type": "text", "data": {"text": "build failed at asio"}}],
        )
    )
    await adapter._convert_message(
        _onebot_group_event(
            "1002",
            "Bob",
            [{"type": "text", "data": {"text": "maybe enable the feature flag"}}],
        )
    )
    event = await adapter._convert_message(
        _onebot_group_event(
            "1003",
            "Carol",
            [
                {"type": "at", "data": {"qq": "999"}},
                {"type": "text", "data": {"text": " help me check"}},
            ],
        )
    )

    assert event is not None
    assert event._extras["recent_group_messages"] == [
        {
            "user_id": "1001",
            "nickname": "Alice",
            "text": "build failed at asio",
        },
        {
            "user_id": "1002",
            "nickname": "Bob",
            "text": "maybe enable the feature flag",
        },
    ]


@pytest.mark.asyncio
async def test_onebot11_adapter_rejects_friend_requests():
    queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
    adapter = OneBot11Adapter({}, queue)
    adapter.bot.call_action = AsyncMock()

    await adapter.bot._handle_event(
        {
            "post_type": "request",
            "request_type": "friend",
            "self_id": 999,
            "user_id": 1001,
            "comment": "please add me",
            "flag": "friend-request-flag",
        }
    )

    adapter.bot.call_action.assert_awaited_once_with(
        action="set_friend_add_request",
        flag="friend-request-flag",
        approve=False,
    )


@pytest.mark.asyncio
async def test_onebot11_adapter_ignores_messages_outside_whitelist():
    queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
    adapter = OneBot11Adapter(
        {
            "whitelist": {
                "private_user_ids": ["1001"],
                "group_ids": ["42"],
            }
        },
        queue,
    )

    private_allowed = await adapter._convert_message(
        _onebot_private_event(
            "1001",
            "Alice",
            [{"type": "text", "data": {"text": "hello"}}],
        )
    )
    private_blocked = await adapter._convert_message(
        _onebot_private_event(
            "1002",
            "Bob",
            [{"type": "text", "data": {"text": "hello"}}],
        )
    )
    group_allowed = await adapter._convert_message(
        _onebot_group_event(
            "1003",
            "Carol",
            [{"type": "text", "data": {"text": "hello"}}],
        )
    )
    group_blocked = await adapter._convert_message(
        SimpleNamespace(
            sender={"user_id": "1004", "nickname": "Dave"},
            message_type="group",
            message=[{"type": "text", "data": {"text": "hello"}}],
            group_id=43,
            self_id=999,
        )
    )

    assert private_allowed is not None
    assert private_allowed.message_type == MessageType.FRIEND_MESSAGE
    assert private_blocked is None
    assert group_allowed is not None
    assert group_allowed.message_type == MessageType.GROUP_MESSAGE
    assert group_blocked is None


@pytest.mark.asyncio
async def test_onebot11_adapter_marks_admin_messages():
    queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
    adapter = OneBot11Adapter({"admin_user_ids": ["1001"]}, queue)

    admin_event = await adapter._convert_message(
        _onebot_private_event(
            "1001",
            "Alice",
            [{"type": "text", "data": {"text": "run tests"}}],
        )
    )
    normal_event = await adapter._convert_message(
        _onebot_private_event(
            "1002",
            "Bob",
            [{"type": "text", "data": {"text": "run tests"}}],
        )
    )

    assert admin_event is not None
    assert admin_event._extras["onebot11_is_admin"] is True
    assert normal_event is not None
    assert normal_event._extras["onebot11_is_admin"] is False


# ── WebChatAdapter ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webchat_adapter_create_event_commits_to_queue_and_resolves_text_response():
    queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
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
    queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
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
    queue: asyncio.Queue[MessageEvent] = asyncio.Queue()
    adapter = WebChatAdapter(queue)
    event, future = adapter.create_event("hello", "session-1")

    await adapter.send_message_chain(event, [Plain(text="a"), Plain(text="b")])

    assert future.result() == {"text": "a\nb", "chain": [Plain(text="a"), Plain(text="b")]}

    _event, pending = adapter.create_event("second", "session-1")
    await adapter.terminate()

    assert pending.cancelled() is True
    assert adapter.status == PlatformStatus.STOPPED
