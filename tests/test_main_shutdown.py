import asyncio
import logging

import main
import pytest


class StopDependentLifecycle:
    def __init__(self):
        self.stop_called = asyncio.Event()

    async def start(self):
        try:
            await self.stop_called.wait()
        except asyncio.CancelledError:
            await self.stop_called.wait()

    async def stop(self):
        self.stop_called.set()


@pytest.mark.asyncio
async def test_run_lifecycle_until_shutdown_calls_stop_before_waiting_for_start_cancellation():
    lifecycle = StopDependentLifecycle()
    shutdown_triggered = asyncio.Event()
    shutdown_triggered.set()

    await asyncio.wait_for(
        main._run_lifecycle_until_shutdown(
            lifecycle,
            shutdown_triggered,
            logging.getLogger("atri.test"),
        ),
        timeout=1,
    )

    assert lifecycle.stop_called.is_set() is True
