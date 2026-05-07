"""Base pipeline stage - inspired by AstrBot's onion-model pipeline."""

from __future__ import annotations

import abc
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.platform.message import MessageEvent


registered_stages: list[type[Stage]] = []


def register_stage(cls):
    """Decorator to register a pipeline stage implementation."""
    registered_stages.append(cls)
    return cls


class Stage(abc.ABC):
    """A single stage in the message processing pipeline."""

    @abc.abstractmethod
    async def initialize(self, ctx: dict) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def process(self, event: MessageEvent) -> AsyncGenerator[None, None]:
        """Process an event.

        Implementations should be ``async def`` and use ``yield`` to act as an
        onion-model wrapper (code before yield runs before downstream stages,
        code after yield runs after).  To stop pipeline propagation, simply
        return without yielding.
        """
        raise NotImplementedError
