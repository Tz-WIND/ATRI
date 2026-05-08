from collections.abc import AsyncGenerator

import pytest

from core.pipeline.scheduler import PipelineScheduler
from core.pipeline.stage import Stage
from core.pipeline.stages.preprocess import PreProcessStage
from core.pipeline.stages.respond import _split_message
from core.pipeline.stages.waking import WakingCheckStage
from core.platform.message import At, MessageEvent, MessageType, Plain


async def _consume(stage: Stage, event: MessageEvent) -> list[None]:
    yielded = []
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
