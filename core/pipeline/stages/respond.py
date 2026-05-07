"""Respond stage - sends the final result back to the originating platform.

Routes responses to the correct platform adapter (OneBot11 or WebChat)
based on the event's platform_name.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from core import logger
from core.pipeline.stage import Stage, register_stage
from core.platform.message import MessageEvent

if TYPE_CHECKING:
    from core.platform.base import Platform


@register_stage
class RespondStage(Stage):
    async def initialize(self, ctx: dict) -> None:
        self.platforms: dict[str, Platform] = ctx.get("platforms", {})

    async def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
        result_chain = event.get_result_chain()
        result_text = event.get_result_text()

        logger.info(f"RespondStage: text={len(result_text)}chars chain={len(result_chain)}items platform={event.platform_name}")  # noqa: E501

        if not result_text and not result_chain:
            logger.info("RespondStage: no result to send.")
            return

        platform = self.platforms.get(event.platform_name)
        if not platform:
            logger.warning(f"No platform adapter for '{event.platform_name}', cannot respond.")
            return

        try:
            if result_chain:
                await platform.send_message_chain(event, result_chain)
            elif result_text:
                if len(result_text) > 4000:
                    chunks = _split_message(result_text, 4000)
                    for chunk in chunks:
                        await platform.send_message(event, chunk)
                else:
                    await platform.send_message(event, result_text)
            logger.info(f"RespondStage: response sent via {event.platform_name} ({len(result_text)} chars)")  # noqa: E501
        except Exception as e:
            logger.exception(f"Failed to send response via {event.platform_name}: {e}")

        yield


def _split_message(text: str, max_len: int) -> list[str]:
    """Split a long message at paragraph boundaries."""
    chunks = []
    while len(text) > max_len:
        split_pos = text.rfind("\n\n", 0, max_len)
        if split_pos == -1:
            split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()
    if text:
        chunks.append(text)
    return chunks
