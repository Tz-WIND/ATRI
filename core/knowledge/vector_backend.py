"""Dense vector search backends for knowledge retrieval."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from heapq import heappush, heapreplace
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np

from core import logger

_EMBEDDING_BLOB_DTYPE = "float32"
_EMBEDDING_BLOB_REVISION = 1
_HNSW_SCHEMA_VERSION = 1
_VECTOR_BACKEND_HNSW = "hnsw"
DEFAULT_HNSW_INDEX_DIR = "data/knowledge/vector_indexes"


class VectorBackend(Protocol):
    def search(
        self,
        *,
        kb_ids: list[str],
        query_vectors: dict[str, list[float]],
        options: dict[str, dict],
        timings: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Return top dense candidates without hydrating chunk content."""


@dataclass
class _LoadedHnswIndex:
    index: Any
    metadata: dict[str, Any]


class HnswVectorBackend:
    """Approximate dense retrieval using a persisted HNSW sidecar index."""

    def __init__(
        self,
        store: Any,
        *,
        index_dir: str | Path = DEFAULT_HNSW_INDEX_DIR,
        fallback: VectorBackend | None = None,
        candidate_k: int = 300,
        ef_search: int = 128,
        m: int = 32,
        ef_construction: int = 200,
        hnswlib_module: Any | None = None,
    ) -> None:
        self.store = store
        self.index_dir = Path(index_dir)
        self.fallback = fallback or SQLiteBlobNumpyVectorBackend(store)
        self.candidate_k = _positive_limit(candidate_k, 300)
        self.ef_search = _positive_limit(ef_search, 128)
        self.m = _positive_limit(m, 32)
        self.ef_construction = _positive_limit(ef_construction, 200)
        self._hnswlib_module = hnswlib_module
        self._hnswlib_import_attempted = hnswlib_module is not None
        self._index_cache: dict[str, _LoadedHnswIndex] = {}

    def delete_index(self, kb_id: str) -> None:
        self._index_cache.pop(kb_id, None)
        delete_hnsw_sidecar_files(kb_id, self.index_dir)

    def search(
        self,
        *,
        kb_ids: list[str],
        query_vectors: dict[str, list[float]],
        options: dict[str, dict],
        timings: dict[str, Any] | None = None,
    ) -> list[dict]:
        hnswlib = self._hnswlib()
        if hnswlib is None:
            _record_value(timings, "ann_unavailable", True)
            return self.fallback.search(
                kb_ids=kb_ids,
                query_vectors=query_vectors,
                options=options,
                timings=timings,
            )

        try:
            return self._search_with_hnsw(
                hnswlib=hnswlib,
                kb_ids=kb_ids,
                query_vectors=query_vectors,
                options=options,
                timings=timings,
            )
        except Exception as e:
            logger.warning("Knowledge HNSW vector backend failed; using exact search: %s", e)
            return self.fallback.search(
                kb_ids=kb_ids,
                query_vectors=query_vectors,
                options=options,
                timings=timings,
            )

    def _search_with_hnsw(
        self,
        *,
        hnswlib: Any,
        kb_ids: list[str],
        query_vectors: dict[str, list[float]],
        options: dict[str, dict],
        timings: dict[str, Any] | None,
    ) -> list[dict]:
        _record_value(timings, "vector_backend", _VECTOR_BACKEND_HNSW)
        ranked_entries: list[tuple[float, int, dict]] = []
        sequence = 0
        for kb_id in kb_ids:
            option = options.get(kb_id, {})
            limit = _positive_limit(option.get("top_k_dense"), 30)
            query_vector = query_vectors.get(kb_id)
            if not query_vector:
                continue
            query = np.asarray(query_vector, dtype=np.float32)
            query_norm = float(np.linalg.norm(query))
            if query_norm <= 0:
                continue

            loaded = self._load_or_build_index(
                hnswlib=hnswlib,
                kb_id=kb_id,
                timings=timings,
            )
            if loaded is None:
                continue
            metadata_rows = list(loaded.metadata.get("rows") or [])
            if not metadata_rows:
                continue
            dimension = int(loaded.metadata.get("dimension") or 0)
            if dimension != int(query.shape[0]):
                _record_count(timings, "vector_dimension_mismatches", 1)
                continue

            indexed_count = len(metadata_rows)
            _add_count(timings, "vector_rows", indexed_count)
            candidate_limit = min(max(limit, self.candidate_k), indexed_count)
            loaded.index.set_ef(max(self.ef_search, candidate_limit))
            query_started_at = time.perf_counter()
            labels, _distances = loaded.index.knn_query(
                np.asarray([query], dtype=np.float32),
                k=candidate_limit,
            )
            _add_timing(timings, "ann_query_ms", query_started_at)
            label_values = _flatten_hnsw_labels(labels)
            _add_count(timings, "ann_candidates", len(label_values))

            label_to_metadata = {int(row["label"]): row for row in metadata_rows if "label" in row}
            candidate_metadata = [
                label_to_metadata[int(label)]
                for label in label_values
                if int(label) in label_to_metadata
            ]
            rescore_started_at = time.perf_counter()
            kb_entries = self._rescore_candidates(
                query=query,
                query_norm=query_norm,
                candidate_metadata=candidate_metadata,
                sequence_start=sequence,
            )
            _add_timing(timings, "ann_rescore_ms", rescore_started_at)
            kb_entries.sort(key=lambda item: (-item[0], item[1]))
            ranked_entries.extend(kb_entries[:limit])
            sequence += len(kb_entries)

        ranked_entries.sort(key=lambda item: (-item[0], item[1]))
        return [row for _, _, row in ranked_entries]

    def _rescore_candidates(
        self,
        *,
        query: np.ndarray,
        query_norm: float,
        candidate_metadata: list[dict[str, Any]],
        sequence_start: int,
    ) -> list[tuple[float, int, dict]]:
        chunk_ids = [str(row.get("chunk_id") or "") for row in candidate_metadata]
        chunk_ids = [chunk_id for chunk_id in chunk_ids if chunk_id]
        vector_rows_by_id = {
            str(row.get("chunk_id") or ""): row
            for row in self.store.vector_index_rows_by_chunk_ids(chunk_ids)
        }
        entries: list[tuple[float, int, dict]] = []
        for offset, metadata in enumerate(candidate_metadata):
            chunk_id = str(metadata.get("chunk_id") or "")
            raw_row = vector_rows_by_id.get(chunk_id)
            if raw_row is None:
                continue
            vector = _vector_from_index_row(raw_row)
            score = _cosine_numpy(
                query,
                vector,
                float(raw_row.get("embedding_norm") or metadata.get("embedding_norm") or 0.0),
                query_norm=query_norm,
            )
            entries.append(
                (
                    score,
                    sequence_start + offset,
                    {
                        "chunk_id": chunk_id,
                        "kb_id": str(metadata.get("kb_id") or raw_row.get("kb_id") or ""),
                        "doc_id": str(metadata.get("doc_id") or raw_row.get("doc_id") or ""),
                        "chunk_index": int(
                            metadata.get("chunk_index") or raw_row.get("chunk_index") or 0
                        ),
                        "embedding_norm": float(
                            raw_row.get("embedding_norm") or metadata.get("embedding_norm") or 0.0
                        ),
                        "created_at": float(
                            metadata.get("created_at") or raw_row.get("created_at") or 0.0
                        ),
                        "dense_score": score,
                    },
                )
            )
        return entries

    def _load_or_build_index(
        self,
        *,
        hnswlib: Any,
        kb_id: str,
        timings: dict[str, Any] | None,
    ) -> _LoadedHnswIndex | None:
        snapshot = self.store.vector_index_snapshot(kb_id)
        cached = self._index_cache.get(kb_id)
        if cached is not None and _metadata_matches_snapshot(cached.metadata, snapshot):
            _record_value(timings, "ann_index_hit", True)
            return cached

        loaded = self._load_index_from_disk(
            hnswlib=hnswlib,
            kb_id=kb_id,
            snapshot=snapshot,
            timings=timings,
        )
        if loaded is not None:
            self._index_cache[kb_id] = loaded
            _record_value(timings, "ann_index_hit", True)
            return loaded

        built = self._build_index(
            hnswlib=hnswlib,
            kb_id=kb_id,
            snapshot=snapshot,
            timings=timings,
        )
        if built is not None:
            self._index_cache[kb_id] = built
            _record_value(timings, "ann_index_hit", False)
        return built

    def _load_index_from_disk(
        self,
        *,
        hnswlib: Any,
        kb_id: str,
        snapshot: dict[str, Any],
        timings: dict[str, Any] | None,
    ) -> _LoadedHnswIndex | None:
        index_path, metadata_path = self._index_files(kb_id)
        if not index_path.exists() or not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not _metadata_matches_snapshot(metadata, snapshot):
            return None
        dimension = int(metadata.get("dimension") or 0)
        if dimension <= 0:
            return None
        load_started_at = time.perf_counter()
        index = hnswlib.Index(space=str(metadata.get("space") or "cosine"), dim=dimension)
        index.load_index(str(index_path), max_elements=max(1, int(metadata.get("count") or 0)))
        _add_timing(timings, "ann_index_load_ms", load_started_at)
        return _LoadedHnswIndex(index=index, metadata=metadata)

    def _build_index(
        self,
        *,
        hnswlib: Any,
        kb_id: str,
        snapshot: dict[str, Any],
        timings: dict[str, Any] | None,
    ) -> _LoadedHnswIndex | None:
        build_started_at = time.perf_counter()
        raw_rows = self.store.vector_index_rows(kb_id)
        vectors: list[np.ndarray] = []
        metadata_rows: list[dict[str, Any]] = []
        dimension = int(snapshot.get("embedding_dimensions") or 0)
        for raw_row in raw_rows:
            vector = _vector_from_index_row(raw_row)
            if vector.size == 0:
                continue
            if dimension <= 0:
                dimension = int(vector.shape[0])
            if int(vector.shape[0]) != dimension:
                continue
            label = len(vectors)
            vectors.append(vector)
            metadata_rows.append(
                {
                    "label": label,
                    "chunk_id": str(raw_row.get("chunk_id") or ""),
                    "kb_id": str(raw_row.get("kb_id") or kb_id),
                    "doc_id": str(raw_row.get("doc_id") or ""),
                    "chunk_index": int(raw_row.get("chunk_index") or 0),
                    "embedding_norm": float(raw_row.get("embedding_norm") or 0.0),
                    "created_at": float(raw_row.get("created_at") or 0.0),
                }
            )
        if not vectors or dimension <= 0:
            _add_timing(timings, "ann_index_build_ms", build_started_at)
            return None

        index_path, metadata_path = self._index_files(kb_id)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        matrix = np.vstack(vectors).astype(np.float32, copy=False)
        labels = np.arange(len(vectors), dtype=np.int64)
        index = hnswlib.Index(space="cosine", dim=dimension)
        index.init_index(
            max_elements=len(vectors),
            ef_construction=self.ef_construction,
            M=self.m,
        )
        index.add_items(matrix, labels)
        index.set_ef(self.ef_search)
        index.save_index(str(index_path))
        metadata = {
            "schema_version": _HNSW_SCHEMA_VERSION,
            "space": "cosine",
            "kb_id": kb_id,
            "dimension": dimension,
            "count": len(metadata_rows),
            "chunk_count": int(snapshot.get("chunk_count") or len(metadata_rows)),
            "updated_at": float(snapshot.get("updated_at") or 0.0),
            "rows": metadata_rows,
        }
        _write_json_atomic(metadata_path, metadata)
        _add_timing(timings, "ann_index_build_ms", build_started_at)
        return _LoadedHnswIndex(index=index, metadata=metadata)

    def _index_files(self, kb_id: str) -> tuple[Path, Path]:
        return hnsw_index_files(kb_id, self.index_dir)

    def _hnswlib(self) -> Any | None:
        if self._hnswlib_module is not None:
            return self._hnswlib_module
        if self._hnswlib_import_attempted:
            return None
        self._hnswlib_import_attempted = True
        try:
            import hnswlib  # type: ignore[import-not-found]
        except Exception as e:
            logger.warning("Knowledge HNSW vector backend unavailable: %s", e)
            return None
        self._hnswlib_module = hnswlib
        return hnswlib


class SQLiteBlobNumpyVectorBackend:
    """Use KnowledgeStore's float32 BLOB/NumPy search with JSON fallback."""

    def __init__(self, store: Any, fallback: VectorBackend | None = None) -> None:
        self.store = store
        self.fallback = fallback or SQLiteJsonVectorBackend(store)

    def search(
        self,
        *,
        kb_ids: list[str],
        query_vectors: dict[str, list[float]],
        options: dict[str, dict],
        timings: dict[str, Any] | None = None,
    ) -> list[dict]:
        dense_vector_search = getattr(self.store, "dense_vector_search", None)
        if not callable(dense_vector_search):
            return self.fallback.search(
                kb_ids=kb_ids,
                query_vectors=query_vectors,
                options=options,
                timings=timings,
            )
        limits = {
            kb_id: _positive_limit(options[kb_id].get("top_k_dense"), 30)
            for kb_id in kb_ids
            if kb_id in options
        }
        try:
            return dense_vector_search(
                kb_ids,
                query_vectors,
                limits,
                timings=timings,
            )
        except Exception as e:
            logger.warning("Knowledge dense vector backend failed; using JSON scan: %s", e)
            return self.fallback.search(
                kb_ids=kb_ids,
                query_vectors=query_vectors,
                options=options,
                timings=timings,
            )


class SQLiteJsonVectorBackend:
    """Exact dense scan over JSON-decoded vectors kept for compatibility."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def search(
        self,
        *,
        kb_ids: list[str],
        query_vectors: dict[str, list[float]],
        options: dict[str, dict],
        timings: dict[str, Any] | None = None,
    ) -> list[dict]:
        _record_value(timings, "vector_backend", "sqlite_json_scan")
        store_started_at = time.perf_counter()
        rows = self.store.vector_chunk_candidates(kb_ids)
        _record_timing(timings, "vector_store_ms", store_started_at)
        _record_count(timings, "vector_rows", len(rows))
        query_norms = {kb_id: _vector_norm(vector) for kb_id, vector in query_vectors.items()}
        heaps: dict[str, list[tuple[float, int, dict]]] = {}
        sequence = 0
        for raw_row in rows:
            kb_id = raw_row["kb_id"]
            vector = query_vectors.get(kb_id)
            if not vector:
                continue
            similarity = _cosine(
                vector,
                raw_row["embedding"],
                raw_row["embedding_norm"],
                query_norm=query_norms.get(kb_id, 0.0),
            )
            row = dict(raw_row)
            row.pop("embedding", None)
            row["dense_score"] = similarity
            limit = _positive_limit(options[kb_id].get("top_k_dense"), 30)
            heap = heaps.setdefault(kb_id, [])
            entry = (similarity, -sequence, row)
            if len(heap) < limit:
                heappush(heap, entry)
            elif entry > heap[0]:
                heapreplace(heap, entry)
            sequence += 1
        limited_entries = [entry for heap in heaps.values() for entry in heap]
        limited_entries.sort(key=lambda item: (-item[0], -item[1]))
        return [row for _, _, row in limited_entries]


def build_default_vector_backend(store: Any, config: dict[str, Any] | None = None) -> VectorBackend:
    fallback = (
        SQLiteBlobNumpyVectorBackend(store, fallback=SQLiteJsonVectorBackend(store))
        if callable(getattr(store, "dense_vector_search", None))
        else SQLiteJsonVectorBackend(store)
    )
    ann_cfg = _ann_config(config)
    if _ann_enabled(config, ann_cfg):
        return HnswVectorBackend(
            store,
            index_dir=ann_cfg.get("index_dir") or DEFAULT_HNSW_INDEX_DIR,
            fallback=fallback,
            candidate_k=_positive_limit(ann_cfg.get("candidate_k"), 300),
            ef_search=_positive_limit(ann_cfg.get("ef_search"), 128),
            m=_positive_limit(ann_cfg.get("m"), 32),
            ef_construction=_positive_limit(ann_cfg.get("ef_construction"), 200),
        )
    return fallback


def build_exact_vector_backend(store: Any) -> VectorBackend:
    if callable(getattr(store, "dense_vector_search", None)):
        return SQLiteBlobNumpyVectorBackend(store, fallback=SQLiteJsonVectorBackend(store))
    return SQLiteJsonVectorBackend(store)


def _cosine(
    query_vector: list[float],
    doc_vector: list[float],
    doc_norm: float,
    *,
    query_norm: float | None = None,
) -> float:
    if not query_vector or not doc_vector or doc_norm <= 0:
        return 0.0
    dot = sum(left * right for left, right in zip(query_vector, doc_vector, strict=False))
    if query_norm is None:
        query_norm = _vector_norm(query_vector)
    if query_norm <= 0:
        return 0.0
    return float(dot / (query_norm * doc_norm))


def _vector_norm(vector: list[float]) -> float:
    return math.sqrt(sum(item * item for item in vector))


def _vector_from_index_row(row: dict[str, Any]) -> np.ndarray:
    blob = row.get("embedding_blob")
    dtype = str(row.get("embedding_dtype") or "")
    try:
        revision = int(row.get("embedding_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    if blob is not None and dtype == _EMBEDDING_BLOB_DTYPE and revision == _EMBEDDING_BLOB_REVISION:
        return np.frombuffer(blob, dtype=np.float32)
    return np.asarray(json.loads(str(row.get("embedding") or "[]")), dtype=np.float32)


def _cosine_numpy(
    query_vector: np.ndarray,
    doc_vector: np.ndarray,
    doc_norm: float,
    *,
    query_norm: float,
) -> float:
    if query_vector.size == 0 or doc_vector.size == 0 or doc_norm <= 0 or query_norm <= 0:
        return 0.0
    if int(query_vector.shape[0]) != int(doc_vector.shape[0]):
        return 0.0
    return float(np.dot(query_vector, doc_vector) / (query_norm * doc_norm))


def _metadata_matches_snapshot(metadata: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    if int(metadata.get("schema_version") or 0) != _HNSW_SCHEMA_VERSION:
        return False
    if str(metadata.get("kb_id") or "") != str(snapshot.get("kb_id") or ""):
        return False
    if int(metadata.get("chunk_count") or 0) != int(snapshot.get("chunk_count") or 0):
        return False
    if int(metadata.get("dimension") or 0) != int(snapshot.get("embedding_dimensions") or 0):
        return False
    return float(metadata.get("updated_at") or 0.0) == float(snapshot.get("updated_at") or 0.0)


def _flatten_hnsw_labels(labels: Any) -> list[int]:
    values = labels.tolist() if hasattr(labels, "tolist") else labels
    if not values:
        return []
    first = values[0]
    if isinstance(first, list):
        return [int(value) for value in first]
    return [int(value) for value in values]


def hnsw_index_files(
    kb_id: str,
    index_dir: str | Path = DEFAULT_HNSW_INDEX_DIR,
) -> tuple[Path, Path]:
    stem = _safe_index_stem(kb_id)
    root = Path(index_dir)
    return root / f"{stem}.hnsw", root / f"{stem}.json"


def delete_hnsw_sidecar_files(
    kb_id: str,
    index_dir: str | Path = DEFAULT_HNSW_INDEX_DIR,
) -> None:
    for path in hnsw_index_files(kb_id, index_dir):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning("Knowledge HNSW sidecar cleanup skipped for %s: %s", path, e)


def _safe_index_stem(value: str) -> str:
    stem = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value))
    return stem or "default"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _ann_config(config: dict[str, Any] | None) -> dict[str, Any]:
    knowledge = config.get("knowledge", {}) if isinstance(config, dict) else {}
    if not isinstance(knowledge, dict):
        return {}
    ann = knowledge.get("ann", {})
    return dict(ann if isinstance(ann, dict) else {})


def _ann_enabled(config: dict[str, Any] | None, ann_cfg: dict[str, Any]) -> bool:
    knowledge = config.get("knowledge", {}) if isinstance(config, dict) else {}
    if not isinstance(knowledge, dict):
        return False
    backend = str(knowledge.get("vector_backend") or "").strip().lower()
    return backend == _VECTOR_BACKEND_HNSW or bool(ann_cfg.get("enabled"))


def _positive_limit(value: object, default: int) -> int:
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _record_timing(
    timings: dict[str, Any] | None,
    key: str,
    started_at: float,
) -> None:
    if timings is None:
        return
    timings[key] = (time.perf_counter() - started_at) * 1000


def _record_count(timings: dict[str, Any] | None, key: str, value: int) -> None:
    if timings is None:
        return
    timings[key] = int(value)


def _add_count(timings: dict[str, Any] | None, key: str, value: int) -> None:
    if timings is None:
        return
    timings[key] = int(timings.get(key) or 0) + int(value)


def _record_value(timings: dict[str, Any] | None, key: str, value: Any) -> None:
    if timings is None:
        return
    timings[key] = value


def _add_timing(
    timings: dict[str, Any] | None,
    key: str,
    started_at: float,
) -> None:
    if timings is None:
        return
    timings[key] = float(timings.get(key) or 0.0) + (time.perf_counter() - started_at) * 1000
