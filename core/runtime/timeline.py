"""Persistent runtime timeline.

The runtime timeline is intentionally separate from the legacy chat transcript
JSON files.  Transcripts keep the compact model context that the agent resumes
from; this store records every runtime event with a monotonic sequence number so
WebSocket clients can replay missed deltas after reconnecting.
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

DEFAULT_RUNTIME_DIR = Path("data/runtime")
RUNTIME_SCHEMA_VERSION = 1
MAX_REPLAY_LIMIT = 10_000


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _json_loads(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:12]}"


def _close_connection(conn: sqlite3.Connection, lock: Any) -> None:
    with lock:
        conn.close()


def summarize_text(text: str, limit: int = 120) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "..."


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _duration_ms(started_at: str | None, ended_at: str) -> int | None:
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((end - start).total_seconds() * 1000))


@dataclass(frozen=True)
class RuntimeEvent:
    seq: int
    timestamp: str
    thread_id: str
    turn_id: str | None
    item_id: str | None
    event_type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "item_id": self.item_id,
            "event": self.event_type,
            "payload": self.payload,
        }

    def to_wire_payload(self) -> dict[str, Any]:
        payload = dict(self.payload)
        payload.setdefault("type", self.event_type)
        payload["runtime_seq"] = self.seq
        payload["thread_id"] = self.thread_id
        payload["timestamp"] = self.timestamp
        if self.turn_id:
            payload["turn_id"] = self.turn_id
        if self.item_id:
            payload["item_id"] = self.item_id
        return payload


class RuntimeTimelineStore:
    """SQLite-backed runtime Thread/Turn/Item/Event store."""

    def __init__(self, runtime_dir: str | Path | None = None):
        root = Path(runtime_dir) if runtime_dir else DEFAULT_RUNTIME_DIR
        if root.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            self.runtime_dir = root.parent
            self.db_path = root
        else:
            self.runtime_dir = root
            self.db_path = root / "timeline.sqlite3"

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
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

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                model TEXT NOT NULL,
                workspace TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                latest_turn_id TEXT,
                title TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                status TEXT NOT NULL,
                input_summary TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                duration_ms INTEGER,
                error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_turns_thread_created
                ON turns(thread_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE,
                FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_items_turn_created
                ON items(turn_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                turn_id TEXT,
                item_id TEXT,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE,
                FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE,
                FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_thread_seq
                ON events(thread_id, seq ASC);

            CREATE INDEX IF NOT EXISTS idx_events_turn_seq
                ON events(turn_id, seq ASC);
            """
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO runtime_meta(key, value) VALUES ('schema_version', '0')"
        )
        stored_version = self._stored_schema_version()
        if stored_version > RUNTIME_SCHEMA_VERSION:
            raise RuntimeError(
                "Runtime timeline schema "
                f"v{stored_version} is newer than supported v{RUNTIME_SCHEMA_VERSION}"
            )
        if stored_version < RUNTIME_SCHEMA_VERSION:
            self._migrate_schema(stored_version, RUNTIME_SCHEMA_VERSION)
            self._set_schema_version(RUNTIME_SCHEMA_VERSION)
        self._conn.commit()

    def _stored_schema_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM runtime_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError) as exc:
            value = row["value"]
            raise RuntimeError(f"Invalid runtime timeline schema version: {value!r}") from exc

    def _set_schema_version(self, version: int) -> None:
        self._conn.execute(
            """
            INSERT INTO runtime_meta(key, value)
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
                f"No migration path for runtime timeline schema v{version} -> v{to_version}"
            )

    def ensure_thread(
        self,
        thread_id: str,
        *,
        model: str,
        workspace: str,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now_iso()
        metadata_json = _json_dumps(metadata or {})
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO threads(
                    id, session_id, model, workspace, created_at, updated_at, title,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id = excluded.session_id,
                    model = excluded.model,
                    workspace = excluded.workspace,
                    updated_at = excluded.updated_at,
                    title = COALESCE(excluded.title, threads.title),
                    metadata_json = CASE
                        WHEN excluded.metadata_json != '{}' THEN excluded.metadata_json
                        ELSE threads.metadata_json
                    END
                """,
                (thread_id, thread_id, model, workspace, now, now, title, metadata_json),
            )
        return self.get_thread(thread_id) or {}

    def start_turn(
        self,
        thread_id: str,
        *,
        input_text: str,
        model: str,
        workspace: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.ensure_thread(thread_id, model=model, workspace=workspace)
        turn_id = _new_id("turn")
        now = _now_iso()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO turns(
                    id, thread_id, status, input_summary, model, created_at, started_at,
                    metadata_json
                )
                VALUES (?, ?, 'in_progress', ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    thread_id,
                    summarize_text(input_text),
                    model,
                    now,
                    now,
                    _json_dumps(metadata or {}),
                ),
            )
            self._conn.execute(
                "UPDATE threads SET latest_turn_id = ?, updated_at = ? WHERE id = ?",
                (turn_id, now, thread_id),
            )
        return turn_id

    def finish_turn(
        self,
        turn_id: str,
        *,
        status: str = "completed",
        error: str | None = None,
    ) -> None:
        ended_at = _now_iso()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT thread_id, started_at FROM turns WHERE id = ?",
                (turn_id,),
            ).fetchone()
            if row is None:
                return
            duration = _duration_ms(row["started_at"], ended_at)
            self._conn.execute(
                """
                UPDATE turns
                SET status = ?, ended_at = ?, duration_ms = ?, error = ?
                WHERE id = ?
                """,
                (status, ended_at, duration, error, turn_id),
            )
            self._conn.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                (ended_at, row["thread_id"]),
            )

    def create_item(
        self,
        thread_id: str,
        turn_id: str,
        *,
        kind: str,
        summary: str,
        status: str = "in_progress",
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        item_id = _new_id("item")
        now = _now_iso()
        started_at = now if status in {"queued", "in_progress"} else None
        ended_at = now if status in {"completed", "failed", "canceled", "interrupted"} else None
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO items(
                    id, thread_id, turn_id, kind, status, summary, detail, metadata_json,
                    created_at, started_at, ended_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    thread_id,
                    turn_id,
                    kind,
                    status,
                    summary,
                    detail,
                    _json_dumps(metadata or {}),
                    now,
                    started_at,
                    ended_at,
                ),
            )
        return item_id

    def finish_item(
        self,
        item_id: str,
        *,
        status: str = "completed",
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        ended_at = _now_iso()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT metadata_json FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                return
            existing_metadata = _json_loads(row["metadata_json"])
            if not isinstance(existing_metadata, dict):
                existing_metadata = {}
            if metadata:
                existing_metadata.update(metadata)
            self._conn.execute(
                """
                UPDATE items
                SET status = ?,
                    detail = COALESCE(?, detail),
                    metadata_json = ?,
                    ended_at = ?
                WHERE id = ?
                """,
                (status, detail, _json_dumps(existing_metadata), ended_at, item_id),
            )

    def append_event(
        self,
        thread_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
        turn_id: str | None = None,
        item_id: str | None = None,
    ) -> RuntimeEvent:
        timestamp = _now_iso()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO events(timestamp, thread_id, turn_id, item_id, type, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, thread_id, turn_id, item_id, event_type, _json_dumps(payload)),
            )
            lastrowid = cursor.lastrowid
            if lastrowid is None:
                raise RuntimeError("failed to allocate runtime event sequence")
            seq = int(lastrowid)
        return RuntimeEvent(
            seq=seq,
            timestamp=timestamp,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
            event_type=event_type,
            payload=dict(payload),
        )

    def events_since(
        self,
        *,
        thread_id: str | None = None,
        since_seq: int | None = None,
        limit: int = 1000,
    ) -> list[RuntimeEvent]:
        since = max(0, _coerce_int(since_seq, 0))
        capped_limit = min(MAX_REPLAY_LIMIT, max(0, _coerce_int(limit, 1000)))
        if capped_limit == 0:
            return []

        with self._lock:
            if thread_id:
                rows = self._conn.execute(
                    """
                    SELECT seq, timestamp, thread_id, turn_id, item_id, type, payload_json
                    FROM events
                    WHERE thread_id = ? AND seq > ?
                    ORDER BY seq ASC
                    LIMIT ?
                    """,
                    (thread_id, since, capped_limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT seq, timestamp, thread_id, turn_id, item_id, type, payload_json
                    FROM events
                    WHERE seq > ?
                    ORDER BY seq ASC
                    LIMIT ?
                    """,
                    (since, capped_limit),
                ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def latest_seq(self, thread_id: str | None = None) -> int:
        with self._lock:
            if thread_id:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS seq FROM events WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS seq FROM events",
                ).fetchone()
        return int(row["seq"] if row else 0)

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, session_id, model, workspace, status, created_at, updated_at,
                       latest_turn_id, title, metadata_json
                FROM threads
                WHERE id = ?
                """,
                (thread_id,),
            ).fetchone()
        return self._thread_from_row(row) if row else None

    def list_threads(self, *, limit: int = 50, include_archived: bool = False) -> list[dict]:
        capped_limit = min(500, max(1, _coerce_int(limit, 50)))
        with self._lock:
            if include_archived:
                rows = self._conn.execute(
                    """
                    SELECT id, session_id, model, workspace, status, created_at, updated_at,
                           latest_turn_id, title, metadata_json
                    FROM threads
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT id, session_id, model, workspace, status, created_at, updated_at,
                           latest_turn_id, title, metadata_json
                    FROM threads
                    WHERE status != 'archived'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                ).fetchall()
        return [self._thread_from_row(row) for row in rows]

    def thread_detail(self, thread_id: str) -> dict[str, Any] | None:
        thread = self.get_thread(thread_id)
        if thread is None:
            return None
        turns = self.turns_for_thread(thread_id)
        items = self.items_for_thread(thread_id)
        return {
            "thread": thread,
            "turns": turns,
            "items": items,
            "latest_seq": self.latest_seq(thread_id),
        }

    def turns_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, thread_id, status, input_summary, model, created_at,
                       started_at, ended_at, duration_ms, error, metadata_json
                FROM turns
                WHERE thread_id = ?
                ORDER BY created_at ASC
                """,
                (thread_id,),
            ).fetchall()
        return [self._turn_from_row(row) for row in rows]

    def items_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, thread_id, turn_id, kind, status, summary, detail, metadata_json,
                       created_at, started_at, ended_at
                FROM items
                WHERE thread_id = ?
                ORDER BY created_at ASC
                """,
                (thread_id,),
            ).fetchall()
        return [self._item_from_row(row) for row in rows]

    def delete_thread(self, thread_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        return cursor.rowcount > 0

    def _event_from_row(self, row: sqlite3.Row) -> RuntimeEvent:
        payload = _json_loads(row["payload_json"])
        if not isinstance(payload, dict):
            payload = {"payload": payload}
        return RuntimeEvent(
            seq=int(row["seq"]),
            timestamp=row["timestamp"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            item_id=row["item_id"],
            event_type=row["type"],
            payload=payload,
        )

    def _thread_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "model": row["model"],
            "workspace": row["workspace"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "latest_turn_id": row["latest_turn_id"],
            "title": row["title"],
            "metadata": _json_loads(row["metadata_json"]),
        }

    def _turn_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "status": row["status"],
            "input_summary": row["input_summary"],
            "model": row["model"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "duration_ms": row["duration_ms"],
            "error": row["error"],
            "metadata": _json_loads(row["metadata_json"]),
        }

    def _item_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "turn_id": row["turn_id"],
            "kind": row["kind"],
            "status": row["status"],
            "summary": row["summary"],
            "detail": row["detail"],
            "metadata": _json_loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
        }
