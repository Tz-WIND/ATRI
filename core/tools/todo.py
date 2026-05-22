"""Visible agent todo list tool."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .base import Tool, ToolCapabilities

if TYPE_CHECKING:
    from core.runtime.todos import TodoStore


class AgentTodoTool(Tool):
    name = "todo"
    description = (
        "Maintain a visible todo list for the current chat session. Use it to create "
        "short task lists, add follow-up items, mark one item complete, mark all items "
        "complete, list the current todo state, or clear it. The todo list is shown in "
        "the Dashboard chat page."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "add", "complete", "complete_all", "list", "clear"],
                "description": "Todo operation to perform.",
            },
            "items": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "completed"]},
                            },
                            "required": ["content"],
                        },
                    ]
                },
                "description": "Items for action=set or action=add.",
            },
            "item": {
                "type": "string",
                "description": "Single item text for action=add.",
            },
            "todo_id": {
                "type": "string",
                "description": "Todo id to complete.",
            },
            "index": {
                "type": "integer",
                "description": "1-based todo item index to complete.",
            },
            "content": {
                "type": "string",
                "description": "Exact todo content to complete when id/index is unavailable.",
            },
        },
        "required": ["action"],
    }
    capabilities = ToolCapabilities(
        capability="agent.todo",
        read_only=True,
    )

    def __init__(
        self,
        workspace: str = ".",
        *,
        todo_store: TodoStore | None = None,
        session_id: str = "",
        on_change: Callable[[dict[str, Any]], None] | None = None,
    ):
        super().__init__(workspace)
        self.todo_store = todo_store
        self.session_id = session_id
        self.on_change = on_change

    def execute(
        self,
        action: str,
        items: list[object] | None = None,
        item: str | None = None,
        todo_id: str | None = None,
        index: int | None = None,
        content: str | None = None,
        **_: Any,
    ) -> str:
        if self.todo_store is None or not self.session_id:
            return "Error: todo store is not configured for this session"

        action = str(action or "").strip().lower()
        try:
            if action == "set":
                snapshot = self.todo_store.set_items(self.session_id, _input_items(items, item))
                self._emit(snapshot)
                return "Set agent todo list.\n" + _format_snapshot(snapshot)
            if action == "add":
                snapshot = self.todo_store.add_items(self.session_id, _input_items(items, item))
                self._emit(snapshot)
                return "Added agent todo item(s).\n" + _format_snapshot(snapshot)
            if action == "complete":
                snapshot, completed_item = self.todo_store.complete_item(
                    self.session_id,
                    todo_id=str(todo_id or ""),
                    index=_coerce_index(index),
                    content=str(content or ""),
                )
                self._emit(snapshot)
                completed_index = _item_index(snapshot, completed_item.get("id"))
                label = str(completed_index) if completed_index else str(completed_item.get("id"))
                return f"Marked todo {label} complete.\n" + _format_snapshot(snapshot)
            if action == "complete_all":
                snapshot = self.todo_store.complete_all(self.session_id)
                self._emit(snapshot)
                return "Marked all todo items complete.\n" + _format_snapshot(snapshot)
            if action == "clear":
                snapshot = self.todo_store.clear(self.session_id)
                self._emit(snapshot)
                return "Cleared agent todo list.\n" + _format_snapshot(snapshot)
            if action == "list":
                return _format_snapshot(self.todo_store.snapshot(self.session_id))
        except ValueError as e:
            return f"Error: {e}"
        return f"Error: unsupported todo action '{action}'"

    def _emit(self, snapshot: dict[str, Any]) -> None:
        if self.on_change:
            self.on_change(snapshot)


def _input_items(items: list[object] | None, item: str | None) -> list[object]:
    values = list(items or [])
    if item:
        values.append(item)
    return values


def _coerce_index(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _item_index(snapshot: dict[str, Any], todo_id: object) -> int:
    for index, item in enumerate(snapshot.get("items") or [], start=1):
        if item.get("id") == todo_id:
            return index
    return 0


def _format_snapshot(snapshot: dict[str, Any]) -> str:
    total = int(snapshot.get("total") or 0)
    completed = int(snapshot.get("completed") or 0)
    lines = [f"Agent todo: {completed}/{total} complete ({total} todo item(s))."]
    for index, item in enumerate(snapshot.get("items") or [], start=1):
        mark = "x" if item.get("status") == "completed" else " "
        lines.append(f"- [{mark}] {index}. {item.get('content', '')} (`{item.get('id', '')}`)")
    return "\n".join(lines)
