"""SQLite persistence for ATRI knowledge bases."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, cast


def utc_timestamp() -> float:
    return time.time()


class KnowledgeStore:
    """Small SQLite data access layer for knowledge metadata and vectors."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None
        self.fts_available = False

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self._create_schema()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _create_schema(self) -> None:
        conn = self._conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                kb_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_config TEXT NOT NULL DEFAULT '{}',
                embedding_dimensions INTEGER NOT NULL,
                rerank_provider TEXT NOT NULL DEFAULT '',
                rerank_model TEXT NOT NULL DEFAULT '',
                rerank_config TEXT NOT NULL DEFAULT '{}',
                chunk_size INTEGER NOT NULL DEFAULT 800,
                chunk_overlap INTEGER NOT NULL DEFAULT 120,
                top_k_dense INTEGER NOT NULL DEFAULT 30,
                top_k_sparse INTEGER NOT NULL DEFAULT 30,
                top_m_final INTEGER NOT NULL DEFAULT 5,
                doc_count INTEGER NOT NULL DEFAULT 0,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                kb_id TEXT NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                doc_name TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                kb_id TEXT NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                char_count INTEGER NOT NULL,
                embedding TEXT NOT NULL,
                embedding_norm REAL NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                kb_id TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_documents_kb_id ON documents(kb_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_kb_id ON chunks(kb_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
            """
        )
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                "USING fts5(chunk_id UNINDEXED, kb_id UNINDEXED, doc_id UNINDEXED, content)"
            )
            self.fts_available = True
        except sqlite3.OperationalError:
            self.fts_available = False
        conn.commit()

    def create_kb(self, values: dict[str, Any]) -> dict:
        now = utc_timestamp()
        record = {
            "kb_id": values.get("kb_id") or str(uuid.uuid4()),
            "name": values["name"],
            "description": values.get("description") or "",
            "embedding_provider": values["embedding_provider"],
            "embedding_model": values["embedding_model"],
            "embedding_config": json.dumps(
                values.get("embedding_config") or {}, ensure_ascii=False
            ),
            "embedding_dimensions": int(values["embedding_dimensions"]),
            "rerank_provider": values.get("rerank_provider") or "",
            "rerank_model": values.get("rerank_model") or "",
            "rerank_config": json.dumps(values.get("rerank_config") or {}, ensure_ascii=False),
            "chunk_size": _int_or_default(values.get("chunk_size"), 800),
            "chunk_overlap": _int_or_default(values.get("chunk_overlap"), 120),
            "top_k_dense": _int_or_default(values.get("top_k_dense"), 30),
            "top_k_sparse": _int_or_default(values.get("top_k_sparse"), 30),
            "top_m_final": _int_or_default(values.get("top_m_final"), 5),
            "created_at": now,
            "updated_at": now,
        }
        keys = ", ".join(record)
        placeholders = ", ".join("?" for _ in record)
        try:
            self._conn().execute(
                f"INSERT INTO knowledge_bases ({keys}) VALUES ({placeholders})",  # noqa: S608
                tuple(record.values()),
            )
        except sqlite3.IntegrityError as e:
            raise _friendly_integrity_error(e) from e
        self._conn().commit()
        return self.get_kb(record["kb_id"]) or {}

    def update_kb(self, kb_id: str, values: dict[str, Any]) -> dict | None:
        if not values:
            return self.get_kb(kb_id)
        mapped: dict[str, Any] = {}
        for key, value in values.items():
            if key in {"embedding_config", "rerank_config"}:
                mapped[key] = json.dumps(value or {}, ensure_ascii=False)
            else:
                mapped[key] = value
        mapped["updated_at"] = utc_timestamp()
        assignments = ", ".join(f"{key}=?" for key in mapped)
        try:
            self._conn().execute(
                f"UPDATE knowledge_bases SET {assignments} WHERE kb_id=?",  # noqa: S608
                (*mapped.values(), kb_id),
            )
        except sqlite3.IntegrityError as e:
            raise _friendly_integrity_error(e) from e
        self._conn().commit()
        return self.get_kb(kb_id)

    def get_kb(self, kb_id: str) -> dict | None:
        row = (
            self._conn()
            .execute(
                "SELECT * FROM knowledge_bases WHERE kb_id=?",
                (kb_id,),
            )
            .fetchone()
        )
        return self._decode_kb(row) if row else None

    def list_kbs(self) -> list[dict]:
        rows = (
            self._conn()
            .execute("SELECT * FROM knowledge_bases ORDER BY created_at DESC")
            .fetchall()
        )
        return [self._decode_kb(row) for row in rows]

    def delete_kb(self, kb_id: str) -> bool:
        doc_ids = [
            row["doc_id"]
            for row in self._conn().execute(
                "SELECT doc_id FROM documents WHERE kb_id=?",
                (kb_id,),
            )
        ]
        for doc_id in doc_ids:
            self._delete_fts_doc(doc_id)
        cur = self._conn().execute("DELETE FROM knowledge_bases WHERE kb_id=?", (kb_id,))
        self._conn().commit()
        return cur.rowcount > 0

    def create_document(
        self,
        kb_id: str,
        file_name: str,
        file_type: str,
        file_size: int,
        source: str,
    ) -> dict:
        now = utc_timestamp()
        doc_id = str(uuid.uuid4())
        self._conn().execute(
            """
            INSERT INTO documents
                (doc_id, kb_id, doc_name, file_type, file_size, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (doc_id, kb_id, file_name, file_type, file_size, source, now, now),
        )
        self._conn().commit()
        return self.get_document(doc_id) or {}

    def add_chunks(self, kb_id: str, doc_id: str, chunks: list[tuple[str, list[float]]]) -> None:
        now = utc_timestamp()
        conn = self._conn()
        for index, (content, vector) in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            norm = sum(item * item for item in vector) ** 0.5
            conn.execute(
                """
                INSERT INTO chunks
                    (chunk_id, kb_id, doc_id, chunk_index, content, char_count,
                     embedding, embedding_norm, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    kb_id,
                    doc_id,
                    index,
                    content,
                    len(content),
                    json.dumps(vector),
                    norm,
                    now,
                ),
            )
            if self.fts_available:
                conn.execute(
                    "INSERT INTO chunks_fts (chunk_id, kb_id, doc_id, content) VALUES (?, ?, ?, ?)",
                    (chunk_id, kb_id, doc_id, content),
                )
        conn.commit()
        self.refresh_counts(kb_id, doc_id)

    def get_document(self, doc_id: str) -> dict | None:
        row = self._conn().execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def list_documents(self, kb_id: str) -> list[dict]:
        rows = (
            self._conn()
            .execute(
                "SELECT * FROM documents WHERE kb_id=? ORDER BY created_at DESC",
                (kb_id,),
            )
            .fetchall()
        )
        return [dict(row) for row in rows]

    def delete_document(self, doc_id: str) -> bool:
        doc = self.get_document(doc_id)
        if not doc:
            return False
        self._delete_fts_doc(doc_id)
        cur = self._conn().execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        self._conn().commit()
        self.refresh_counts(doc["kb_id"])
        return cur.rowcount > 0

    def list_chunks(self, doc_id: str, offset: int = 0, limit: int = 100) -> list[dict]:
        rows = (
            self._conn()
            .execute(
                """
            SELECT chunk_id, kb_id, doc_id, chunk_index, content, char_count, created_at
            FROM chunks
            WHERE doc_id=?
            ORDER BY chunk_index ASC
            LIMIT ? OFFSET ?
            """,
                (doc_id, limit, offset),
            )
            .fetchall()
        )
        return [dict(row) for row in rows]

    def delete_chunk(self, chunk_id: str) -> bool:
        row = (
            self._conn()
            .execute(
                "SELECT kb_id, doc_id FROM chunks WHERE chunk_id=?",
                (chunk_id,),
            )
            .fetchone()
        )
        if not row:
            return False
        if self.fts_available:
            self._conn().execute("DELETE FROM chunks_fts WHERE chunk_id=?", (chunk_id,))
        cur = self._conn().execute("DELETE FROM chunks WHERE chunk_id=?", (chunk_id,))
        self._conn().commit()
        self.refresh_counts(row["kb_id"], row["doc_id"])
        return cur.rowcount > 0

    def vector_chunks(self, kb_ids: list[str]) -> list[dict]:
        if not kb_ids:
            return []
        rows = (
            self._conn()
            .execute(
                """
            SELECT c.*, d.doc_name, kb.name AS kb_name
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            JOIN knowledge_bases kb ON kb.kb_id = c.kb_id
            WHERE c.kb_id IN (SELECT value FROM json_each(?))
            """,
                (json.dumps(kb_ids),),
            )
            .fetchall()
        )
        return [self._decode_chunk(row) for row in rows]

    def keyword_search(self, query: str, kb_ids: list[str], limit: int) -> list[dict]:
        if not kb_ids or not query.strip():
            return []
        if self.fts_available:
            match_query = _fts_query(query)
            if match_query:
                try:
                    rows = (
                        self._conn()
                        .execute(
                            """
                        SELECT c.*, d.doc_name, kb.name AS kb_name, bm25(chunks_fts) AS rank
                        FROM chunks_fts
                        JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
                        JOIN documents d ON d.doc_id = c.doc_id
                        JOIN knowledge_bases kb ON kb.kb_id = c.kb_id
                        WHERE chunks_fts MATCH ? AND c.kb_id IN (SELECT value FROM json_each(?))
                        ORDER BY rank ASC
                        LIMIT ?
                        """,
                            (match_query, json.dumps(kb_ids), limit),
                        )
                        .fetchall()
                    )
                    return [
                        self._decode_chunk(row, sparse_score=-float(row["rank"])) for row in rows
                    ]
                except sqlite3.OperationalError:
                    pass

        like_terms = [f"%{term}%" for term in _query_terms(query)]
        if not like_terms:
            return []
        terms = [term.strip("%").lower() for term in like_terms]
        matches = []
        for row in self.vector_chunks(kb_ids):
            content = row["content"].lower()
            if any(term in content for term in terms):
                row["sparse_score"] = 1.0
                matches.append(row)
            if len(matches) >= limit:
                break
        return matches

    def create_task(self, kind: str, kb_id: str = "", status: str = "pending") -> dict:
        now = utc_timestamp()
        task_id = str(uuid.uuid4())
        self._conn().execute(
            """
            INSERT INTO tasks (task_id, kind, status, kb_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, kind, status, kb_id, now, now),
        )
        self._conn().commit()
        return self.get_task(task_id) or {}

    def update_task(
        self,
        task_id: str,
        *,
        status: str,
        result: dict | None = None,
        error: str = "",
    ) -> dict | None:
        self._conn().execute(
            "UPDATE tasks SET status=?, result=?, error=?, updated_at=? WHERE task_id=?",
            (status, json.dumps(result or {}, ensure_ascii=False), error, utc_timestamp(), task_id),
        )
        self._conn().commit()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict | None:
        row = self._conn().execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["result"] = json.loads(data.get("result") or "{}")
        return data

    def refresh_counts(self, kb_id: str, doc_id: str | None = None) -> None:
        conn = self._conn()
        if doc_id:
            conn.execute(
                """
                UPDATE documents
                SET chunk_count=(SELECT COUNT(*) FROM chunks WHERE doc_id=?), updated_at=?
                WHERE doc_id=?
                """,
                (doc_id, utc_timestamp(), doc_id),
            )
        conn.execute(
            """
            UPDATE knowledge_bases
            SET doc_count=(SELECT COUNT(*) FROM documents WHERE kb_id=?),
                chunk_count=(SELECT COUNT(*) FROM chunks WHERE kb_id=?),
                updated_at=?
            WHERE kb_id=?
            """,
            (kb_id, kb_id, utc_timestamp(), kb_id),
        )
        conn.commit()

    def _delete_fts_doc(self, doc_id: str) -> None:
        if self.fts_available:
            self._conn().execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))

    def _decode_kb(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["embedding_config"] = json.loads(data.get("embedding_config") or "{}")
        data["rerank_config"] = json.loads(data.get("rerank_config") or "{}")
        return data

    def _decode_chunk(self, row: sqlite3.Row, sparse_score: float = 0.0) -> dict:
        data = dict(row)
        data["embedding"] = json.loads(data.get("embedding") or "[]")
        data["sparse_score"] = sparse_score
        return data

    def _conn(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("knowledge store is not initialized")
        return self.conn


def _query_terms(query: str) -> list[str]:
    terms = []
    current = []
    for char in query.lower():
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            current.append(char)
        elif current:
            terms.append("".join(current))
            current = []
    if current:
        terms.append("".join(current))
    return [term for term in terms if len(term) > 1]


def _fts_query(query: str) -> str:
    terms = _query_terms(query)
    return " OR ".join(f'"{term}"' for term in terms[:12])


def _int_or_default(value: object, default: int) -> int:
    return default if value is None else int(cast(Any, value))


def _friendly_integrity_error(error: sqlite3.IntegrityError) -> ValueError:
    message = str(error)
    if "knowledge_bases.name" in message:
        return ValueError("knowledge base name already exists")
    return ValueError(message)
