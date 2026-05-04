"""Pre-processing stage - cleans and normalizes the message before processing."""

from collections.abc import AsyncGenerator

from core import logger
from core.platform.message import MessageEvent, At, Plain
from core.pipeline.stage import Stage, register_stage


@register_stage
class PreProcessStage(Stage):
    async def initialize(self, ctx: dict) -> None:
        self.strip_at_prefix = ctx.get("strip_at_prefix", True)

    async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
        if self.strip_at_prefix:
            # Remove leading @bot from the message text for cleaner Agent input
            cleaned_parts = []
            skip_next_space = False
            for comp in event.message_chain:
                if isinstance(comp, At):
                    skip_next_space = True
                    continue
                if isinstance(comp, Plain) and skip_next_space:
                    text = comp.text.lstrip()
                    if text:
                        cleaned_parts.append(text)
                    skip_next_space = False
                elif isinstance(comp, Plain):
                    cleaned_parts.append(comp.text)

            if cleaned_parts:
                event.message_str = " ".join(cleaned_parts).strip()

        logger.debug(f"Preprocessed message: {event.message_str[:80]}")
        yield
