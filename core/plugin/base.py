"""Base plugin interface - inspired by AstrBot's Star system.

Plugins can:
- Hook into pipeline stages (before/after processing)
- Register custom tools for the Agent
- Register custom commands
- Run background tasks
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from core.platform.message import MessageEvent
from core.tools.base import Tool


@dataclass
class PluginMetadata:
    name: str
    description: str = ""
    version: str = "0.1.0"
    author: str = ""


class Plugin(abc.ABC):
    """Base class for all ATRI plugins."""

    metadata: PluginMetadata

    def __init__(self):
        self._commands: dict[str, Callable] = {}
        self._tools: list[Tool] = []
        self._background_tasks: list[Callable] = []

    @abc.abstractmethod
    async def on_load(self, ctx: dict) -> None:
        """Called when the plugin is loaded. Use ctx to access shared resources."""
        ...

    async def on_unload(self) -> None:
        """Called when the plugin is unloaded."""
        pass

    async def on_message(self, event: MessageEvent) -> bool:
        """Called for every incoming message. Return True to stop pipeline propagation."""
        return False

    def register_command(self, name: str, handler: Callable):
        """Register a chat command (e.g. /help)."""
        self._commands[name] = handler

    def register_tool(self, tool: Tool):
        """Register a custom tool for the Agent to use."""
        self._tools.append(tool)

    def register_background_task(self, coro_func: Callable):
        """Register a background coroutine to run alongside the main loop."""
        self._background_tasks.append(coro_func)

    def get_commands(self) -> dict[str, Callable]:
        return self._commands

    def get_tools(self) -> list[Tool]:
        return self._tools

    def get_background_tasks(self) -> list[Callable]:
        return self._background_tasks
