"""Event bus - dispatches message events from platforms to the pipeline scheduler."""

import asyncio
from asyncio import Queue

from core import logger
from core.pipeline.scheduler import PipelineScheduler
from core.platform.message import MessageEvent


class EventBus:
    def __init__(self, event_queue: Queue, scheduler: PipelineScheduler):
        self.event_queue = event_queue
        self.scheduler = scheduler
        self._running_tasks: set[asyncio.Task] = set()

    async def dispatch(self) -> None:
        """Infinite loop: pull events from queue and dispatch to pipeline."""
        logger.info("EventBus started, waiting for events...")
        while True:
            event: MessageEvent = await self.event_queue.get()
            self._log_event(event)
            task = asyncio.create_task(self._safe_dispatch(event))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

    async def _safe_dispatch(self, event: MessageEvent) -> None:
        """Wrap pipeline execution so exceptions don't kill the task silently."""
        try:
            await self.scheduler.execute(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Unhandled error in pipeline for event: {e}")

    @staticmethod
    def _log_event(event: MessageEvent) -> None:
        sender = event.get_sender_name() or event.sender.user_id
        logger.info(f"[{event.platform_name}] {sender}: {event.get_message_outline()[:80]}")
