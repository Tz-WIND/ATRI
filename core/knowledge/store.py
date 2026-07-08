"""SQLite persistence for ATRI knowledge bases."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import numpy as np

from core.knowledge.vector_backend import delete_hnsw_sidecar_files

DEFAULT_EMBEDDING_CACHE_MAX_SIZE = 20_000
_EMBEDDING_BLOB_DTYPE = "float32"
_EMBEDDING_BLOB_REVISION = 1
_VECTOR_BACKEND_SQLITE_BLOB_NUMPY = "sqlite_blob_numpy"


@dataclass(frozen=True)
class _VectorMatrix:
    kb_id: str
    chunk_ids: tuple[str, ...]
    doc_ids: tuple[str, ...]
    chunk_indexes: tuple[int, ...]
    embeddings: np.ndarray
    norms: np.ndarray
    created_ats: tuple[float, ...]


def utc_timestamp() -> float:
    return time.time()


class KnowledgeStore:
    """Small SQLite data access layer for knowledge metadata and vectors."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        embedding_cache_max_size: int = DEFAULT_EMBEDDING_CACHE_MAX_SIZE,
    ) -> None:
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None
        self.fts_available = False
        self._embedding_cache: OrderedDict[str, tuple[float, tuple[float, ...]]] = OrderedDict()
        self._vector_candidate_cache: OrderedDict[str, tuple[dict, ...]] = OrderedDict()
        self._vector_candidate_cache_size = 0
        self._vector_matrix_cache: OrderedDict[str, _VectorMatrix] = OrderedDict()
        self._vector_matrix_cache_size = 0
        self.embedding_cache_max_size = _nonnegative_int(
            embedding_cache_max_size,
            DEFAULT_EMBEDDING_CACHE_MAX_SIZE,
        )

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
        self._embedding_cache.clear()
        self._vector_candidate_cache.clear()
        self._vector_candidate_cache_size = 0
        self._vector_matrix_cache.clear()
        self._vector_matrix_cache_size = 0

    def set_embedding_cache_max_size(self, value: object) -> None:
        self.embedding_cache_max_size = _nonnegative_int(
            value,
            DEFAULT_EMBEDDING_CACHE_MAX_SIZE,
        )
        self._trim_embedding_cache()
        self._trim_vector_candidate_cache()
        self._trim_vector_matrix_cache()

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
                embedding_blob BLOB,
                embedding_dtype TEXT NOT NULL DEFAULT 'float32',
                embedding_revision INTEGER NOT NULL DEFAULT 1,
                embedding_norm REAL NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS document_payloads (
                doc_id TEXT PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                parser_metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS document_indexes (
                index_id TEXT PRIMARY KEY,
                kb_id TEXT NOT NULL REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
                index_type TEXT NOT NULL,
                status TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                observed_version INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_reconciled_at REAL,
                UNIQUE(doc_id, index_type)
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
            CREATE INDEX IF NOT EXISTS idx_document_indexes_doc_id ON document_indexes(doc_id);
            CREATE INDEX IF NOT EXISTS idx_document_indexes_reconcile
                ON document_indexes(status, observed_version, version);
            """
        )
        self._ensure_chunk_vector_columns(conn)
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                "USING fts5(chunk_id UNINDEXED, kb_id UNINDEXED, doc_id UNINDEXED, content)"
            )
            self.fts_available = True
        except sqlite3.OperationalError:
            self.fts_available = False
        conn.commit()

    def _ensure_chunk_vector_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
        if "embedding_blob" not in columns:
            conn.execute("ALTER TABLE chunks ADD COLUMN embedding_blob BLOB")
        if "embedding_dtype" not in columns:
            conn.execute(
                "ALTER TABLE chunks ADD COLUMN embedding_dtype TEXT NOT NULL DEFAULT 'float32'"
            )
        if "embedding_revision" not in columns:
            conn.execute(
                "ALTER TABLE chunks ADD COLUMN embedding_revision INTEGER NOT NULL DEFAULT 1"
            )

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
        self._delete_embedding_cache_for_kb(kb_id)
        self._delete_vector_candidate_cache_for_kb(kb_id)
        self._delete_vector_matrix_cache_for_kb(kb_id)
        cur = self._conn().execute("DELETE FROM knowledge_bases WHERE kb_id=?", (kb_id,))
        self._conn().commit()
        deleted = cur.rowcount > 0
        if deleted:
            delete_hnsw_sidecar_files(kb_id)
        return deleted

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
            embedding_blob = _embedding_to_float32_blob(vector)
            conn.execute(
                """
                INSERT INTO chunks
                    (chunk_id, kb_id, doc_id, chunk_index, content, char_count,
                     embedding, embedding_blob, embedding_dtype, embedding_revision,
                     embedding_norm, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    kb_id,
                    doc_id,
                    index,
                    content,
                    len(content),
                    json.dumps(vector),
                    embedding_blob,
                    _EMBEDDING_BLOB_DTYPE,
                    _EMBEDDING_BLOB_REVISION,
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
        self._delete_vector_candidate_cache_for_kb(kb_id)
        self._delete_vector_matrix_cache_for_kb(kb_id)
        self.refresh_counts(kb_id, doc_id)

    def replace_chunks(
        self,
        kb_id: str,
        doc_id: str,
        chunks: list[tuple[str, list[float]]],
    ) -> None:
        self._delete_fts_doc(doc_id)
        self._delete_embedding_cache_for_doc(doc_id)
        self._delete_vector_candidate_cache_for_kb(kb_id)
        self._delete_vector_matrix_cache_for_kb(kb_id)
        self._conn().execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        self._conn().commit()
        if chunks:
            self.add_chunks(kb_id, doc_id, chunks)
        else:
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
        self._delete_embedding_cache_for_doc(doc_id)
        self._delete_vector_candidate_cache_for_kb(doc["kb_id"])
        self._delete_vector_matrix_cache_for_kb(doc["kb_id"])
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
        self._embedding_cache.pop(chunk_id, None)
        self._delete_vector_candidate_cache_for_kb(row["kb_id"])
        self._delete_vector_matrix_cache_for_kb(row["kb_id"])
        cur = self._conn().execute("DELETE FROM chunks WHERE chunk_id=?", (chunk_id,))
        self._conn().commit()
        self.refresh_counts(row["kb_id"], row["doc_id"])
        return cur.rowcount > 0

    def save_document_payload(
        self,
        doc_id: str,
        content: str,
        *,
        parser_metadata: dict[str, Any] | None = None,
    ) -> dict:
        now = utc_timestamp()
        digest = sha256(content.encode("utf-8")).hexdigest()
        self._conn().execute(
            """
            INSERT INTO document_payloads
                (doc_id, content, content_sha256, parser_metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(doc_id) DO UPDATE SET
                content=excluded.content,
                content_sha256=excluded.content_sha256,
                parser_metadata=excluded.parser_metadata,
                updated_at=excluded.updated_at
            """,
            (
                doc_id,
                content,
                digest,
                json.dumps(parser_metadata or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        self._conn().commit()
        return self.get_document_payload(doc_id) or {}

    def get_document_payload(self, doc_id: str) -> dict | None:
        row = (
            self._conn()
            .execute("SELECT * FROM document_payloads WHERE doc_id=?", (doc_id,))
            .fetchone()
        )
        if not row:
            return None
        data = dict(row)
        data["parser_metadata"] = json.loads(data.get("parser_metadata") or "{}")
        return data

    def request_document_indexes(
        self,
        *,
        kb_id: str,
        doc_id: str,
        index_types: list[str],
    ) -> list[dict]:
        now = utc_timestamp()
        conn = self._conn()
        for index_type in index_types:
            cleaned_type = str(index_type).strip()
            if not cleaned_type:
                continue
            existing = conn.execute(
                """
                SELECT index_id, version
                FROM document_indexes
                WHERE doc_id=? AND index_type=?
                """,
                (doc_id, cleaned_type),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE document_indexes
                    SET status='pending',
                        version=?,
                        error='',
                        result='{}',
                        updated_at=?
                    WHERE index_id=?
                    """,
                    (int(existing["version"]) + 1, now, existing["index_id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO document_indexes
                        (index_id, kb_id, doc_id, index_type, status, version,
                         observed_version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'pending', 1, 0, ?, ?)
                    """,
                    (str(uuid.uuid4()), kb_id, doc_id, cleaned_type, now, now),
                )
        conn.commit()
        return self.list_document_indexes(doc_id)

    def record_document_index_active(
        self,
        *,
        kb_id: str,
        doc_id: str,
        index_type: str,
        result: dict[str, Any] | None = None,
    ) -> dict | None:
        now = utc_timestamp()
        self._conn().execute(
            """
            INSERT INTO document_indexes
                (index_id, kb_id, doc_id, index_type, status, version,
                 observed_version, error, result, created_at, updated_at,
                 last_reconciled_at)
            VALUES (?, ?, ?, ?, 'active', 1, 1, '', ?, ?, ?, ?)
            ON CONFLICT(doc_id, index_type) DO UPDATE SET
                status='active',
                observed_version=document_indexes.version,
                error='',
                result=excluded.result,
                updated_at=excluded.updated_at,
                last_reconciled_at=excluded.last_reconciled_at
            """,
            (
                str(uuid.uuid4()),
                kb_id,
                doc_id,
                str(index_type).strip(),
                json.dumps(result or {}, ensure_ascii=False),
                now,
                now,
                now,
            ),
        )
        self._conn().commit()
        indexes = self.list_document_indexes(doc_id)
        return next(
            (item for item in indexes if item["index_type"] == str(index_type).strip()),
            None,
        )

    def record_document_index_queued(
        self,
        *,
        kb_id: str,
        doc_id: str,
        index_type: str,
        result: dict[str, Any] | None = None,
    ) -> dict | None:
        now = utc_timestamp()
        self._conn().execute(
            """
            INSERT INTO document_indexes
                (index_id, kb_id, doc_id, index_type, status, version,
                 observed_version, error, result, created_at, updated_at,
                 last_reconciled_at)
            VALUES (?, ?, ?, ?, 'queued', 1, 1, '', ?, ?, ?, ?)
            ON CONFLICT(doc_id, index_type) DO UPDATE SET
                status='queued',
                observed_version=document_indexes.version,
                error='',
                result=excluded.result,
                updated_at=excluded.updated_at,
                last_reconciled_at=excluded.last_reconciled_at
            """,
            (
                str(uuid.uuid4()),
                kb_id,
                doc_id,
                str(index_type).strip(),
                json.dumps(result or {}, ensure_ascii=False),
                now,
                now,
                now,
            ),
        )
        self._conn().commit()
        indexes = self.list_document_indexes(doc_id)
        return next(
            (item for item in indexes if item["index_type"] == str(index_type).strip()),
            None,
        )

    def list_document_indexes(self, doc_id: str) -> list[dict]:
        rows = (
            self._conn()
            .execute(
                """
                SELECT doc_id, index_type, status, version, observed_version, error
                FROM document_indexes
                WHERE doc_id=?
                ORDER BY
                    CASE index_type
                        WHEN 'vector_fulltext' THEN 0
                        WHEN 'graph' THEN 1
                        ELSE 2
                    END,
                    index_type ASC
                """,
                (doc_id,),
            )
            .fetchall()
        )
        return [dict(row) for row in rows]

    def list_indexes_needing_reconciliation_for_document(
        self,
        *,
        doc_id: str,
        index_types: list[str],
    ) -> list[dict]:
        cleaned_types = [str(item).strip() for item in index_types if str(item).strip()]
        if not cleaned_types:
            return []
        rows = (
            self._conn()
            .execute(
                """
                SELECT index_id, kb_id, doc_id, index_type, status, version,
                       observed_version, error
                FROM document_indexes
                WHERE doc_id=?
                    AND index_type IN (SELECT value FROM json_each(?))
                    AND (
                        (status='pending' AND observed_version < version)
                        OR status='deleting'
                    )
                ORDER BY
                    CASE index_type
                        WHEN 'vector_fulltext' THEN 0
                        WHEN 'graph' THEN 1
                        ELSE 2
                    END,
                    index_type ASC
                """,
                (doc_id, json.dumps(cleaned_types)),
            )
            .fetchall()
        )
        return [dict(row) for row in rows]

    def list_indexes_needing_reconciliation(self, *, limit: int = 20) -> list[dict]:
        rows = (
            self._conn()
            .execute(
                """
                SELECT index_id, kb_id, doc_id, index_type, status, version,
                       observed_version, error
                FROM document_indexes
                WHERE
                    (status='pending' AND observed_version < version)
                    OR status='deleting'
                ORDER BY
                    updated_at ASC,
                    CASE index_type
                        WHEN 'vector_fulltext' THEN 0
                        WHEN 'graph' THEN 1
                        ELSE 2
                    END,
                    index_type ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
            .fetchall()
        )
        return [dict(row) for row in rows]

    def claim_document_index(self, index_id: str) -> dict | None:
        row = (
            self._conn()
            .execute(
                """
                SELECT index_id, kb_id, doc_id, index_type, status, version, observed_version
                FROM document_indexes
                WHERE index_id=?
                """,
                (index_id,),
            )
            .fetchone()
        )
        if row is None:
            return None
        data = dict(row)
        now = utc_timestamp()
        if data["status"] == "pending" and int(data["observed_version"]) < int(data["version"]):
            cur = self._conn().execute(
                """
                UPDATE document_indexes
                SET status='creating',
                    updated_at=?,
                    last_reconciled_at=?
                WHERE index_id=?
                    AND status='pending'
                    AND observed_version < version
                """,
                (now, now, index_id),
            )
            self._conn().commit()
            if cur.rowcount <= 0:
                return None
            data["action"] = "create" if int(data["version"]) == 1 else "update"
            data["target_version"] = int(data["version"])
            data["status"] = "creating"
            return data
        if data["status"] == "deleting":
            cur = self._conn().execute(
                """
                UPDATE document_indexes
                SET status='deletion_in_progress',
                    updated_at=?,
                    last_reconciled_at=?
                WHERE index_id=? AND status='deleting'
                """,
                (now, now, index_id),
            )
            self._conn().commit()
            if cur.rowcount <= 0:
                return None
            data["action"] = "delete"
            data["target_version"] = None
            data["status"] = "deletion_in_progress"
            return data
        return None

    def complete_document_index(
        self,
        *,
        doc_id: str,
        index_type: str,
        target_version: int,
        result: dict[str, Any] | None = None,
    ) -> bool:
        cur = self._conn().execute(
            """
            UPDATE document_indexes
            SET status='active',
                observed_version=?,
                error='',
                result=?,
                updated_at=?
            WHERE doc_id=?
                AND index_type=?
                AND status='creating'
                AND version=?
            """,
            (
                int(target_version),
                json.dumps(result or {}, ensure_ascii=False),
                utc_timestamp(),
                doc_id,
                index_type,
                int(target_version),
            ),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def queue_document_index(
        self,
        *,
        doc_id: str,
        index_type: str,
        target_version: int,
        result: dict[str, Any] | None = None,
    ) -> bool:
        cur = self._conn().execute(
            """
            UPDATE document_indexes
            SET status='queued',
                observed_version=?,
                error='',
                result=?,
                updated_at=?
            WHERE doc_id=?
                AND index_type=?
                AND status='creating'
                AND version=?
            """,
            (
                int(target_version),
                json.dumps(result or {}, ensure_ascii=False),
                utc_timestamp(),
                doc_id,
                index_type,
                int(target_version),
            ),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def fail_document_index(self, *, index_id: str, error: str) -> bool:
        cur = self._conn().execute(
            """
            UPDATE document_indexes
            SET status='failed',
                error=?,
                updated_at=?
            WHERE index_id=?
                AND status IN ('creating', 'deletion_in_progress')
            """,
            (error, utc_timestamp(), index_id),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def reset_stale_document_indexes(self, *, timeout_seconds: float) -> int:
        cutoff = utc_timestamp() - max(1.0, float(timeout_seconds))
        cur = self._conn().execute(
            """
            UPDATE document_indexes
            SET status='pending',
                error='index claim timed out; queued for retry',
                updated_at=?
            WHERE status='creating'
                AND COALESCE(last_reconciled_at, updated_at) < ?
                AND observed_version < version
            """,
            (utc_timestamp(), cutoff),
        )
        self._conn().commit()
        return int(cur.rowcount)

    def delete_document_index(self, *, doc_id: str, index_type: str) -> bool:
        cur = self._conn().execute(
            """
            DELETE FROM document_indexes
            WHERE doc_id=? AND index_type=? AND status='deletion_in_progress'
            """,
            (doc_id, index_type),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def vector_chunks(self, kb_ids: list[str]) -> list[dict]:
        if not kb_ids:
            return []
        candidates = self.vector_chunk_candidates(kb_ids)
        hydrated = self.chunks_by_ids([row["chunk_id"] for row in candidates])
        hydrated_by_id = {row["chunk_id"]: row for row in hydrated}
        return [
            {**row, **hydrated_by_id[row["chunk_id"]]}
            for row in candidates
            if row["chunk_id"] in hydrated_by_id
        ]

    def vector_chunk_candidates(self, kb_ids: list[str]) -> list[dict]:
        if not kb_ids:
            return []
        if self.embedding_cache_max_size <= 0:
            return self._load_vector_chunk_candidates(kb_ids)

        missing_kb_ids = [kb_id for kb_id in kb_ids if kb_id not in self._vector_candidate_cache]
        if missing_kb_ids:
            loaded = self._load_vector_chunk_candidates(missing_kb_ids)
            grouped: dict[str, list[dict]] = {}
            for row in loaded:
                grouped.setdefault(row["kb_id"], []).append(row)
            loaded_grouped = {
                kb_id: tuple(dict(row) for row in grouped.get(kb_id, []))
                for kb_id in missing_kb_ids
            }
            for kb_id in missing_kb_ids:
                self._cache_vector_candidates(kb_id, list(loaded_grouped[kb_id]))
        else:
            loaded_grouped = {}

        rows: list[dict] = []
        for kb_id in kb_ids:
            cached = self._vector_candidate_cache.get(kb_id)
            if cached is not None:
                self._vector_candidate_cache.move_to_end(kb_id)
                rows.extend(dict(row) for row in cached)
            else:
                rows.extend(dict(row) for row in loaded_grouped.get(kb_id, ()))
        return rows

    def _load_vector_chunk_candidates(self, kb_ids: list[str]) -> list[dict]:
        if not kb_ids:
            return []
        rows = (
            self._conn()
            .execute(
                """
            SELECT c.chunk_id, c.kb_id, c.doc_id, c.chunk_index,
                   c.embedding, c.embedding_norm, c.created_at
            FROM chunks c
            WHERE c.kb_id IN (SELECT value FROM json_each(?))
            """,
                (json.dumps(kb_ids),),
            )
            .fetchall()
        )
        return [self._decode_chunk(row) for row in rows]

    def vector_index_snapshot(self, kb_id: str) -> dict:
        row = (
            self._conn()
            .execute(
                """
            SELECT kb_id, embedding_dimensions, chunk_count, updated_at
            FROM knowledge_bases
            WHERE kb_id=?
            """,
                (kb_id,),
            )
            .fetchone()
        )
        if row is None:
            return {
                "kb_id": kb_id,
                "embedding_dimensions": 0,
                "chunk_count": 0,
                "updated_at": 0.0,
            }
        return dict(row)

    def vector_index_rows(self, kb_id: str) -> list[dict]:
        rows = (
            self._conn()
            .execute(
                """
            SELECT c.chunk_id, c.kb_id, c.doc_id, c.chunk_index, c.embedding,
                   c.embedding_blob, c.embedding_dtype, c.embedding_revision,
                   c.embedding_norm, c.created_at
            FROM chunks c
            WHERE c.kb_id=?
            ORDER BY c.rowid ASC
            """,
                (kb_id,),
            )
            .fetchall()
        )
        return [dict(row) for row in rows]

    def vector_index_rows_by_chunk_ids(self, chunk_ids: list[str]) -> list[dict]:
        if not chunk_ids:
            return []
        rows = (
            self._conn()
            .execute(
                """
            SELECT c.chunk_id, c.kb_id, c.doc_id, c.chunk_index, c.embedding,
                   c.embedding_blob, c.embedding_dtype, c.embedding_revision,
                   c.embedding_norm, c.created_at
            FROM chunks c
            WHERE c.chunk_id IN (SELECT value FROM json_each(?))
            """,
                (json.dumps(chunk_ids),),
            )
            .fetchall()
        )
        by_id = {str(row["chunk_id"]): dict(row) for row in rows}
        return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]

    def dense_vector_search(
        self,
        kb_ids: list[str],
        query_vectors: dict[str, list[float]],
        limits: dict[str, int],
        timings: dict[str, Any] | None = None,
    ) -> list[dict]:
        if not kb_ids:
            _set_timing(timings, "vector_matrix_load_ms", 0.0)
            _set_timing(timings, "vector_matmul_ms", 0.0)
            _record_count(timings, "vector_rows", 0)
            return []

        _set_value(timings, "vector_backend", _VECTOR_BACKEND_SQLITE_BLOB_NUMPY)
        matrices: list[_VectorMatrix] = []
        total_rows = 0
        load_ms = 0.0
        for kb_id in kb_ids:
            load_started_at = time.perf_counter()
            matrix = self._vector_matrix_for_kb(kb_id)
            load_ms += (time.perf_counter() - load_started_at) * 1000
            matrices.append(matrix)
            total_rows += len(matrix.chunk_ids)
        _set_timing(timings, "vector_matrix_load_ms", load_ms)
        _set_timing(timings, "vector_store_ms", load_ms)
        _record_count(timings, "vector_rows", total_rows)

        ranked_entries: list[tuple[float, int, dict]] = []
        sequence_base = 0
        matmul_ms = 0.0
        dimension_mismatches = 0
        for matrix in matrices:
            query_vector = query_vectors.get(matrix.kb_id)
            if not query_vector or len(matrix.chunk_ids) == 0:
                sequence_base += len(matrix.chunk_ids)
                continue
            query = np.asarray(query_vector, dtype=np.float32)
            if matrix.embeddings.shape[1] != query.shape[0]:
                dimension_mismatches += 1
                sequence_base += len(matrix.chunk_ids)
                continue
            query_norm = float(np.linalg.norm(query))
            if query_norm <= 0:
                sequence_base += len(matrix.chunk_ids)
                continue

            matmul_started_at = time.perf_counter()
            dots = matrix.embeddings @ query
            denominators = matrix.norms * query_norm
            scores = np.divide(
                dots,
                denominators,
                out=np.zeros_like(dots, dtype=np.float32),
                where=denominators > 0,
            )
            matmul_ms += (time.perf_counter() - matmul_started_at) * 1000

            limit = _positive_int(limits.get(matrix.kb_id), 30)
            selected_indexes = _top_score_indexes(scores, limit)
            for index in selected_indexes:
                score = float(scores[index])
                sequence = sequence_base + int(index)
                ranked_entries.append(
                    (
                        score,
                        sequence,
                        {
                            "chunk_id": matrix.chunk_ids[index],
                            "kb_id": matrix.kb_id,
                            "doc_id": matrix.doc_ids[index],
                            "chunk_index": matrix.chunk_indexes[index],
                            "embedding_norm": float(matrix.norms[index]),
                            "created_at": matrix.created_ats[index],
                            "dense_score": score,
                        },
                    )
                )
            sequence_base += len(matrix.chunk_ids)

        _set_timing(timings, "vector_matmul_ms", matmul_ms)
        if dimension_mismatches:
            _record_count(timings, "vector_dimension_mismatches", dimension_mismatches)
        ranked_entries.sort(key=lambda item: (-item[0], item[1]))
        return [row for _, _, row in ranked_entries]

    def _vector_matrix_for_kb(self, kb_id: str) -> _VectorMatrix:
        if self.embedding_cache_max_size > 0:
            cached = self._vector_matrix_cache.get(kb_id)
            if cached is not None:
                self._vector_matrix_cache.move_to_end(kb_id)
                return cached

        rows = (
            self._conn()
            .execute(
                """
            SELECT c.chunk_id, c.kb_id, c.doc_id, c.chunk_index, c.embedding,
                   c.embedding_blob, c.embedding_dtype, c.embedding_revision,
                   c.embedding_norm, c.created_at
            FROM chunks c
            WHERE c.kb_id=?
            ORDER BY c.rowid ASC
            """,
                (kb_id,),
            )
            .fetchall()
        )
        vectors: list[np.ndarray] = []
        chunk_ids: list[str] = []
        doc_ids: list[str] = []
        chunk_indexes: list[int] = []
        norms: list[float] = []
        created_ats: list[float] = []
        dimension: int | None = None
        backfilled = False
        for row in rows:
            vector, did_backfill = self._vector_from_blob_or_json(row)
            backfilled = backfilled or did_backfill
            if vector.size == 0:
                continue
            if dimension is None:
                dimension = int(vector.shape[0])
            if int(vector.shape[0]) != dimension:
                continue
            vectors.append(vector)
            chunk_ids.append(str(row["chunk_id"]))
            doc_ids.append(str(row["doc_id"]))
            chunk_indexes.append(int(row["chunk_index"]))
            norms.append(float(row["embedding_norm"]))
            created_ats.append(float(row["created_at"]))
        if backfilled:
            self._conn().commit()

        embeddings = (
            np.vstack(vectors).astype(np.float32, copy=False)
            if vectors
            else np.empty((0, 0), dtype=np.float32)
        )
        matrix = _VectorMatrix(
            kb_id=kb_id,
            chunk_ids=tuple(chunk_ids),
            doc_ids=tuple(doc_ids),
            chunk_indexes=tuple(chunk_indexes),
            embeddings=embeddings,
            norms=np.asarray(norms, dtype=np.float32),
            created_ats=tuple(created_ats),
        )
        self._cache_vector_matrix(kb_id, matrix)
        return matrix

    def _vector_from_blob_or_json(self, row: sqlite3.Row) -> tuple[np.ndarray, bool]:
        blob = row["embedding_blob"]
        dtype = str(row["embedding_dtype"] or "")
        revision = int(row["embedding_revision"] or 0)
        if (
            blob is not None
            and dtype == _EMBEDDING_BLOB_DTYPE
            and revision == _EMBEDDING_BLOB_REVISION
        ):
            return np.frombuffer(blob, dtype=np.float32), False

        vector = np.asarray(json.loads(row["embedding"] or "[]"), dtype=np.float32)
        self._conn().execute(
            """
            UPDATE chunks
            SET embedding_blob=?, embedding_dtype=?, embedding_revision=?
            WHERE chunk_id=?
            """,
            (
                vector.tobytes(),
                _EMBEDDING_BLOB_DTYPE,
                _EMBEDDING_BLOB_REVISION,
                row["chunk_id"],
            ),
        )
        return vector, True

    def _cache_vector_matrix(self, kb_id: str, matrix: _VectorMatrix) -> bool:
        if (
            self.embedding_cache_max_size <= 0
            or len(matrix.chunk_ids) > self.embedding_cache_max_size
        ):
            return False
        existing = self._vector_matrix_cache.pop(kb_id, None)
        if existing is not None:
            self._vector_matrix_cache_size -= len(existing.chunk_ids)
        self._vector_matrix_cache[kb_id] = matrix
        self._vector_matrix_cache_size += len(matrix.chunk_ids)
        self._trim_vector_matrix_cache()
        return True

    def _cache_vector_candidates(self, kb_id: str, rows: list[dict]) -> bool:
        if self.embedding_cache_max_size <= 0 or len(rows) > self.embedding_cache_max_size:
            return False
        existing = self._vector_candidate_cache.pop(kb_id, None)
        if existing is not None:
            self._vector_candidate_cache_size -= len(existing)
        self._vector_candidate_cache[kb_id] = tuple(dict(row) for row in rows)
        self._vector_candidate_cache_size += len(rows)
        self._trim_vector_candidate_cache()
        return True

    def chunks_by_ids(self, chunk_ids: list[str]) -> list[dict]:
        if not chunk_ids:
            return []
        rows = (
            self._conn()
            .execute(
                """
            SELECT c.chunk_id, c.kb_id, c.doc_id, c.chunk_index, c.content,
                   c.char_count, c.created_at, d.doc_name, kb.name AS kb_name
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            JOIN knowledge_bases kb ON kb.kb_id = c.kb_id
            WHERE c.chunk_id IN (SELECT value FROM json_each(?))
            """,
                (json.dumps(chunk_ids),),
            )
            .fetchall()
        )
        by_id = {row["chunk_id"]: self._decode_search_chunk(row) for row in rows}
        return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]

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
                        SELECT c.chunk_id, c.kb_id, c.doc_id, c.chunk_index, c.content,
                               c.char_count, c.created_at, d.doc_name, kb.name AS kb_name,
                               bm25(chunks_fts) AS rank
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
                        self._decode_search_chunk(row, sparse_score=-float(row["rank"]))
                        for row in rows
                    ]
                except sqlite3.OperationalError:
                    pass

        like_terms = [f"%{term}%" for term in _query_terms(query)]
        if not like_terms:
            return []
        terms = [term.strip("%").lower() for term in like_terms]
        matches = []
        rows = (
            self._conn()
            .execute(
                """
            SELECT c.chunk_id, c.kb_id, c.doc_id, c.chunk_index, c.content,
                   c.char_count, c.created_at, d.doc_name, kb.name AS kb_name
            FROM chunks c
            JOIN documents d ON d.doc_id = c.doc_id
            JOIN knowledge_bases kb ON kb.kb_id = c.kb_id
            WHERE c.kb_id IN (SELECT value FROM json_each(?))
            """,
                (json.dumps(kb_ids),),
            )
            .fetchall()
        )
        for raw_row in rows:
            row = self._decode_search_chunk(raw_row)
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

    def _delete_embedding_cache_for_doc(self, doc_id: str) -> None:
        chunk_ids = [
            row["chunk_id"]
            for row in self._conn().execute(
                "SELECT chunk_id FROM chunks WHERE doc_id=?",
                (doc_id,),
            )
        ]
        for chunk_id in chunk_ids:
            self._embedding_cache.pop(chunk_id, None)

    def _delete_embedding_cache_for_kb(self, kb_id: str) -> None:
        chunk_ids = [
            row["chunk_id"]
            for row in self._conn().execute(
                "SELECT chunk_id FROM chunks WHERE kb_id=?",
                (kb_id,),
            )
        ]
        for chunk_id in chunk_ids:
            self._embedding_cache.pop(chunk_id, None)

    def _delete_vector_candidate_cache_for_kb(self, kb_id: str) -> None:
        rows = self._vector_candidate_cache.pop(kb_id, None)
        if rows is not None:
            self._vector_candidate_cache_size -= len(rows)

    def _delete_vector_matrix_cache_for_kb(self, kb_id: str) -> None:
        matrix = self._vector_matrix_cache.pop(kb_id, None)
        if matrix is not None:
            self._vector_matrix_cache_size -= len(matrix.chunk_ids)

    def _decode_kb(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["embedding_config"] = json.loads(data.get("embedding_config") or "{}")
        data["rerank_config"] = json.loads(data.get("rerank_config") or "{}")
        return data

    def _decode_chunk(self, row: sqlite3.Row, sparse_score: float = 0.0) -> dict:
        data = dict(row)
        data["embedding"] = self._decode_embedding(row)
        data["sparse_score"] = sparse_score
        return data

    def _decode_search_chunk(self, row: sqlite3.Row, sparse_score: float = 0.0) -> dict:
        data = dict(row)
        data["sparse_score"] = sparse_score
        return data

    def _decode_embedding(self, row: sqlite3.Row) -> list[float]:
        chunk_id = str(row["chunk_id"])
        created_at = float(row["created_at"])
        if self.embedding_cache_max_size <= 0:
            return list(json.loads(row["embedding"] or "[]"))
        cached = self._embedding_cache.get(chunk_id)
        if cached is not None and cached[0] == created_at:
            self._embedding_cache.move_to_end(chunk_id)
            return list(cached[1])
        decoded = tuple(json.loads(row["embedding"] or "[]"))
        self._embedding_cache[chunk_id] = (created_at, decoded)
        self._embedding_cache.move_to_end(chunk_id)
        self._trim_embedding_cache()
        return list(decoded)

    def _trim_embedding_cache(self) -> None:
        if self.embedding_cache_max_size <= 0:
            self._embedding_cache.clear()
            return
        while len(self._embedding_cache) > self.embedding_cache_max_size:
            self._embedding_cache.popitem(last=False)

    def _trim_vector_candidate_cache(self) -> None:
        if self.embedding_cache_max_size <= 0:
            self._vector_candidate_cache.clear()
            self._vector_candidate_cache_size = 0
            return
        while self._vector_candidate_cache_size > self.embedding_cache_max_size:
            _, rows = self._vector_candidate_cache.popitem(last=False)
            self._vector_candidate_cache_size -= len(rows)

    def _trim_vector_matrix_cache(self) -> None:
        if self.embedding_cache_max_size <= 0:
            self._vector_matrix_cache.clear()
            self._vector_matrix_cache_size = 0
            return
        while self._vector_matrix_cache_size > self.embedding_cache_max_size:
            _, matrix = self._vector_matrix_cache.popitem(last=False)
            self._vector_matrix_cache_size -= len(matrix.chunk_ids)

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


def _nonnegative_int(value: object, default: int) -> int:
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError):
        parsed = default
    return max(0, parsed)


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _embedding_to_float32_blob(vector: list[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _top_score_indexes(scores: np.ndarray, limit: int) -> list[int]:
    if scores.size == 0:
        return []
    count = min(max(1, int(limit)), int(scores.size))
    indexes = np.arange(scores.size)
    order = np.lexsort((indexes, -scores))
    return [int(index) for index in order[:count]]


def _set_value(timings: dict[str, Any] | None, key: str, value: Any) -> None:
    if timings is not None:
        timings[key] = value


def _set_timing(timings: dict[str, Any] | None, key: str, elapsed_ms: float) -> None:
    if timings is not None:
        timings[key] = float(elapsed_ms)


def _record_count(timings: dict[str, Any] | None, key: str, value: int) -> None:
    if timings is not None:
        timings[key] = int(value)


def _friendly_integrity_error(error: sqlite3.IntegrityError) -> ValueError:
    message = str(error)
    if "knowledge_bases.name" in message:
        return ValueError("knowledge base name already exists")
    return ValueError(message)
