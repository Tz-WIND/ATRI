"""Hybrid retrieval for the SQLite knowledge store."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

from core import logger
from core.knowledge.embedding import ModelSelection
from core.knowledge.rerank import RerankClient
from core.knowledge.store import KnowledgeStore
from core.knowledge.vector_backend import VectorBackend, build_default_vector_backend


@dataclass
class RetrievalHit:
    chunk_id: str
    kb_id: str
    kb_name: str
    doc_id: str
    doc_name: str
    chunk_index: int
    content: str
    score: float
    char_count: int

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "kb_id": self.kb_id,
            "kb_name": self.kb_name,
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": self.score,
            "char_count": self.char_count,
        }


class HybridRetriever:
    """Combine dense cosine retrieval, SQLite text retrieval, and optional rerank."""

    def __init__(
        self,
        store: KnowledgeStore,
        rerank_client: RerankClient | None = None,
        vector_backend: VectorBackend | None = None,
        vector_config: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.rerank_client = rerank_client
        self.vector_backend = vector_backend or build_default_vector_backend(store, vector_config)

    async def retrieve(
        self,
        *,
        query: str,
        kb_records: list[dict],
        query_vectors: dict[str, list[float]],
        top_k: int,
        timings: dict[str, Any] | None = None,
    ) -> list[RetrievalHit]:
        started_at = time.perf_counter()
        kb_ids = [kb["kb_id"] for kb in kb_records]
        if not query.strip() or not kb_ids:
            _record_timing(timings, "vector_retriever_ms", started_at)
            return []

        options = {kb["kb_id"]: kb for kb in kb_records}
        dense_started_at = time.perf_counter()
        dense_ranked = self._dense_rank(kb_ids, query_vectors, options, timings=timings)
        _record_timing(timings, "vector_dense_ms", dense_started_at)
        sparse_started_at = time.perf_counter()
        sparse_ranked = self.store.keyword_search(
            query,
            kb_ids,
            max(_positive_limit(kb.get("top_k_sparse"), 30) for kb in kb_records),
        )
        _record_timing(timings, "vector_sparse_ms", sparse_started_at)
        _record_count(timings, "vector_sparse_rows", len(sparse_ranked))
        fuse_started_at = time.perf_counter()
        fused = self._fuse(dense_ranked, sparse_ranked)
        _record_timing(timings, "vector_fuse_ms", fuse_started_at)
        _record_count(timings, "vector_fused_rows", len(fused))
        hits = [self._hit_from_row(row, score) for row, score in fused]
        rerank_started_at = time.perf_counter()
        hits = await self._maybe_rerank(query, kb_records, hits)
        _record_timing(timings, "vector_rerank_ms", rerank_started_at)
        _record_count(timings, "vector_reranked_hits", len(hits))
        limit_started_at = time.perf_counter()
        limited = self._apply_final_limits(hits, kb_records, top_k)
        _record_timing(timings, "vector_limit_ms", limit_started_at)
        _record_count(timings, "vector_returned_hits", len(limited))
        _record_timing(timings, "vector_retriever_ms", started_at)
        return limited

    def _dense_rank(
        self,
        kb_ids: list[str],
        query_vectors: dict[str, list[float]],
        options: dict[str, dict],
        *,
        timings: dict[str, Any] | None = None,
    ) -> list[dict]:
        limited_candidates = self.vector_backend.search(
            kb_ids=kb_ids,
            query_vectors=query_vectors,
            options=options,
            timings=timings,
        )
        return self._hydrate_dense_candidates(limited_candidates, timings=timings)

    def _hydrate_dense_candidates(
        self,
        limited_candidates: list[dict],
        *,
        timings: dict[str, Any] | None = None,
    ) -> list[dict]:
        _record_count(timings, "vector_dense_rows", len(limited_candidates))
        hydrate_started_at = time.perf_counter()
        hydrated_rows = self.store.chunks_by_ids([row["chunk_id"] for row in limited_candidates])
        _record_timing(timings, "vector_hydrate_ms", hydrate_started_at)
        hydrated_by_id = {row["chunk_id"]: row for row in hydrated_rows}
        limited = [
            {**row, **hydrated_by_id[row["chunk_id"]]}
            for row in limited_candidates
            if row["chunk_id"] in hydrated_by_id
        ]
        _record_count(timings, "vector_hydrated_rows", len(limited))
        return limited

    def _fuse(self, dense_rows: list[dict], sparse_rows: list[dict]) -> list[tuple[dict, float]]:
        by_id: dict[str, tuple[dict, float]] = {}
        for rows in (dense_rows, sparse_rows):
            for rank, row in enumerate(rows, start=1):
                current_row, current_score = by_id.get(row["chunk_id"], (row, 0.0))
                by_id[row["chunk_id"]] = (current_row, current_score + 1.0 / (60 + rank))
        return sorted(by_id.values(), key=lambda item: item[1], reverse=True)

    async def _maybe_rerank(
        self,
        query: str,
        kb_records: list[dict],
        hits: list[RetrievalHit],
    ) -> list[RetrievalHit]:
        if not self.rerank_client or not hits:
            return hits
        rerank_kb = next((kb for kb in kb_records if kb.get("rerank_model")), None)
        if not rerank_kb:
            return hits
        selection = ModelSelection(
            provider=rerank_kb.get("rerank_provider", ""),
            model=rerank_kb.get("rerank_model", ""),
            config=dict(rerank_kb.get("rerank_config") or {}),
            provider_config=dict(rerank_kb.get("rerank_provider_config") or {}),
        )
        try:
            reranked = await self.rerank_client.rerank(
                selection,
                query,
                [hit.content for hit in hits],
            )
        except Exception as e:
            logger.warning(
                "Knowledge rerank failed for %s/%s: %s",
                selection.provider,
                selection.model,
                e,
            )
            return hits
        by_index = []
        for item in reranked:
            try:
                index = int(item["index"])
                score = float(item["score"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= index < len(hits):
                hit = hits[index]
                hit.score = score
                by_index.append(hit)
        if not by_index:
            return hits
        by_index.sort(key=lambda item: item.score, reverse=True)
        return by_index

    def _apply_final_limits(
        self,
        hits: list[RetrievalHit],
        kb_records: list[dict],
        top_k: int,
    ) -> list[RetrievalHit]:
        options = {kb["kb_id"]: kb for kb in kb_records}
        global_limit = _positive_limit(top_k, 1)
        counts: dict[str, int] = {}
        limited = []
        for hit in hits:
            kb = options.get(hit.kb_id, {})
            kb_limit = _positive_limit(kb.get("top_m_final"), global_limit)
            if counts.get(hit.kb_id, 0) >= kb_limit:
                continue
            counts[hit.kb_id] = counts.get(hit.kb_id, 0) + 1
            limited.append(hit)
            if len(limited) >= global_limit:
                break
        return limited

    def _hit_from_row(self, row: dict, score: float) -> RetrievalHit:
        return RetrievalHit(
            chunk_id=row["chunk_id"],
            kb_id=row["kb_id"],
            kb_name=row["kb_name"],
            doc_id=row["doc_id"],
            doc_name=row["doc_name"],
            chunk_index=int(row["chunk_index"]),
            content=row["content"],
            score=score,
            char_count=int(row["char_count"]),
        )


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
