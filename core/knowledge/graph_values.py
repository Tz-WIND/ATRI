"""Value coercion and telemetry helpers for Neo4j graph retrieval."""

from __future__ import annotations

import math
import time
from typing import Any

from core.knowledge.graph_constants import (
    CHAIN_ORDER_KEY_SEPARATOR,
    GRAPH_EXPANSION_CANDIDATE_MAX_LIMIT,
    GRAPH_RETRIEVAL_MAX_DEPTH,
    SOURCE_SCOPE_BATCH_FALLBACK,
)

_DEFAULT_MULTIHOP_EXPANSION_LIMIT = 40

_VALID_RANKING_POLICIES = {"hybrid", "relevance", "latest"}

MULTI_HOP_EXPANSION_CACHE_PATH_LIMIT_DEFAULT = GRAPH_EXPANSION_CANDIDATE_MAX_LIMIT

MULTI_HOP_EXPANSION_CACHE_PATH_LIMIT_MAX = 10_000

MULTI_HOP_EXPANSION_CACHE_PRELOAD_SEED_LIMIT_DEFAULT = 64

MULTI_HOP_EXPANSION_CACHE_PRELOAD_SEED_LIMIT_MAX = 2048

MULTI_HOP_EXPANSION_CACHE_PRELOAD_PATH_LIMIT_DEFAULT = 200

MULTI_HOP_EXPANSION_CACHE_PRELOAD_PATH_LIMIT_MAX = 50_000

_DEFAULT_MULTIHOP_EXPANSION_CACHE_PRELOAD_SEED_LIMIT = (
    MULTI_HOP_EXPANSION_CACHE_PRELOAD_SEED_LIMIT_DEFAULT
)


def _source_score_rows(
    source_ids: list[str],
    source_scores: dict[str, float],
) -> list[dict[str, float | str]]:
    if not source_ids or not isinstance(source_scores, dict):
        return []
    rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    max_score = 0.0
    for source_id in source_ids:
        source_id = str(source_id or "").strip()
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        raw_score = source_scores.get(source_id)
        if raw_score is None:
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score) or score <= 0:
            continue
        rows.append((source_id, score))
        max_score = max(max_score, score)
    if max_score <= 0:
        return []
    return [
        {
            "source_id": source_id,
            "score": score / max_score,
        }
        for source_id, score in rows
    ]


def _retrieval_depth(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(GRAPH_RETRIEVAL_MAX_DEPTH, parsed))


def _ranking_policy(value: Any) -> str:
    policy = str(value or "hybrid").strip().lower()
    return policy if policy in _VALID_RANKING_POLICIES else "hybrid"


def _expansion_candidate_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _DEFAULT_MULTIHOP_EXPANSION_LIMIT
    return max(1, min(GRAPH_EXPANSION_CANDIDATE_MAX_LIMIT, parsed))


def _multi_hop_expansion_cache_path_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = MULTI_HOP_EXPANSION_CACHE_PATH_LIMIT_DEFAULT
    return max(1, min(MULTI_HOP_EXPANSION_CACHE_PATH_LIMIT_MAX, parsed))


def _multi_hop_expansion_cache_preload_seed_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _DEFAULT_MULTIHOP_EXPANSION_CACHE_PRELOAD_SEED_LIMIT
    return max(0, min(MULTI_HOP_EXPANSION_CACHE_PRELOAD_SEED_LIMIT_MAX, parsed))


def _multi_hop_expansion_cache_preload_path_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = MULTI_HOP_EXPANSION_CACHE_PRELOAD_PATH_LIMIT_DEFAULT
    return max(1, min(MULTI_HOP_EXPANSION_CACHE_PRELOAD_PATH_LIMIT_MAX, parsed))


def _fact_source_ids(fact: dict[str, Any]) -> list[str]:
    raw = fact.get("source_ids")
    values = raw if isinstance(raw, list) else []
    source_scope = str(fact.get("source_scope") or "").strip().lower()
    if source_scope != SOURCE_SCOPE_BATCH_FALLBACK:
        values = [*values, fact.get("source_id")]
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _fact_chain_ids(fact: dict[str, Any]) -> list[str]:
    raw = fact.get("chain_ids")
    values = raw if isinstance(raw, list) else []
    values = [*values, fact.get("chain_id")]
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _fact_chain_order_keys(fact: dict[str, Any]) -> list[str]:
    raw = fact.get("chain_order_keys")
    values = raw if isinstance(raw, list) else []
    chain_id = str(fact.get("chain_id") or "").strip()
    chain_order = _optional_int(fact.get("chain_order"))
    if chain_id and chain_order is not None:
        values = [*values, f"{chain_id}{CHAIN_ORDER_KEY_SEPARATOR}{chain_order}"]
    result = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    return bool(value)


def _entity_type_key(value: Any) -> str:
    return " ".join(str(value or "entity").strip().lower().split()) or "entity"


def _connection_signature(config: dict[str, Any]) -> tuple[bool, str, str, str, str]:
    return (
        bool(config.get("enabled", False)),
        str(config.get("uri") or "neo4j://localhost:7687"),
        str(config.get("username") or "neo4j"),
        str(config.get("password") or ""),
        str(config.get("database") or "neo4j"),
    )


def _result_count(result: list[Any], fallback: int) -> int:
    if not result:
        return fallback
    try:
        return int(result[0].get("count", fallback))
    except (TypeError, ValueError, AttributeError):
        return fallback


def _record_timing(
    timings: dict[str, Any] | None,
    key: str,
    started_at: float,
) -> None:
    if timings is None:
        return
    timings[key] = (time.perf_counter() - started_at) * 1000


def _set_timing(timings: dict[str, Any] | None, key: str, elapsed_ms: float) -> None:
    if timings is None:
        return
    timings[key] = float(elapsed_ms)


def _record_count(timings: dict[str, Any] | None, key: str, value: int) -> None:
    if timings is None:
        return
    timings[key] = int(value)


def _set_bool(timings: dict[str, Any] | None, key: str, value: bool) -> None:
    if timings is None:
        return
    timings[key] = bool(value)
