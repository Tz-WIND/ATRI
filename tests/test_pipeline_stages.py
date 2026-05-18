from collections.abc import AsyncGenerator
from typing import Any

import pytest

from core.agent.llm import LLMResponse
from core.pipeline.scheduler import PipelineScheduler
from core.pipeline.stage import Stage
from core.pipeline.stages.preprocess import PreProcessStage
from core.pipeline.stages.process import ProcessStage, _event_allows_high_privilege_tools
from core.pipeline.stages.respond import _split_message
from core.pipeline.stages.waking import WakingCheckStage
from core.platform.message import At, Image, MessageEvent, MessageType, Plain


async def _consume(stage: Stage, event: MessageEvent) -> list[None]:
    yielded: list[None] = []
    async for item in stage.process(event):
        yielded.append(item)
    return yielded


@pytest.mark.asyncio
async def test_waking_stage_wakes_webchat_private_at_and_keyword_messages():
    stage = WakingCheckStage()
    await stage.initialize({"wake_words": ["atri"], "self_id": "bot"})

    webchat = MessageEvent(platform_name="webchat")
    private = MessageEvent(message_type=MessageType.FRIEND_MESSAGE, platform_name="onebot11")
    mentioned = MessageEvent(
        message_type=MessageType.GROUP_MESSAGE,
        message_chain=[At(qq="bot")],
        platform_name="onebot11",
    )
    keyword = MessageEvent(
        message_type=MessageType.GROUP_MESSAGE,
        message_str="hey ATRI",
        platform_name="onebot11",
    )

    for event in (webchat, private, mentioned, keyword):
        assert await _consume(stage, event) == [None]
        assert event.is_wake is True


@pytest.mark.asyncio
async def test_waking_stage_does_not_yield_for_unrelated_group_message():
    stage = WakingCheckStage()
    await stage.initialize({"wake_words": ["atri"], "self_id": "bot"})
    event = MessageEvent(
        message_type=MessageType.GROUP_MESSAGE,
        message_str="nothing to see",
        message_chain=[At(qq="someone-else")],
        platform_name="onebot11",
    )

    assert await _consume(stage, event) == []
    assert event.is_wake is False


@pytest.mark.asyncio
async def test_preprocess_stage_strips_leading_at_components():
    stage = PreProcessStage()
    await stage.initialize({"strip_at_prefix": True})
    event = MessageEvent(message_chain=[At(qq="bot"), Plain(text="   run tests")])

    assert await _consume(stage, event) == [None]
    assert event.message_str == "run tests"


@pytest.mark.asyncio
async def test_preprocess_stage_can_leave_message_unchanged():
    stage = PreProcessStage()
    await stage.initialize({"strip_at_prefix": False})
    event = MessageEvent(message_str="@bot run tests", message_chain=[At(qq="bot")])

    assert await _consume(stage, event) == [None]
    assert event.message_str == "@bot run tests"


def test_split_message_prefers_paragraph_then_line_boundaries():
    assert _split_message("abc", 10) == ["abc"]
    assert _split_message("aa\n\nbbcc", 4) == ["aa", "bbcc"]
    assert _split_message("aa\nbbcc", 4) == ["aa", "bbcc"]
    assert _split_message("abcdef", 3) == ["abc", "def"]


def test_process_stage_allows_high_privilege_tools_for_onebot_admin_only():
    webchat = MessageEvent(platform_name="webchat")
    onebot_admin = MessageEvent(platform_name="onebot11")
    onebot_admin._extras["onebot11_is_admin"] = True
    onebot_normal = MessageEvent(platform_name="onebot11")

    assert _event_allows_high_privilege_tools(webchat) is True
    assert _event_allows_high_privilege_tools(onebot_admin) is True
    assert _event_allows_high_privilege_tools(onebot_normal) is False


@pytest.mark.asyncio
async def test_process_stage_routes_images_through_transcription_when_enabled(monkeypatch):
    stage = ProcessStage()
    stage.image_transcription = {"enabled": True}
    event = MessageEvent(
        message_str="what is this?",
        message_chain=[
            Plain(text="what is this?"),
            Image(url="data:image/png;base64,aGVsbG8=", file="screen.png"),
        ],
    )

    def fake_transcribe(received_event, images):
        assert received_event is event
        assert len(images) == 1
        return "a screenshot with an error"

    monkeypatch.setattr(stage, "_transcribe_event_images", fake_transcribe)

    content = await stage._event_content_for_agent(event)

    assert content == "what is this?\n\n[Image transcription]\na screenshot with an error"


def test_process_stage_image_transcription_uses_non_stream_llm(monkeypatch):
    captured: dict[str, Any] = {}

    class FakeLLM:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.api_format = kwargs["api_format"]

        def chat(self, messages, stream=True):
            captured["messages"] = messages
            captured["stream"] = stream
            return LLMResponse(content="screen description")

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr("core.pipeline.stages.process.LLM", FakeLLM)

    stage = ProcessStage()
    stage.image_transcription = {
        "enabled": True,
        "model": "vision-model",
        "api_key": "sk-test",
        "base_url": "https://example.test/v1",
        "api_format": "openai",
        "max_tokens": 512,
        "temperature": 0,
        "prompt": "describe it",
    }
    image = Image(url="data:image/png;base64,aGVsbG8=", file="screen.png")
    event = MessageEvent(
        message_str="what is this?",
        message_chain=[
            Plain(text="what is this?"),
            image,
        ],
    )

    transcription = stage._transcribe_event_images(event, [image])

    assert image.file == "screen.png"
    assert transcription == "screen description"
    assert captured["stream"] is False
    assert captured["closed"] is True
    assert captured["init"]["model"] == "vision-model"
    assert captured["messages"][0]["content"][0]["text"] == "describe it"


@pytest.mark.asyncio
async def test_process_stage_keeps_multimodal_content_when_transcription_disabled():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    event = MessageEvent(
        message_str="look",
        message_chain=[
            Plain(text="look"),
            Image(url="data:image/png;base64,aGVsbG8=", file="screen.png"),
        ],
    )

    content = await stage._event_content_for_agent(event)

    assert content == [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
    ]


@pytest.mark.asyncio
async def test_process_stage_prepends_recent_group_messages_to_onebot_group_input():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    event = MessageEvent(
        message_str="help me check",
        message_chain=[Plain(text="help me check")],
        message_type=MessageType.GROUP_MESSAGE,
        platform_name="onebot11",
    )
    event._extras["recent_group_messages"] = [
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

    content = await stage._event_content_for_agent(event)

    assert content == (
        "[Recent group messages before this request]\n"
        "- Alice (1001): build failed at asio\n"
        "- Bob (1002): maybe enable the feature flag\n\n"
        "[Current request]\n"
        "help me check"
    )


@pytest.mark.asyncio
async def test_pipeline_scheduler_preserves_onion_stage_order():
    calls = []

    class OuterStage(Stage):
        async def initialize(self, ctx: dict) -> None:
            return None

        async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
            calls.append("outer-before")
            yield
            calls.append("outer-after")

    class InnerStage(Stage):
        async def initialize(self, ctx: dict) -> None:
            return None

        async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
            calls.append("inner-before")
            yield
            calls.append("inner-after")

    scheduler = PipelineScheduler({})
    scheduler.stages = [OuterStage(), InnerStage()]

    await scheduler._process_stages(MessageEvent(), 0)

    assert calls == ["outer-before", "inner-before", "inner-after", "outer-after"]


@pytest.mark.asyncio
async def test_pipeline_scheduler_stops_before_downstream_stages():
    calls = []

    class StopStage(Stage):
        async def initialize(self, ctx: dict) -> None:
            return None

        async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
            calls.append("stop")
            event.stop()
            yield

    class DownstreamStage(Stage):
        async def initialize(self, ctx: dict) -> None:
            return None

        async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
            calls.append("downstream")
            yield

    scheduler = PipelineScheduler({})
    scheduler.stages = [StopStage(), DownstreamStage()]

    await scheduler._process_stages(MessageEvent(), 0)

    assert calls == ["stop"]
