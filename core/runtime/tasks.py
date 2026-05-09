"""Persistent runtime task queue.

This store is intentionally small: it records background task lifecycle state,
visible progress events, final results, gate evidence, and artifacts.  The
worker implementation still lives in tools, but task state survives process
restarts so completed or interrupted work remains queryable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .timeline import DEFAULT_RUNTIME_DIR, summarize_text

TASK_SCHEMA_VERSION = 1
MAX_TASK_EVENT_LIMIT = 10_000
TASK_OPTIMIZE_INTERVAL_SECONDS = 3600.0
TERMINAL_TASK_STATUSES = {"completed", "failed", "canceled", "interrupted"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _new_task_id() -> str:
    return f"task_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:12]}"


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _duration_ms(started_at: str | None, ended_at: str | None) -> int | None:
    if not started_at or not ended_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((end - start).total_seconds() * 1000))


def _merge_dict(existing: Any, patch: dict[str, Any] | None) -> dict[str, Any]:
    base = existing if isinstance(existing, dict) else {}
    merged = dict(base)
    if patch:
        merged.update(patch)
    return merged


def _merge_artifacts(
    existing: Any,
    artifacts: dict[str, Any] | list[dict[str, Any]] | None,
) -> list:
    merged = list(existing) if isinstance(existing, list) else []
    if artifacts is None:
        return merged
    if isinstance(artifacts, list):
        merged.extend(artifacts)
    else:
        merged.append(artifacts)
    return merged


def _close_connection(conn: sqlite3.Connection, lock: Any) -> None:
    with lock:
        conn.close()


@dataclass(frozen=True)
class TaskEvent:
    seq: int
    task_id: str
    timestamp: str
    event_type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "event": self.event_type,
            "payload": self.payload,
        }


class TaskStore:
    """SQLite-backed queue state for background runtime tasks.

    Passing a directory stores tasks in ``tasks.sqlite3`` under that directory.
    Passing a ``.db``/``.sqlite`` path uses that exact database file.
    """

    def __init__(self, runtime_dir: str | Path | None = None):
        root = Path(runtime_dir) if runtime_dir else DEFAULT_RUNTIME_DIR
        if root.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            self.runtime_dir = root.parent
            self.db_path = root
        else:
            self.runtime_dir = root
            self.db_path = root / "tasks.sqlite3"

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._last_optimize_at = time.monotonic()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._connection_finalizer = weakref.finalize(
            self,
            _close_connection,
            self._conn,
            self._lock,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._init_schema()

    def close(self) -> None:
        self._connection_finalizer()

    def optimize(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA optimize")
            self._last_optimize_at = time.monotonic()

    def _maybe_optimize(self) -> None:
        now = time.monotonic()
        if now - self._last_optimize_at < TASK_OPTIMIZE_INTERVAL_SECONDS:
            return
        with self._lock:
            now = time.monotonic()
            if now - self._last_optimize_at < TASK_OPTIMIZE_INTERVAL_SECONDS:
                return
            self._conn.execute("PRAGMA optimize")
            self._last_optimize_at = now

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                input TEXT NOT NULL,
                workspace TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                duration_ms INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                evidence_json TEXT NOT NULL DEFAULT '{}',
                artifacts_json TEXT NOT NULL DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_kind_updated
                ON tasks(kind, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_tasks_status_updated
                ON tasks(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS task_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_task_events_task_seq
                ON task_events(task_id, seq ASC);
            """
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO task_meta(key, value) VALUES ('schema_version', '0')"
        )
        stored_version = self._stored_schema_version()
        if stored_version > TASK_SCHEMA_VERSION:
            raise RuntimeError(
                f"Runtime task schema v{stored_version} is newer than supported "
                f"v{TASK_SCHEMA_VERSION}"
            )
        if stored_version < TASK_SCHEMA_VERSION:
            self._migrate_schema(stored_version, TASK_SCHEMA_VERSION)
            self._set_schema_version(TASK_SCHEMA_VERSION)
        self._conn.commit()

    def _stored_schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM task_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError) as exc:
            value = row["value"]
            raise RuntimeError(f"Invalid runtime task schema version: {value!r}") from exc

    def _set_schema_version(self, version: int) -> None:
        self._conn.execute(
            """
            INSERT INTO task_meta(key, value)
            VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(version),),
        )

    def _migrate_schema(self, from_version: int, to_version: int) -> None:
        version = from_version
        while version < to_version:
            if version == 0:
                version = 1
                continue
            raise RuntimeError(
                f"No migration path for runtime task schema v{version} -> v{to_version}"
            )

    def create_task(
        self,
        *,
        kind: str,
        title: str,
        input_text: str = "",
        workspace: str = "",
        metadata: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        task_id: str | None = None,
        status: str = "queued",
    ) -> str:
        now = _now_iso()
        started_at = now if status == "running" else None
        ended_at = now if status in TERMINAL_TASK_STATUSES else None
        task_id = task_id or _new_task_id()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tasks(
                    id, kind, status, title, input, workspace, created_at, updated_at,
                    started_at, ended_at, duration_ms, metadata_json, evidence_json,
                    artifacts_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    kind,
                    status,
                    title,
                    input_text,
                    workspace,
                    now,
                    now,
                    started_at,
                    ended_at,
                    _duration_ms(started_at, ended_at),
                    _json_dumps(metadata or {}),
                    _json_dumps(evidence or {}),
                    _json_dumps(artifacts or []),
                ),
            )
        self.append_event(
            task_id,
            "task_created",
            {"kind": kind, "status": status, "title": title},
        )
        return task_id

    def start_task(self, task_id: str) -> bool:
        updated = self.update_task(task_id, status="running")
        if updated:
            self.append_event(task_id, "task_started", {})
        return updated

    def finish_task(
        self,
        task_id: str,
        *,
        status: str = "completed",
        result: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        artifacts: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> bool:
        updated = self.update_task(
            task_id,
            status=status,
            result=result,
            error=error,
            metadata=metadata,
            evidence=evidence,
            artifacts=artifacts,
        )
        if updated:
            payload = {"status": status}
            if error:
                payload["error"] = error
            self.append_event(task_id, "task_finished", payload)
        return updated

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        artifacts: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> bool:
        now = _now_iso()
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT status, result, error, started_at, ended_at, metadata_json,
                       evidence_json, artifacts_json
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                return False

            new_status = status or row["status"]
            started_at = row["started_at"]
            if new_status == "running" and not started_at:
                started_at = now
            ended_at = row["ended_at"]
            if new_status in TERMINAL_TASK_STATUSES and not ended_at:
                ended_at = now

            merged_metadata = _merge_dict(_json_loads(row["metadata_json"], {}), metadata)
            merged_evidence = _merge_dict(_json_loads(row["evidence_json"], {}), evidence)
            merged_artifacts = _merge_artifacts(_json_loads(row["artifacts_json"], []), artifacts)

            self._conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    result = ?,
                    error = ?,
                    updated_at = ?,
                    started_at = ?,
                    ended_at = ?,
                    duration_ms = ?,
                    metadata_json = ?,
                    evidence_json = ?,
                    artifacts_json = ?
                WHERE id = ?
                """,
                (
                    new_status,
                    row["result"] if result is None else result,
                    row["error"] if error is None else error,
                    now,
                    started_at,
                    ended_at,
                    _duration_ms(started_at, ended_at),
                    _json_dumps(merged_metadata),
                    _json_dumps(merged_evidence),
                    _json_dumps(merged_artifacts),
                    task_id,
                ),
            )
        self._maybe_optimize()
        return True

    def record_evidence(self, task_id: str, evidence: dict[str, Any]) -> bool:
        updated = self.update_task(task_id, evidence=evidence)
        if updated:
            self.append_event(task_id, "task_evidence", evidence)
        return updated

    def add_artifact(self, task_id: str, artifact: dict[str, Any]) -> bool:
        updated = self.update_task(task_id, artifacts=artifact)
        if updated:
            self.append_event(task_id, "task_artifact", artifact)
        return updated

    def append_event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> TaskEvent:
        timestamp = _now_iso()
        payload = dict(payload or {})
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO task_events(task_id, timestamp, type, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, timestamp, event_type, _json_dumps(payload)),
            )
            lastrowid = cursor.lastrowid
            if lastrowid is None:
                raise RuntimeError("failed to allocate runtime task event sequence")
        event = TaskEvent(
            seq=int(lastrowid),
            task_id=task_id,
            timestamp=timestamp,
            event_type=event_type,
            payload=payload,
        )
        self._maybe_optimize()
        return event

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, kind, status, title, input, workspace, result, error,
                       created_at, updated_at, started_at, ended_at, duration_ms,
                       metadata_json, evidence_json, artifacts_json
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        capped_limit = min(500, max(1, _coerce_int(limit, 50)))
        with self._lock:
            if kind and status:
                rows = self._conn.execute(
                    """
                    SELECT id, kind, status, title, input, workspace, result, error,
                           created_at, updated_at, started_at, ended_at, duration_ms,
                           metadata_json, evidence_json, artifacts_json
                    FROM tasks
                    WHERE kind = ? AND status = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (kind, status, capped_limit),
                ).fetchall()
            elif kind:
                rows = self._conn.execute(
                    """
                    SELECT id, kind, status, title, input, workspace, result, error,
                           created_at, updated_at, started_at, ended_at, duration_ms,
                           metadata_json, evidence_json, artifacts_json
                    FROM tasks
                    WHERE kind = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (kind, capped_limit),
                ).fetchall()
            elif status:
                rows = self._conn.execute(
                    """
                    SELECT id, kind, status, title, input, workspace, result, error,
                           created_at, updated_at, started_at, ended_at, duration_ms,
                           metadata_json, evidence_json, artifacts_json
                    FROM tasks
                    WHERE status = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (status, capped_limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT id, kind, status, title, input, workspace, result, error,
                           created_at, updated_at, started_at, ended_at, duration_ms,
                           metadata_json, evidence_json, artifacts_json
                    FROM tasks
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def events(
        self,
        task_id: str,
        *,
        since_seq: int | None = None,
        limit: int = 1000,
    ) -> list[TaskEvent]:
        since = max(0, _coerce_int(since_seq, 0))
        capped_limit = min(MAX_TASK_EVENT_LIMIT, max(0, _coerce_int(limit, 1000)))
        if capped_limit == 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT seq, task_id, timestamp, type, payload_json
                FROM task_events
                WHERE task_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (task_id, since, capped_limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def mark_incomplete_as_interrupted(self, *, reason: str) -> int:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE status NOT IN (?, ?, ?, ?)",
                sorted(TERMINAL_TASK_STATUSES),
            ).fetchall()
        count = 0
        for row in rows:
            task_id = row["id"]
            if self.finish_task(
                task_id,
                status="interrupted",
                error=reason,
                metadata={"interrupted_reason": reason},
            ):
                count += 1
        return count

    def _task_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        metadata = _json_loads(row["metadata_json"], {})
        evidence = _json_loads(row["evidence_json"], {})
        artifacts = _json_loads(row["artifacts_json"], [])
        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(evidence, dict):
            evidence = {}
        if not isinstance(artifacts, list):
            artifacts = []
        return {
            "id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "title": row["title"],
            "input": row["input"],
            "input_summary": summarize_text(row["input"]),
            "workspace": row["workspace"],
            "result": row["result"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "duration_ms": row["duration_ms"],
            "metadata": metadata,
            "evidence": evidence,
            "artifacts": artifacts,
        }

    def _event_from_row(self, row: sqlite3.Row) -> TaskEvent:
        payload = _json_loads(row["payload_json"], {})
        if not isinstance(payload, dict):
            payload = {}
        return TaskEvent(
            seq=int(row["seq"]),
            task_id=row["task_id"],
            timestamp=row["timestamp"],
            event_type=row["type"],
            payload=payload,
        )
