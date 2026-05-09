"""Bridge between Agent and the tools package.

Avoids circular imports by lazily importing tools when needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from core.tools.base import Tool


def get_all_tools(
    workspace: str,
    skill_manager=None,
    tool_result_store=None,
    task_store=None,
    mcp_servers: dict | None = None,
    mode_controller=None,
) -> list[Tool]:
    from core.tools import create_tools

    return create_tools(
        workspace,
        skill_manager=skill_manager,
        tool_result_store=tool_result_store,
        task_store=task_store,
        mcp_servers=mcp_servers,
        mode_controller=mode_controller,
    )


def get_tool(name: str, tools: Iterable[Tool]) -> Tool | None:
    for t in tools:
        if t.name == name:
            return t
    return None
