"""Hybrid retrieval for the SQLite knowledge store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from core import logger
from core.knowledge.embedding import ModelSelection
from core.knowledge.rerank import RerankClient
from core.knowledge.store import KnowledgeStore


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

    def __init__(self, store: KnowledgeStore, rerank_client: RerankClient | None = None) -> None:
        self.store = store
        self.rerank_client = rerank_client

    async def retrieve(
        self,
        *,
        query: str,
        kb_records: list[dict],
        query_vectors: dict[str, list[float]],
        top_k: int,
    ) -> list[RetrievalHit]:
        kb_ids = [kb["kb_id"] for kb in kb_records]
        if not query.strip() or not kb_ids:
            return []

        options = {kb["kb_id"]: kb for kb in kb_records}
        dense_ranked = self._dense_rank(kb_ids, query_vectors, options)
        sparse_ranked = self.store.keyword_search(
            query,
            kb_ids,
            max(_positive_limit(kb.get("top_k_sparse"), 30) for kb in kb_records),
        )
        fused = self._fuse(dense_ranked, sparse_ranked)
        hits = [self._hit_from_row(row, score) for row, score in fused]
        hits = await self._maybe_rerank(query, kb_records, hits)
        return self._apply_final_limits(hits, kb_records, top_k)

    def _dense_rank(
        self,
        kb_ids: list[str],
        query_vectors: dict[str, list[float]],
        options: dict[str, dict],
    ) -> list[dict]:
        rows = self.store.vector_chunks(kb_ids)
        scored = []
        for row in rows:
            vector = query_vectors.get(row["kb_id"])
            if not vector:
                continue
            similarity = _cosine(vector, row["embedding"], row["embedding_norm"])
            row = dict(row)
            row["dense_score"] = similarity
            scored.append(row)
        scored.sort(key=lambda item: item["dense_score"], reverse=True)

        limited = []
        counts: dict[str, int] = {}
        for row in scored:
            kb_id = row["kb_id"]
            limit = _positive_limit(options[kb_id].get("top_k_dense"), 30)
            if counts.get(kb_id, 0) >= limit:
                continue
            counts[kb_id] = counts.get(kb_id, 0) + 1
            limited.append(row)
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


def _cosine(query_vector: list[float], doc_vector: list[float], doc_norm: float) -> float:
    if not query_vector or not doc_vector or doc_norm <= 0:
        return 0.0
    dot = sum(left * right for left, right in zip(query_vector, doc_vector, strict=False))
    query_norm = sum(item * item for item in query_vector) ** 0.5
    if query_norm <= 0:
        return 0.0
    return float(dot / (query_norm * doc_norm))


def _positive_limit(value: object, default: int) -> int:
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)
