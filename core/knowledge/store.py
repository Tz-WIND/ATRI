"""SQLite persistence for ATRI knowledge bases."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, cast

DEFAULT_EMBEDDING_CACHE_MAX_SIZE = 20_000


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

    def set_embedding_cache_max_size(self, value: object) -> None:
        self.embedding_cache_max_size = _nonnegative_int(
            value,
            DEFAULT_EMBEDDING_CACHE_MAX_SIZE,
        )
        self._trim_embedding_cache()
        self._trim_vector_candidate_cache()

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
        self._delete_embedding_cache_for_kb(kb_id)
        self._delete_vector_candidate_cache_for_kb(kb_id)
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
        self._delete_vector_candidate_cache_for_kb(kb_id)
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
        cur = self._conn().execute("DELETE FROM chunks WHERE chunk_id=?", (chunk_id,))
        self._conn().commit()
        self.refresh_counts(row["kb_id"], row["doc_id"])
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


def _friendly_integrity_error(error: sqlite3.IntegrityError) -> ValueError:
    message = str(error)
    if "knowledge_bases.name" in message:
        return ValueError("knowledge base name already exists")
    return ValueError(message)
