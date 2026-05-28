"""ATRI core lifecycle manager.

Orchestrates initialization, startup, and shutdown of all components:
Platform adapters (OneBot11 + WebChat), Pipeline, EventBus, Plugin Manager, Dashboard.
"""

import asyncio
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
from core.platform.daw_agent import DawAgentAdapter
from core.platform.onebot11 import OneBot11Adapter
from core.platform.webchat import WebChatAdapter
from core.plugin.manager import PluginManager
from core.utils import atomic_write_text

DEFAULT_CONFIG_PATH = "config.yaml"


SHUTDOWN_GRACE_PERIOD = 5.0  # seconds to wait for tasks to finish


class Lifecycle:
    def __init__(self, config_path: str = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.config = self._load_config()
        self.start_time: float = 0
        self.onebot11: OneBot11Adapter | None = None
        self.webchat: WebChatAdapter | None = None
        self.daw_agent: DawAgentAdapter | None = None
        self.process_stage: Any = None
        self.knowledge_manager: Any = None
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

        from core.tools.novelai_image import set_novelai_config
        from core.tools.web_search import set_tavily_key

        set_tavily_key(config.get("tavily_api_key", "") or None)
        set_novelai_config(config.get("novelai", {}))
        return config

    def save_config(self, config: dict | None = None):
        if config is None:
            config = self.config
        cfg_path = Path(self.config_path)
        payload = yaml.dump(config, default_flow_style=False, allow_unicode=True)
        atomic_write_text(
            cfg_path,
            payload,
            prefix=".config_",
        )

    async def initialize(self) -> None:
        logger.info("ATRI Agent Framework starting...")

        ws_path = Path(self.config["workspace"])
        ws_path.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

        self.event_queue: Queue = Queue()

        plugins_dir = self.config.get("plugins_dir", "plugins")
        self.plugin_manager = PluginManager(plugins_dir)
        await self.plugin_manager.initialize(self.config)

        from core.knowledge import KnowledgeBaseManager

        self.knowledge_manager = KnowledgeBaseManager(config=self.config)
        await self.knowledge_manager.initialize()

        # Platform adapters
        platforms: dict = {}

        # WebChat adapter (always enabled when dashboard is on)
        self.webchat = WebChatAdapter(self.event_queue)
        platforms["webchat"] = self.webchat

        # DAW/VST embedded agent adapter (driven by dashboard HTTP/WebView)
        self.daw_agent = DawAgentAdapter(self.event_queue)
        platforms["daw_agent"] = self.daw_agent

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
            "model_provider": self.config.get("model_provider", ""),
            "active_models": self.config.get("active_models", []),
            "embedding_model": self.config.get("embedding_model", ""),
            "embedding_provider": self.config.get("embedding_provider", ""),
            "active_embedding_models": self.config.get("active_embedding_models", []),
            "rerank_model": self.config.get("rerank_model", ""),
            "rerank_provider": self.config.get("rerank_provider", ""),
            "active_rerank_models": self.config.get("active_rerank_models", []),
            "providers": self.config.get("providers", {}),
            "max_tokens": self.config.get("max_tokens", 4096),
            "temperature": self.config.get("temperature", 0.0),
            "max_context_tokens": self.config.get("max_context_tokens", 128000),
            "max_rounds": self.config.get("max_rounds", 50),
            "extra_instructions": self.config.get("extra_instructions", ""),
            "persona": self.config.get("persona", ""),
            "agent_mode": self.config.get("agent_mode", "agent"),
            "skills_root": self.config.get("skills_root", "skills"),
            "skill_search_roots": self.config.get("skill_search_roots", []),
            "skills_config": self.config.get("skills", {}),
            "tavily_api_key": self.config.get("tavily_api_key", ""),
            "novelai": self.config.get("novelai", {}),
            "image_transcription": self.config.get("image_transcription", {}),
            "mcp_servers": self.config.get("mcp_servers", {}),
            "knowledge": self.config.get("knowledge", {}),
            "knowledge_manager": self.knowledge_manager,
            "sessions_dir": self.config.get("sessions_dir"),
            "runtime_dir": self.config.get("runtime_dir"),
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

        # DAW/VST agent adapter (idle loop, driven by dashboard HTTP/WebView)
        if self.daw_agent:
            self._tasks.append(
                asyncio.create_task(self._safe_task(self.daw_agent.run(), "DawAgent"))
            )

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

        await self._start_audio_host()

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

    async def _start_audio_host(self) -> None:
        audio_cfg = self.config.get("audio_host", {})
        if not audio_cfg.get("auto_start", True):
            return

        from core.host import configure_host_manager

        host = configure_host_manager(
            binary_path=audio_cfg.get("binary_path") or None,
            sample_rate=int(audio_cfg.get("sample_rate", 48000) or 48000),
            buffer_size=int(audio_cfg.get("buffer_size", 256) or 256),
            audio_engine=audio_cfg.get("audio_engine") or "default",
            bit_depth=audio_cfg.get("bit_depth") or "f32",
        )
        try:
            await host.start()
        except FileNotFoundError as e:
            logger.warning("Audio host auto-start skipped: %s", e)
            return
        except OSError as e:
            logger.warning("Audio host auto-start failed: %s", e)
            return

        try:
            from dashboard.music import sync_current_project_to_host

            await sync_current_project_to_host(broadcast=False)
        except Exception as e:
            logger.warning("Audio host started, but initial project sync failed: %s", e)

        dashboard = getattr(self, "dashboard", None)
        if dashboard:
            await dashboard.reconcile_audio_streaming_state()

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
        if self.daw_agent:
            await self.daw_agent.terminate()

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
        from core.host import get_host_manager
        from core.tools.mcp import get_mcp_registry

        host = get_host_manager()
        if host.is_running:
            await host.stop()

        get_mcp_registry().close()

        if self.knowledge_manager:
            await self.knowledge_manager.close()

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
