"""Pipeline scheduler - executes stages in order with onion-model support."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from core import logger
from core.platform.message import MessageEvent
from .stage import Stage, registered_stages


STAGES_ORDER = [
    "WakingCheckStage",
    "PreProcessStage",
    "ProcessStage",
    "RespondStage",
]


class PipelineScheduler:
    """Schedules and executes pipeline stages in order."""

    def __init__(self, ctx: dict):
        self.ctx = ctx
        self.stages: list[Stage] = []

    async def initialize(self) -> None:
        ordered = sorted(
            registered_stages,
            key=lambda cls: (
                STAGES_ORDER.index(cls.__name__)
                if cls.__name__ in STAGES_ORDER
                else 999
            ),
        )
        for stage_cls in ordered:
            instance = stage_cls()
            await instance.initialize(self.ctx)
            self.stages.append(instance)
        logger.info(f"Pipeline initialized with {len(self.stages)} stages: "
                     f"{[s.__class__.__name__ for s in self.stages]}")

    async def execute(self, event: MessageEvent) -> None:
        try:
            await self._process_stages(event, 0)
            logger.debug("Pipeline execution complete.")
        except Exception as e:
            logger.exception(f"Pipeline error: {e}")

    async def _process_stages(self, event: MessageEvent, from_stage: int) -> None:
        for i in range(from_stage, len(self.stages)):
            stage = self.stages[i]

            coroutine = stage.process(event)

            if isinstance(coroutine, AsyncGenerator):
                async for _ in coroutine:
                    if event.is_stopped():
                        logger.debug(f"Stage {stage.__class__.__name__} stopped event.")
                        break
                    await self._process_stages(event, i + 1)
                    if event.is_stopped():
                        break
                return  # inner stages already processed via the recursive call
            else:
                await coroutine
                if event.is_stopped():
                    logger.debug(f"Stage {stage.__class__.__name__} stopped event.")
                    break
