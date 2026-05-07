"""Bridge between Agent and the tools package.

Avoids circular imports by lazily importing tools when needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core.tools.base import Tool


def get_all_tools(workspace: str) -> list[Tool]:
    from core.tools import create_tools
    return create_tools(workspace)


def get_tool(name: str, tools: Iterable[Tool]) -> Tool | None:
    for t in tools:
        if t.name == name:
            return t
    return None
