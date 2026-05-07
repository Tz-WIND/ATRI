"""ATRI core lifecycle manager.

Orchestrates initialization, startup, and shutdown of all components:
Platform adapters (OneBot11 + WebChat), Pipeline, EventBus, Plugin Manager, Dashboard.
"""

import asyncio
import os
import tempfile
import time
import traceback
from asyncio import Queue
from pathlib import Path
from typing import Any

import yaml

from core import logger
from core.config_schema import (
    ConfigValidationError,
    normalize_config,
)
from core.event_bus import EventBus
from core.pipeline.scheduler import PipelineScheduler
from core.pipeline.stages import *  # noqa: F403 - registers stages
from core.platform.onebot11 import OneBot11Adapter
from core.platform.webchat import WebChatAdapter
from core.plugin.manager import PluginManager

DEFAULT_CONFIG_PATH = "config.yaml"


SHUTDOWN_GRACE_PERIOD = 5.0  # seconds to wait for tasks to finish


class Lifecycle:
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.config = self._load_config()
        self.start_time: float = 0
        self.onebot11: OneBot11Adapter | None = None
        self.webchat: WebChatAdapter | None = None
        self.process_stage: Any = None
        self._tasks: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()

    def _load_config(self) -> dict:
        path = Path(self.config_path)
        config_was_empty = True
        if path.exists():
            with open(path, encoding="utf-8") as f:
                loaded_config = yaml.safe_load(f)
            config_was_empty = not bool(loaded_config)
            user_config = loaded_config or {}
        else:
            user_config = {}

        try:
            config, changed = normalize_config(user_config)
        except ConfigValidationError as e:
            raise RuntimeError(f"Invalid config {self.config_path}: {e}") from e

        if changed or config_was_empty:
            self.save_config(config)

        from core.tools.web_search import set_tavily_key

        set_tavily_key(config.get("tavily_api_key", "") or None)
        return config

    def save_config(self, config: dict | None = None):
        if config is None:
            config = self.config
        # Atomic write: write to temp file then rename to avoid corruption on crash
        cfg_path = Path(self.config_path)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(cfg_path.parent), suffix=".tmp", prefix=".config_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, cfg_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def initialize(self) -> None:
        logger.info("ATRI Agent Framework starting...")

        ws_path = Path(self.config["workspace"])
        ws_path.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

        self.event_queue: Queue = Queue()

        plugins_dir = self.config.get("plugins_dir", "plugins")
        self.plugin_manager = PluginManager(plugins_dir)
        await self.plugin_manager.initialize(self.config)

        # Platform adapters
        platforms: dict = {}

        # WebChat adapter (always enabled when dashboard is on)
        self.webchat = WebChatAdapter(self.event_queue)
        platforms["webchat"] = self.webchat

        # OneBot11 adapter
        ob_config = self.config.get("onebot11", {})
        if ob_config.get("enabled", True):
            self.onebot11 = OneBot11Adapter(ob_config, self.event_queue)
            platforms["onebot11"] = self.onebot11
        else:
            self.onebot11 = None

        self.platforms = platforms

        # Pipeline context -- pass all platform adapters so RespondStage can route
        pipeline_ctx = {
            "workspace": str(ws_path.resolve()),  # noqa: ASYNC240
            "model": self.config["model"],
            "api_key": self.config["api_key"],
            "base_url": self.config.get("base_url"),
            "api_format": self.config.get("api_format", "openai"),
            "active_models": self.config.get("active_models", []),
            "providers": self.config.get("providers", {}),
            "max_tokens": self.config.get("max_tokens", 4096),
            "temperature": self.config.get("temperature", 0.0),
            "max_context_tokens": self.config.get("max_context_tokens", 128000),
            "max_rounds": self.config.get("max_rounds", 50),
            "extra_instructions": self.config.get("extra_instructions", ""),
            "persona": self.config.get("persona", ""),
            "skills_root": self.config.get("skills_root", "skills"),
            "skills_config": self.config.get("skills", {}),
            "tavily_api_key": self.config.get("tavily_api_key", ""),
            "sessions_dir": self.config.get("sessions_dir"),
            "wake_words": self.config.get("wake_words", []),
            "self_id": "",
            "platforms": platforms,
        }

        # Pipeline scheduler
        self.scheduler = PipelineScheduler(pipeline_ctx)
        await self.scheduler.initialize()

        # Grab reference to ProcessStage for dashboard direct access
        from core.pipeline.stages.process import ProcessStage

        for stage in self.scheduler.stages:
            if isinstance(stage, ProcessStage):
                self.process_stage = stage
                break

        # Event bus
        self.event_bus = EventBus(self.event_queue, self.scheduler)

        self.start_time = time.time()
        logger.info("Initialization complete.")

    async def start(self) -> None:
        """Start all services."""
        # Event bus
        self._tasks.append(
            asyncio.create_task(self._safe_task(self.event_bus.dispatch(), "EventBus"))
        )

        # WebChat adapter (idle loop, driven by dashboard HTTP)
        if self.webchat:
            self._tasks.append(asyncio.create_task(self._safe_task(self.webchat.run(), "WebChat")))

        # OneBot11 adapter
        if self.onebot11:
            self._tasks.append(
                asyncio.create_task(self._safe_task(self.onebot11.run(), "OneBot11"))
            )

        # Dashboard
        dashboard_cfg = self.config.get("dashboard", {})
        if dashboard_cfg.get("enabled", True):
            from dashboard.server import Dashboard

            self.dashboard = Dashboard(
                self,
                host=dashboard_cfg.get("host", "127.0.0.1"),
                port=dashboard_cfg.get("port", 6185),
            )
            self._tasks.append(
                asyncio.create_task(self._safe_task(self.dashboard.run(), "Dashboard"))
            )

        # Plugin background tasks
        for plugin in self.plugin_manager.plugins:
            for task_func in plugin.get_background_tasks():
                self._tasks.append(
                    asyncio.create_task(
                        self._safe_task(task_func(), f"Plugin-{plugin.metadata.name}")
                    )
                )

        logger.info("ATRI is running. Press Ctrl+C to stop.")
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def cancel_operation(self, session_id: str | None = None) -> bool:
        """Cancel the currently running agent operation (if any).

        If session_id is given, cancels that specific session's agent.
        Otherwise cancels whichever agent is currently active.

        Returns True if an operation was cancelled, False if nothing was active.
        """
        if self.process_stage:
            if session_id:
                return bool(self.process_stage.cancel_session(session_id))
            return bool(self.process_stage.cancel_current())
        return False

    async def stop(self) -> None:
        logger.info("Shutting down...")

        # 1. Signal all components to stop
        self._shutdown_event.set()

        # 2. Notify platform adapters first so they stop accepting new events
        if self.onebot11:
            await self.onebot11.terminate()
        if self.webchat:
            await self.webchat.terminate()

        # 3. Cancel all running background tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # 4. Wait for tasks to finish with a grace period
        if self._tasks:
            _done, pending = await asyncio.wait(self._tasks, timeout=SHUTDOWN_GRACE_PERIOD)
            if pending:
                logger.warning(
                    f"{len(pending)} task(s) did not finish within "
                    f"{SHUTDOWN_GRACE_PERIOD}s grace period, forcing shutdown."
                )
                for task in pending:
                    task.cancel()

        # 5. Terminate plugins and dashboard last
        await self.plugin_manager.terminate()
        if hasattr(self, "dashboard"):
            await self.dashboard.stop()

        logger.info("ATRI stopped.")

    @staticmethod
    async def _safe_task(coro, name: str):
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Task '{name}' crashed: {e}")
            logger.error(traceback.format_exc())
