"""Session-scoped visible todo lists for agent runs."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.utils import atomic_write_text

from .timeline import DEFAULT_RUNTIME_DIR

TODO_SCHEMA_VERSION = 1
MAX_TODO_ITEMS = 100


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _new_todo_id() -> str:
    return f"todo_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:8]}"


def _clean_text(value: object, *, max_chars: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_chars]


def _normalize_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status in {"done", "complete", "completed", "checked"}:
        return "completed"
    return "pending"


def _safe_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    return text if all(char in allowed for char in text) else ""


def _deepcopy_payload(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))


class TodoStore:
    """JSON-backed todo snapshots keyed by normalized session id."""

    def __init__(self, runtime_dir: str | Path | None = None):
        root = Path(runtime_dir) if runtime_dir else DEFAULT_RUNTIME_DIR
        if root.suffix.lower() == ".json":
            self.runtime_dir = root.parent
            self.path = root
        else:
            self.runtime_dir = root
            self.path = root / "agent_todos.json"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def close(self) -> None:
        return

    def snapshot(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(session_id)

    def set_items(self, session_id: str, items: list[object]) -> dict[str, Any]:
        now = _now_iso()
        normalized = [
            item
            for raw in items[:MAX_TODO_ITEMS]
            if (item := self._item_from_raw(raw, now=now)) is not None
        ]
        with self._lock:
            state = self._state_locked(session_id)
            state["items"] = normalized
            state["updated_at"] = now
            self._save_locked()
            return self._snapshot_locked(session_id)

    def add_items(self, session_id: str, items: list[object]) -> dict[str, Any]:
        now = _now_iso()
        additions = [
            item for raw in items if (item := self._item_from_raw(raw, now=now)) is not None
        ]
        with self._lock:
            state = self._state_locked(session_id)
            remaining = max(0, MAX_TODO_ITEMS - len(state["items"]))
            state["items"].extend(additions[:remaining])
            state["updated_at"] = now
            self._save_locked()
            return self._snapshot_locked(session_id)

    def complete_item(
        self,
        session_id: str,
        *,
        todo_id: str = "",
        index: int | None = None,
        content: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        now = _now_iso()
        with self._lock:
            state = self._state_locked(session_id)
            item = self._find_item_locked(
                state["items"],
                todo_id=todo_id,
                index=index,
                content=content,
            )
            if item is None:
                raise ValueError("todo item not found")
            item["status"] = "completed"
            item["updated_at"] = now
            state["updated_at"] = now
            self._save_locked()
            return self._snapshot_locked(session_id), dict(item)

    def complete_all(self, session_id: str) -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            state = self._state_locked(session_id)
            for item in state["items"]:
                item["status"] = "completed"
                item["updated_at"] = now
            state["updated_at"] = now
            self._save_locked()
            return self._snapshot_locked(session_id)

    def clear(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._state_locked(session_id)
            state["items"] = []
            state["updated_at"] = _now_iso()
            self._save_locked()
            return self._snapshot_locked(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            sessions = self._data.setdefault("sessions", {})
            existed = session_id in sessions
            sessions.pop(session_id, None)
            if existed:
                self._save_locked()
            return existed

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": TODO_SCHEMA_VERSION, "sessions": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"schema_version": TODO_SCHEMA_VERSION, "sessions": {}}
        if not isinstance(data, dict):
            return {"schema_version": TODO_SCHEMA_VERSION, "sessions": {}}
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        data["schema_version"] = TODO_SCHEMA_VERSION
        return data

    def _save_locked(self) -> None:
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        atomic_write_text(self.path, payload, prefix=".agent_todos_")

    def _state_locked(self, session_id: str) -> dict[str, Any]:
        sessions = self._data.setdefault("sessions", {})
        state = sessions.get(session_id)
        if not isinstance(state, dict):
            state = {"updated_at": _now_iso(), "items": []}
            sessions[session_id] = state
        if not isinstance(state.get("items"), list):
            state["items"] = []
        return state

    def _snapshot_locked(self, session_id: str) -> dict[str, Any]:
        state = self._state_locked(session_id)
        items = []
        for raw in state.get("items", []):
            item_time = _now_iso()
            if isinstance(raw, dict):
                item_time = str(raw.get("created_at") or item_time)
            item = self._item_from_raw(raw, now=item_time)
            if item is not None:
                items.append(item)
        completed = sum(1 for item in items if item["status"] == "completed")
        return _deepcopy_payload(
            {
                "session_id": session_id,
                "updated_at": state.get("updated_at") or "",
                "items": items,
                "total": len(items),
                "completed": completed,
                "all_completed": bool(items) and completed == len(items),
            }
        )

    def _item_from_raw(self, raw: object, *, now: str) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            content = _clean_text(raw.get("content") or raw.get("title") or raw.get("text"))
            if not content:
                return None
            created_at = str(raw.get("created_at") or now)
            updated_at = str(raw.get("updated_at") or created_at)
            return {
                "id": _safe_id(raw.get("id")) or _new_todo_id(),
                "content": content,
                "status": _normalize_status(raw.get("status") or raw.get("state")),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        content = _clean_text(raw)
        if not content:
            return None
        return {
            "id": _new_todo_id(),
            "content": content,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }

    def _find_item_locked(
        self,
        items: list[dict[str, Any]],
        *,
        todo_id: str = "",
        index: int | None = None,
        content: str = "",
    ) -> dict[str, Any] | None:
        clean_id = _safe_id(todo_id)
        if clean_id:
            for item in items:
                if item.get("id") == clean_id:
                    return item
        if index is not None and index > 0:
            zero_index = index - 1
            if zero_index < len(items):
                return items[zero_index]
        clean_content = _clean_text(content).lower()
        if clean_content:
            for item in items:
                if str(item.get("content") or "").strip().lower() == clean_content:
                    return item
        return None
