"""Waking check stage - determines if the bot should respond.

Checks: private chat (always wake), @bot in group, wake word match.
"""

from collections.abc import AsyncGenerator

from core import logger
from core.platform.message import MessageEvent, At
from core.pipeline.stage import Stage, register_stage


@register_stage
class WakingCheckStage(Stage):
    async def initialize(self, ctx: dict) -> None:
        self.wake_words: list[str] = ctx.get("wake_words", [])
        self.self_id: str = ctx.get("self_id", "")

    async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
        # WebChat messages always wake (they're explicit user interactions)
        if event.platform_name == "webchat":
            event.is_wake = True
            yield
            return

        if event.is_private():
            event.is_wake = True
            yield
            return

        # Check if bot is @-mentioned. OneBot provides the bot id on each event.
        self_id = event.self_id or self.self_id
        for comp in event.message_chain:
            if isinstance(comp, At):
                if (self_id and comp.qq == self_id) or comp.qq == "all":
                    event.is_wake = True
                    break

        # Check wake words
        if not event.is_wake and self.wake_words:
            msg_lower = event.message_str.lower()
            for word in self.wake_words:
                if word.lower() in msg_lower:
                    event.is_wake = True
                    break

        if event.is_wake:
            yield
        else:
            logger.debug(f"Message not waking bot, skipping: {event.message_str[:50]}")
