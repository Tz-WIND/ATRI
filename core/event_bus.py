"""Event bus - dispatches message events from platforms to the pipeline scheduler."""

import asyncio
from asyncio import Queue

from core import logger
from core.platform.message import MessageEvent
from core.pipeline.scheduler import PipelineScheduler


class EventBus:
    def __init__(self, event_queue: Queue, scheduler: PipelineScheduler):
        self.event_queue = event_queue
        self.scheduler = scheduler

    async def dispatch(self) -> None:
        """Infinite loop: pull events from queue and dispatch to pipeline."""
        logger.info("EventBus started, waiting for events...")
        while True:
            event: MessageEvent = await self.event_queue.get()
            self._log_event(event)
            asyncio.create_task(self.scheduler.execute(event))

    @staticmethod
    def _log_event(event: MessageEvent) -> None:
        sender = event.get_sender_name() or event.sender.user_id
        logger.info(
            f"[{event.platform_name}] {sender}: {event.get_message_outline()[:80]}"
        )
