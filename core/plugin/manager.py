"""Plugin manager - discovers, loads, and manages plugins.

Scans the plugins/ directory for Python modules containing Plugin subclasses.
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback
from pathlib import Path

from core import logger
from core.platform.message import MessageEvent
from .base import Plugin, PluginMetadata


class PluginManager:
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
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

        logger.info(f"Loaded {len(self._plugins)} plugins: "
                     f"{[p.metadata.name for p in self._plugins]}")

    async def _load_plugin_package(self, package_name: str) -> None:
        try:
            module = importlib.import_module(package_name)
            await self._find_and_load_plugins(module, package_name)
        except Exception as e:
            logger.error(f"Failed to load plugin package '{package_name}': {e}")
            logger.debug(traceback.format_exc())

    async def _load_plugin_module(self, module_name: str) -> None:
        try:
            module = importlib.import_module(module_name)
            await self._find_and_load_plugins(module, module_name)
        except Exception as e:
            logger.error(f"Failed to load plugin module '{module_name}': {e}")
            logger.debug(traceback.format_exc())

    async def _find_and_load_plugins(self, module, source: str) -> None:
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
                    self._plugins.append(plugin)
                    logger.info(f"Loaded plugin: {plugin.metadata.name} from {source}")
                except Exception as e:
                    logger.error(f"Failed to initialize plugin {attr_name}: {e}")
                    logger.debug(traceback.format_exc())

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
