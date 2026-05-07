"""ATRI Agent Framework - Entry point."""

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.lifecycle import Lifecycle

# Seconds before a second Ctrl+C triggers shutdown instead of another cancel
DOUBLE_PRESS_WINDOW = 3.0


def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger("atri")
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)


async def main():
    setup_logging()
    logger = logging.getLogger("atri")

    lifecycle = Lifecycle()
    await lifecycle.initialize()

    loop = asyncio.get_running_loop()
    shutdown_triggered = asyncio.Event()
    _last_cancel_time = 0.0

    def handle_signal():
        """Smart signal handler: first Ctrl+C cancels current operation,
        a second one within DOUBLE_PRESS_WINDOW triggers full shutdown.
        When nothing is active, Ctrl+C always triggers shutdown.
        """
        nonlocal _last_cancel_time
        now = time.time()

        # If the user double-taps within the window, force shutdown
        if now - _last_cancel_time < DOUBLE_PRESS_WINDOW:
            logger.info("Second interrupt — shutting down...")
            shutdown_triggered.set()
            return

        # Try to cancel the current agent operation
        if lifecycle.cancel_operation():
            _last_cancel_time = now
            logger.info(
                "Operation cancelled. Press Ctrl+C again within %.0fs to force shutdown.",
                DOUBLE_PRESS_WINDOW,
            )
        else:
            # Nothing active — graceful shutdown
            logger.info("Shutdown signal received, stopping gracefully...")
            shutdown_triggered.set()

    # Register handlers for both SIGINT (Ctrl+C) and SIGTERM (Docker/systemd)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Windows does not support loop.add_signal_handler
            signal.signal(sig, lambda signum, frame: handle_signal())

    try:
        start_task = asyncio.create_task(lifecycle.start())
        shutdown_task = asyncio.create_task(shutdown_triggered.wait())

        _done, pending = await asyncio.wait(
            [start_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if start_task in pending:
            logger.info("Shutting down gracefully...")
            start_task.cancel()
            try:
                await start_task
            except asyncio.CancelledError:
                pass
    except asyncio.CancelledError:
        logger.info("Main task cancelled, stopping...")
    finally:
        await lifecycle.stop()

    logger.info("Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBye!")
