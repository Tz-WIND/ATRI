import asyncio

import pytest

from core.event_bus import EventBus
from core.pipeline.scheduler import PipelineScheduler


class _FakeEvent:
    platform_name = "test"
    sender = type("Sender", (), {"user_id": "user"})()

    def get_sender_name(self) -> str:
        return "tester"

    def get_message_outline(self) -> str:
        return "hello"


class _SlowScheduler:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(self, _event) -> None:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


@pytest.mark.asyncio
async def test_event_bus_shutdown_cancels_running_pipeline():
    queue: asyncio.Queue = asyncio.Queue()
    scheduler = _SlowScheduler()
    bus = EventBus(queue, scheduler)  # type: ignore[arg-type]

    dispatch_task = asyncio.create_task(bus.dispatch())
    await queue.put(_FakeEvent())
    await asyncio.wait_for(scheduler.started.wait(), timeout=1)

    await bus.shutdown(grace_period=0.2)

    await asyncio.wait_for(scheduler.cancelled.wait(), timeout=1)
    await asyncio.wait_for(dispatch_task, timeout=1)
    assert dispatch_task.done()


@pytest.mark.asyncio
async def test_event_bus_shutdown_discards_pending_events():
    queue: asyncio.Queue = asyncio.Queue()
    scheduler = PipelineScheduler({"workspace": "."})
    bus = EventBus(queue, scheduler)

    await queue.put(object())
    await queue.put(object())
    bus._discard_pending_events()

    assert queue.empty()
