"""Plugin manager - discovers, loads, and manages plugins.

Scans the plugins/ directory for Python modules containing Plugin subclasses.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import sys
import threading
import traceback
from collections.abc import Callable
from pathlib import Path

from core import logger
from core.platform.message import MessageEvent

from .base import Plugin

DEFAULT_PLUGIN_STARTUP_TIMEOUT = 10.0


class PluginStartupTimeoutError(TimeoutError):
    """Raised when a plugin source exceeds the manager startup deadline."""


class PluginManager:
    def __init__(
        self,
        plugins_dir: str = "plugins",
        startup_timeout: float | None = DEFAULT_PLUGIN_STARTUP_TIMEOUT,
    ):
        self.plugins_dir = Path(plugins_dir)
        self.startup_timeout = startup_timeout
        self._plugins: list[Plugin] = []
        self._ctx: dict = {}

    @property
    def plugins(self) -> list[Plugin]:
        return self._plugins

    async def initialize(self, ctx: dict) -> None:
        """Load all plugins from the plugins directory."""
        self._ctx = ctx
        self.plugins_dir.mkdir(parents=True, exist_ok=True)

        # Add plugins dir to sys.path so we can import from it
        plugins_path = str(self.plugins_dir.resolve())
        if plugins_path not in sys.path:
            sys.path.insert(0, plugins_path)

        for item in sorted(self.plugins_dir.iterdir()):
            if item.is_dir() and (item / "__init__.py").exists():
                await self._load_plugin_package(item.name)
            elif item.is_file() and item.suffix == ".py" and item.name != "__init__.py":
                await self._load_plugin_module(item.stem)

        logger.info(
            f"Loaded {len(self._plugins)} plugins: {[p.metadata.name for p in self._plugins]}"
        )

    async def _load_plugin_package(self, package_name: str) -> None:
        await self._load_plugin_source(package_name, "package")

    async def _load_plugin_module(self, module_name: str) -> None:
        await self._load_plugin_source(module_name, "module")

    async def _load_plugin_source(self, module_name: str, source_kind: str) -> None:
        try:
            plugins = await self._load_plugins_with_timeout(module_name)
            self._plugins.extend(plugins)
        except PluginStartupTimeoutError:
            timeout = self.startup_timeout
            logger.error(
                "Timed out loading plugin %s '%s' after %.2fs; skipping.",
                source_kind,
                module_name,
                timeout,
            )
        except Exception as e:
            logger.error(f"Failed to load plugin {source_kind} '{module_name}': {e}")
            logger.debug(traceback.format_exc())

    async def _load_plugins_with_timeout(self, module_name: str) -> list[Plugin]:
        future = self._run_in_daemon_thread(
            lambda: self._load_plugin_source_sync(module_name),
            name=f"PluginLoader-{module_name}",
        )
        wrapped = asyncio.wrap_future(future)
        if self.startup_timeout is None:
            return await wrapped

        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=self.startup_timeout)
        except TimeoutError:
            if future.done():
                raise
            future.add_done_callback(
                lambda done: self._log_late_plugin_source(module_name, done)
            )
            raise PluginStartupTimeoutError from None

    def _load_plugin_source_sync(self, module_name: str) -> list[Plugin]:
        module = importlib.import_module(module_name)
        return asyncio.run(self._find_and_load_plugins(module, module_name))

    @staticmethod
    def _run_in_daemon_thread(
        func: Callable[[], list[Plugin]],
        *,
        name: str,
    ) -> concurrent.futures.Future[list[Plugin]]:
        future: concurrent.futures.Future[list[Plugin]] = concurrent.futures.Future()

        def runner() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                result = func()
            except BaseException as e:
                if not future.done():
                    future.set_exception(e)
            else:
                if not future.done():
                    future.set_result(result)

        thread = threading.Thread(target=runner, name=name, daemon=True)
        thread.start()
        return future

    @staticmethod
    def _log_late_plugin_source(
        module_name: str,
        future: concurrent.futures.Future[list[Plugin]],
    ) -> None:
        try:
            future.result()
        except BaseException as e:
            logger.debug(
                "Timed-out plugin source '%s' later failed after being skipped: %s",
                module_name,
                e,
            )
        else:
            logger.debug(
                "Timed-out plugin source '%s' later finished after being skipped.",
                module_name,
            )

    async def _find_and_load_plugins(self, module, source: str) -> list[Plugin]:
        loaded_plugins: list[Plugin] = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Plugin)
                and attr is not Plugin
                and hasattr(attr, "metadata")
            ):
                try:
                    plugin = attr()
                    await plugin.on_load(self._ctx)
                    loaded_plugins.append(plugin)
                    logger.info(f"Loaded plugin: {plugin.metadata.name} from {source}")
                except Exception as e:
                    logger.error(f"Failed to initialize plugin {attr_name}: {e}")
                    logger.debug(traceback.format_exc())
        return loaded_plugins

    async def dispatch_message(self, event: MessageEvent) -> bool:
        """Let each plugin handle the message. Returns True if any plugin stopped propagation."""
        for plugin in self._plugins:
            try:
                if await plugin.on_message(event):
                    return True
            except Exception as e:
                logger.error(f"Plugin {plugin.metadata.name} error: {e}")
        return False

    def get_all_tools(self):
        """Collect custom tools from all loaded plugins."""
        tools = []
        for plugin in self._plugins:
            tools.extend(plugin.get_tools())
        return tools

    def get_all_commands(self) -> dict:
        """Collect commands from all loaded plugins."""
        commands = {}
        for plugin in self._plugins:
            commands.update(plugin.get_commands())
        return commands

    async def terminate(self) -> None:
        for plugin in self._plugins:
            try:
                await plugin.on_unload()
            except Exception as e:
                logger.warning(f"Error unloading plugin {plugin.metadata.name}: {e}")
        self._plugins.clear()
