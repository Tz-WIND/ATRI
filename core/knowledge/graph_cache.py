"""Caching helpers for Neo4j graph retrieval."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

from core.knowledge.graph_format import _row_float, _row_int, _row_value

_GRAPH_CACHE_TTL_SECONDS = 600.0

_MULTI_HOP_EXPANSION_CACHE_VERSION = "multi_hop_expansion:v1"

_MULTI_HOP_EXPANSION_CACHE_TRAVERSAL_MODE = "variable_length"

_MULTI_HOP_EXPANSION_CACHE_DIRECTION = "undirected"

_MULTI_HOP_EXPANSION_CACHE_RELATION_TYPES = ("FACT",)

_GRAPH_CACHE_LIMITS = {
    "final_context": 256,
    "fulltext_seed": 512,
    "multi_hop_expansion": 512,
}


class GraphRetrievalCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = _GRAPH_CACHE_TTL_SECONDS,
        limits: dict[str, int] | None = None,
    ) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.limits = dict(limits or _GRAPH_CACHE_LIMITS)
        self._stores: dict[str, OrderedDict[Any, tuple[float, Any]]] = {}
        self._lock = threading.Lock()

    def get(self, namespace: str, key: Any) -> Any | None:
        if self.ttl_seconds <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            store = self._stores.get(namespace)
            if not store or key not in store:
                return None
            stored_at, value = store[key]
            if now - stored_at > self.ttl_seconds:
                del store[key]
                return None
            store.move_to_end(key)
            return value

    def set(self, namespace: str, key: Any, value: Any) -> None:
        if self.ttl_seconds <= 0:
            return
        limit = max(1, int(self.limits.get(namespace, 1)))
        with self._lock:
            store = self._stores.setdefault(namespace, OrderedDict())
            store[key] = (time.monotonic(), value)
            store.move_to_end(key)
            while len(store) > limit:
                store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._stores.clear()


def _final_context_cache_key(
    *,
    revision: int,
    query: str,
    source_ids: list[str],
    source_score_rows: list[dict[str, float | str]],
    max_facts: int,
    retrieval_depth: int,
    ranking_policy: str,
    expansion_candidate_limit: int,
    multi_hop_expansion_cache_mode: str,
    include_entity_types: bool,
    fulltext_ready: bool,
) -> tuple[Any, ...]:
    return (
        "v1",
        int(revision),
        _cache_query_text(query),
        tuple(_source_ids_cache_key(source_ids)),
        tuple(_source_score_rows_cache_key(source_score_rows)),
        int(max_facts),
        int(retrieval_depth),
        str(ranking_policy),
        int(expansion_candidate_limit),
        str(multi_hop_expansion_cache_mode),
        bool(include_entity_types),
        bool(fulltext_ready),
    )


def _multi_hop_expansion_cache_key(
    *,
    revision: int,
    seed_element_id: str,
    depth: int,
    path_limit: int,
) -> tuple[Any, ...]:
    return (
        _MULTI_HOP_EXPANSION_CACHE_VERSION,
        int(revision),
        str(seed_element_id),
        int(depth),
        _MULTI_HOP_EXPANSION_CACHE_TRAVERSAL_MODE,
        _MULTI_HOP_EXPANSION_CACHE_RELATION_TYPES,
        _MULTI_HOP_EXPANSION_CACHE_DIRECTION,
        int(path_limit),
    )


def _cache_query_text(query: str) -> str:
    return " ".join(str(query or "").strip().lower().split())


def _unique_text_values(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _source_ids_cache_key(source_ids: list[str]) -> list[str]:
    result: list[str] = []
    for source_id in source_ids:
        text = str(source_id or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _source_score_rows_cache_key(
    source_score_rows: list[dict[str, float | str]],
) -> list[tuple[str, float]]:
    return [
        (str(row.get("source_id") or ""), round(float(row.get("score") or 0.0), 12))
        for row in source_score_rows
    ]


def _cached_seed_rows(rows: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        element_id = str(_row_value(row, "element_id") or "").strip()
        if not element_id:
            continue
        result.append(
            {
                "element_id": element_id,
                "score": _row_float(row, "score", 0.0),
            }
        )
    return result


def _cached_multi_hop_seed_rows(rows: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        element_id = str(_row_value(row, "element_id") or "").strip()
        if not element_id or element_id in seen:
            continue
        seen.add(element_id)
        result.append(
            {
                "element_id": element_id,
                "seed_score": _row_float(row, "seed_score", 0.0),
            }
        )
    return result


def _normalized_multi_hop_expansion_paths(paths: Any) -> list[dict[str, Any]]:
    if not isinstance(paths, list):
        return []
    result: list[dict[str, Any]] = []
    for path in paths:
        normalized = _normalized_multi_hop_expansion_path(path)
        if normalized is not None:
            result.append(normalized)
    return result


def _normalized_multi_hop_expansion_path(path: Any) -> dict[str, Any] | None:
    hop = _row_int(path, "hop", 0)
    rel_ids = _row_value(path, "rel_ids", []) or []
    if hop <= 1 or not isinstance(rel_ids, list):
        return None
    rel_refs: list[dict[str, Any]] = []
    for fallback_index, rel_ref in enumerate(rel_ids):
        element_id = str(_row_value(rel_ref, "element_id") or "").strip()
        fact_key = str(_row_value(rel_ref, "fact_key") or "").strip()
        if not element_id and not fact_key:
            continue
        normalized_ref: dict[str, Any] = {
            "rel_index": _row_int(rel_ref, "rel_index", fallback_index),
        }
        if element_id:
            normalized_ref["element_id"] = element_id
        if fact_key:
            normalized_ref["fact_key"] = fact_key
        rel_refs.append(normalized_ref)
    rel_refs.sort(key=lambda ref: int(ref["rel_index"]))
    if len(rel_refs) != hop:
        return None
    return {"hop": hop, "rel_ids": rel_refs}


def _multi_hop_expansion_cache_path_row(
    seed_element_id: str,
    path_index: int,
    path: Any,
) -> dict[str, Any] | None:
    normalized = _normalized_multi_hop_expansion_path(path)
    if normalized is None:
        return None
    return {
        "seed_element_id": seed_element_id,
        "path_key": f"{seed_element_id}:{path_index}",
        "hop": normalized["hop"],
        "rel_ids": normalized["rel_ids"],
    }


def _graph_context_fact_count(context: Any) -> int:
    return sum(1 for line in str(context or "").splitlines() if line.lstrip().startswith("- "))
