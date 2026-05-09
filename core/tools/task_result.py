"""Query persisted background runtime tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import Tool

if TYPE_CHECKING:
    from core.runtime import TaskEvent, TaskStore


_MAX_DETAIL_CHARS = 12000


class TaskResultTool(Tool):
    name = "task_result"
    description = (
        "Check persisted background runtime tasks. "
        "Call with task_id to inspect one task, or omit task_id to list recent tasks. "
        "Optional kind filters the list, for example sub_agent, command, or test_command."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "Persisted task id to inspect. If omitted, lists recent tasks.",
            },
            "kind": {
                "type": "string",
                "description": "Optional task kind filter when listing tasks.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of tasks to list, default 20.",
            },
        },
        "required": [],
    }

    def __init__(self, workspace: str = ".", task_store: TaskStore | None = None):
        super().__init__(workspace)
        self.task_store = task_store

    def execute(
        self,
        task_id: str | None = None,
        kind: str | None = None,
        limit: int = 20,
        **kwargs: Any,
    ) -> str:
        if self.task_store is None:
            return "No persistent task store is configured."
        if task_id:
            task = self.task_store.get_task(task_id)
            if task is None:
                return f"No persisted task found with id '{task_id}'"
            return _format_task_detail(task, self.task_store.events(task_id))

        tasks = self.task_store.list_tasks(kind=_clean(kind) or None, limit=limit)
        if not tasks:
            suffix = f" with kind '{kind}'" if kind else ""
            return f"No persisted tasks{suffix}."
        return "Persisted runtime tasks:\n" + "\n".join(
            _format_task_summary(task) for task in tasks
        )


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _truncate(text: object, max_chars: int) -> str:
    value = "" if text is None else str(text)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 31] + "\n... (task output truncated)"


def _format_task_summary(task: dict) -> str:
    title = task.get("title") or task.get("input_summary") or task["id"]
    return f"  - `{task['id']}`: {task['status']} {task['kind']} - {_truncate(title, 90)}"


def _format_task_detail(task: dict, events: list[TaskEvent]) -> str:
    lines = [
        f"### Task `{task['id']}`",
        f"Kind: {task['kind']}",
        f"Status: {task['status']}",
        f"Input: {_truncate(task.get('input') or '', 500)}",
    ]
    if task.get("error"):
        lines.append(f"Error: {task['error']}")

    metadata = task.get("metadata") or {}
    text = metadata.get("text")
    if text:
        lines.extend(["", "Visible output:", _truncate(text, 4000)])

    if events:
        lines.extend(["", "Events:"])
        for event in events[-30:]:
            lines.append(
                f"- #{event.seq} {event.event_type}: {_truncate(repr(event.payload), 700)}"
            )
        if len(events) > 30:
            lines.append(f"- ... ({len(events) - 30} earlier event(s) omitted)")

    evidence = task.get("evidence") or {}
    if evidence:
        lines.extend(["", f"Evidence: {_truncate(repr(evidence), 1000)}"])

    artifacts = task.get("artifacts") or []
    if artifacts:
        lines.extend(["", f"Artifacts: {_truncate(repr(artifacts), 1000)}"])

    if task.get("result"):
        lines.extend(["", "Result:", _truncate(task["result"], 5000)])

    return _truncate("\n".join(lines), _MAX_DETAIL_CHARS)
