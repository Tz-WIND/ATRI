"""Event bus - dispatches message events from platforms to the pipeline scheduler."""

import asyncio
import contextlib
from asyncio import Queue

from core import logger
from core.pipeline.scheduler import PipelineScheduler
from core.platform.message import MessageEvent


class EventBus:
    def __init__(self, event_queue: Queue, scheduler: PipelineScheduler):
        self.event_queue = event_queue
        self.scheduler = scheduler
        self._running_tasks: set[asyncio.Task] = set()
        self._shutdown = asyncio.Event()

    async def shutdown(self, grace_period: float = 5.0) -> None:
        """Stop accepting events and wait for in-flight pipeline work to finish."""
        self._shutdown.set()
        discarded = self._discard_pending_events()
        if discarded:
            logger.debug("Discarded %d pending event(s) during shutdown", discarded)
        await self._cancel_running_tasks(grace_period)

    def _discard_pending_events(self) -> int:
        discarded = 0
        while True:
            try:
                self.event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                discarded += 1
        return discarded

    async def _cancel_running_tasks(self, grace_period: float) -> None:
        if not self._running_tasks:
            return

        for task in list(self._running_tasks):
            task.cancel()

        _done, pending = await asyncio.wait(self._running_tasks, timeout=grace_period)
        if pending:
            logger.warning(
                "%d pipeline task(s) did not finish within %.1fs during shutdown",
                len(pending),
                grace_period,
            )
            for task in pending:
                task.cancel()

    async def dispatch(self) -> None:
        """Infinite loop: pull events from queue and dispatch to pipeline."""
        logger.info("EventBus started, waiting for events...")
        while not self._shutdown.is_set():
            get_task = asyncio.create_task(self.event_queue.get())
            shutdown_wait = asyncio.create_task(self._shutdown.wait())
            _done, _pending = await asyncio.wait(
                [get_task, shutdown_wait],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if shutdown_wait in _done:
                get_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await get_task
                break

            shutdown_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await shutdown_wait

            event: MessageEvent = get_task.result()
            self._log_event(event)
            task = asyncio.create_task(self._safe_dispatch(event))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)

        logger.debug("EventBus dispatch loop stopped")

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
