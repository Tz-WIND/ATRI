"""ATRI Agent Framework - Entry point."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.lifecycle import Lifecycle


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

    def trigger_shutdown():
        shutdown_triggered.set()

    # Register handlers for both SIGINT (Ctrl+C) and SIGTERM (Docker/systemd)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, trigger_shutdown)
        except NotImplementedError:
            # Windows does not support loop.add_signal_handler
            signal.signal(sig, lambda signum, frame: trigger_shutdown())

    try:
        start_task = asyncio.create_task(lifecycle.start())
        shutdown_task = asyncio.create_task(shutdown_triggered.wait())

        done, pending = await asyncio.wait(
            [start_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if start_task in pending:
            logger.info("Shutdown signal received, stopping gracefully...")
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
