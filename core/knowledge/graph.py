"""Neo4j persistence and retrieval for graph knowledge facts."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import sys
import time
import warnings
from collections.abc import Callable
from hashlib import sha256
from typing import Any, TypeVar, cast

from core import logger
from core.knowledge.graph_cache import (
    GraphRetrievalCache,
    _cached_multi_hop_seed_rows,
    _cached_seed_rows,
    _final_context_cache_key,
    _graph_context_fact_count,
    _multi_hop_expansion_cache_key,
    _multi_hop_expansion_cache_path_row,
    _normalized_multi_hop_expansion_paths,
    _unique_text_values,
)
from core.knowledge.graph_constants import (
    CHAIN_ORDER_KEY_SEPARATOR,
    GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
    GRAPH_QUERY_ENUMERATION_TERMS,
    GRAPH_RETRIEVAL_DEFAULT_DEPTH,
    HYPER_ROLE_PREDICATE,
    SOURCE_SCOPE_BATCH_FALLBACK,
    SOURCE_SCOPE_EXACT,
    SOURCE_SCOPE_INFERRED,
    SOURCE_SCOPE_LEGACY,
    format_graph_context,
)
from core.knowledge.graph_format import (
    _canonical_retrieved_entity_name,
    _context_entity_label,
    _entity_alias_key,
    _format_retrieved_fact_lines,
    _rank_retrieved_rows,
    _row_int,
    _row_value,
)
from core.knowledge.graph_query import _fulltext_query, _query_term_rows, _query_terms
from core.knowledge.graph_values import (
    _connection_signature,
    _entity_type_key,
    _expansion_candidate_limit,
    _fact_chain_ids,
    _fact_chain_order_keys,
    _fact_source_ids,
    _multi_hop_expansion_cache_path_limit,
    _multi_hop_expansion_cache_preload_path_limit,
    _multi_hop_expansion_cache_preload_seed_limit,
    _optional_bool,
    _optional_int,
    _optional_text,
    _ranking_policy,
    _record_count,
    _record_timing,
    _result_count,
    _retrieval_depth,
    _set_bool,
    _set_timing,
    _source_score_rows,
)

DriverFactory = Callable[[str, tuple[str, str]], Any]
_ENTITY_FULLTEXT_INDEX = "entity_text"
_FACT_FULLTEXT_INDEX = "fact_text"
_GRAPH_REVISION_METADATA_KEY = "graph_revision"
_GRAPH_SOURCE_PROJECTION_BACKFILL_METADATA_KEY = "graph_source_projection_backfill_v2"
_GRAPH_EVIDENCE_LEDGER_BACKFILL_METADATA_KEY = "graph_evidence_ledger_backfill_v2"
_PERSISTENT_MULTI_HOP_EXPANSION_CACHE_VERSION = "persistent_multi_hop_expansion:v2"
_MULTI_HOP_EXPANSION_CACHE_MODES = {"off", "memory", "persistent"}
_ACTIVE_FACT_STATUS = "active"
_FACT_STATUS_PROPERTY = "status"
_CURRENT_SINGLE_CONFLICT_POLICY = "current_single"
_APPEND_ONLY_CONFLICT_POLICY = "append_only"
_FULLTEXT_FACT_SEED_CANDIDATE_MULTIPLIER = 4
# Conservative whitelist: topology alone never creates a conflict; only these
# predicates are treated as one-current-value slots for the same subject.
_CURRENT_SINGLE_PREDICATES = frozenset(
    {
        "has_status",
        "has_version",
        "works_at",
    }
)
T = TypeVar("T")
__all__ = ["GRAPH_QUERY_ENUMERATION_TERMS", "Neo4jGraphClient", "_query_terms"]


def _legacy_persistent_cache_enabled(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
        return bool(normalized)
    return bool(value)


def _seed_rows_for_element_ids(
    seed_rows: list[dict[str, Any]],
    seed_element_ids: list[str],
) -> list[dict[str, Any]]:
    wanted = set(_unique_text_values(seed_element_ids))
    if not wanted:
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in seed_rows:
        element_id = str(row.get("element_id") or "").strip()
        if not element_id or element_id in seen or element_id not in wanted:
            continue
        seen.add(element_id)
        rows.append(row)
    return rows


def _fact_conflict_policy(fact: dict[str, Any]) -> str:
    predicate = str(fact.get("predicate") or "").strip().lower()
    if predicate in _CURRENT_SINGLE_PREDICATES:
        return _CURRENT_SINGLE_CONFLICT_POLICY
    return _APPEND_ONLY_CONFLICT_POLICY


def _fact_status(fact: dict[str, Any]) -> str:
    return (_optional_text(fact.get("status")) or _ACTIVE_FACT_STATUS).lower()


def _fact_slot_key(fact: dict[str, Any], conflict_policy: str) -> str | None:
    if conflict_policy != _CURRENT_SINGLE_CONFLICT_POLICY:
        return None
    subject_type_key = _optional_text(fact.get("subject_type_key"))
    subject_key = _optional_text(fact.get("subject_key"))
    predicate = _optional_text(fact.get("predicate"))
    if not subject_type_key or not subject_key or not predicate:
        return None
    return f"{subject_type_key}:{subject_key}|{predicate}"


def _fold_current_single_fact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slot_indexes: dict[str, int] = {}
    folded: list[dict[str, Any]] = []
    for row in rows:
        slot_key = _optional_text(row.get("slot_key"))
        if (
            row.get("conflict_policy") != _CURRENT_SINGLE_CONFLICT_POLICY
            or row.get("status") != _ACTIVE_FACT_STATUS
            or not slot_key
        ):
            folded.append(row)
            continue
        existing_index = slot_indexes.get(slot_key)
        if existing_index is None:
            slot_indexes[slot_key] = len(folded)
            folded.append(row)
            continue
        folded[existing_index] = row
    return folded


def _fulltext_fact_seed_candidate_limit(seed_limit: int) -> int:
    normalized_limit = max(1, int(seed_limit or 1))
    return normalized_limit * _FULLTEXT_FACT_SEED_CANDIDATE_MULTIPLIER


def _fact_batch_source_ids(fact: dict[str, Any]) -> list[str]:
    raw = fact.get("batch_source_ids")
    values = raw if isinstance(raw, list) else []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _fact_source_refs(fact: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    raw_refs = fact.get("source_refs")
    if isinstance(raw_refs, list):
        values.extend(raw_refs)
    source_ref = _optional_text(fact.get("source_ref"))
    if source_ref:
        values.append(source_ref)
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _fact_evidence_items(fact: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = fact.get("evidence_items")
    items = raw_items if isinstance(raw_items, list) else []
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        evidence_text = _optional_text(item.get("text"))
        if not evidence_text:
            continue
        normalized_text = _normalized_evidence_text(item.get("normalized_text") or evidence_text)
        source_scope = _source_scope(item.get("source_scope") or fact.get("source_scope"))
        batch_source_ids = _fact_batch_source_ids(
            {"batch_source_ids": item.get("batch_source_ids") or fact.get("batch_source_ids")}
        )
        source_id = _optional_text(item.get("source_id")) or ""
        evidence_key_source = (
            source_id
            if source_id
            else "batch:" + sha256("|".join(batch_source_ids).encode("utf-8")).hexdigest()[:16]
        )
        evidence_key = _optional_text(item.get("evidence_key")) or _evidence_key(
            fact_key=str(fact.get("fact_key") or ""),
            source_key=evidence_key_source,
            normalized_text=normalized_text,
        )
        normalized_items.append(
            {
                "evidence_key": evidence_key,
                "fact_key": str(fact.get("fact_key") or ""),
                "source_id": source_id,
                "source_kind": (
                    _optional_text(item.get("source_kind") or fact.get("source_kind")) or ""
                ),
                "source_ref": _optional_text(item.get("source_ref")) or "",
                "source_scope": source_scope,
                "text": evidence_text,
                "normalized_text": normalized_text,
                "confidence": _evidence_confidence(item.get("confidence")),
                "exact_source": source_scope in {SOURCE_SCOPE_EXACT, SOURCE_SCOPE_INFERRED},
                "batch_source_ids": batch_source_ids,
            }
        )
    if normalized_items:
        return _dedupe_evidence_items(normalized_items)
    evidence = _optional_text(fact.get("evidence"))
    if not evidence:
        return []
    source_scope = _source_scope(fact.get("source_scope"))
    source_ids = _fact_source_ids(fact)
    source_id_values = source_ids if source_ids else [""]
    batch_source_ids = _fact_batch_source_ids(fact)
    normalized_text = _normalized_evidence_text(evidence)
    fallback_items = []
    for source_id in source_id_values:
        evidence_key_source = (
            source_id
            if source_id
            else "batch:" + sha256("|".join(batch_source_ids).encode("utf-8")).hexdigest()[:16]
        )
        fallback_items.append(
            {
                "evidence_key": _evidence_key(
                    fact_key=str(fact.get("fact_key") or ""),
                    source_key=evidence_key_source,
                    normalized_text=normalized_text,
                ),
                "fact_key": str(fact.get("fact_key") or ""),
                "source_id": source_id,
                "source_kind": _optional_text(fact.get("source_kind")) or "",
                "source_ref": _optional_text(fact.get("source_ref")) or "",
                "source_scope": source_scope,
                "text": evidence,
                "normalized_text": normalized_text,
                "confidence": _evidence_confidence(fact.get("confidence")),
                "exact_source": source_scope in {SOURCE_SCOPE_EXACT, SOURCE_SCOPE_INFERRED},
                "batch_source_ids": batch_source_ids,
            }
        )
    return fallback_items


def _source_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    if scope in {
        SOURCE_SCOPE_EXACT,
        SOURCE_SCOPE_INFERRED,
        SOURCE_SCOPE_BATCH_FALLBACK,
        SOURCE_SCOPE_LEGACY,
    }:
        return scope
    return SOURCE_SCOPE_LEGACY


def _evidence_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, parsed))


def _normalized_evidence_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _evidence_key(*, fact_key: str, source_key: str, normalized_text: str) -> str:
    payload = "|".join([fact_key, source_key, normalized_text])
    return sha256(payload.encode("utf-8")).hexdigest()


def _dedupe_evidence_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("evidence_key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


class Neo4jGraphClient:
    """Small synchronous Neo4j client used from the async graph worker via to_thread."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        driver_factory: DriverFactory | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.driver_factory = driver_factory or _default_driver_factory
        self.driver: Any = None
        self._constraints_ready = False
        self._fulltext_indexes_ready = False
        self._fulltext_index_unavailable_reason: str | None = None
        self._graph_revision = 0
        self._retrieval_cache = GraphRetrievalCache()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def update_config(self, config: dict[str, Any] | None) -> None:
        new_config = dict(config or {})
        if _connection_signature(self.config) != _connection_signature(new_config):
            self.close()
            self._bump_graph_revision()
        self.config = new_config

    def initialize(self) -> None:
        if not self.enabled:
            return
        if self.driver is None:
            uri = str(self.config.get("uri") or "neo4j://localhost:7687")
            username = str(self.config.get("username") or "neo4j")
            password = str(self.config.get("password") or "")
            self.driver = self.driver_factory(uri, (username, password))
            self.driver.verify_connectivity()
        self.ensure_constraints()

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()
            self.driver = None
        self._retrieval_cache.clear()
        self._constraints_ready = False
        self._fulltext_indexes_ready = False
        self._fulltext_index_unavailable_reason = None

    def _bump_graph_revision(self) -> bool:
        self._retrieval_cache.clear()
        if self.driver is None:
            self._graph_revision += 1
            return False
        bumped_revision = self._bump_persistent_graph_revision()
        if bumped_revision is None:
            self._graph_revision += 1
            self._clear_persistent_multi_hop_expansion_cache()
            return False
        self._graph_revision = bumped_revision
        self._prune_persistent_multi_hop_expansion_cache(
            current_revision=bumped_revision,
        )
        return True

    def _cached_fulltext_seed_rows(
        self,
        fulltext_query: str,
        seed_limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        if not fulltext_query:
            return {"entity_seed_rows": [], "fact_seed_rows": []}
        key = (
            self._graph_revision,
            _ENTITY_FULLTEXT_INDEX,
            _FACT_FULLTEXT_INDEX,
            str(fulltext_query),
            int(seed_limit),
        )
        cached = self._retrieval_cache.get("fulltext_seed", key)
        if cached is not None:
            return cast(dict[str, list[dict[str, Any]]], cached)

        fact_seed_candidate_limit = _fulltext_fact_seed_candidate_limit(seed_limit)

        def load_seed_rows(session: Any) -> dict[str, list[dict[str, Any]]]:
            entity_rows = session.run(
                """
                CALL db.index.fulltext.queryNodes(
                  $entity_text_index,
                  $fulltext_query,
                  {limit: $seed_limit}
                )
                YIELD node AS seed, score
                RETURN elementId(seed) AS element_id, score
                """,
                entity_text_index=_ENTITY_FULLTEXT_INDEX,
                fulltext_query=fulltext_query,
                seed_limit=seed_limit,
                timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
            )
            fact_rows = session.run(
                """
                CALL db.index.fulltext.queryRelationships(
                  $fact_text_index,
                  $fulltext_query,
                  {limit: $fact_seed_candidate_limit}
                )
                YIELD relationship AS r, score
                WHERE coalesce(r[$status_property], 'active') = 'active'
                WITH r, score
                ORDER BY score DESC
                LIMIT $seed_limit
                RETURN elementId(r) AS element_id, score
                """,
                fact_text_index=_FACT_FULLTEXT_INDEX,
                fulltext_query=fulltext_query,
                seed_limit=seed_limit,
                fact_seed_candidate_limit=fact_seed_candidate_limit,
                status_property=_FACT_STATUS_PROPERTY,
                timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
            )
            return {
                "entity_seed_rows": _cached_seed_rows(entity_rows),
                "fact_seed_rows": _cached_seed_rows(fact_rows),
            }

        rows = self._run_with_reconnect(load_seed_rows)
        self._retrieval_cache.set("fulltext_seed", key, rows)
        return rows

    def _multi_hop_seed_rows(
        self,
        *,
        source_ids: list[str],
        source_score_rows: list[dict[str, float | str]],
        entity_seed_rows: list[dict[str, Any]],
        fact_seed_rows: list[dict[str, Any]],
        seed_limit: int,
    ) -> list[dict[str, Any]]:
        if not source_ids and not entity_seed_rows and not fact_seed_rows:
            return []
        query = """
        CALL () {
          MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
          WHERE source_node.source_id IN $source_ids
          MATCH (s:Entity)-[source_r:FACT]->(o:Entity)
          WHERE source_r.fact_key = fact_node.fact_key
            AND coalesce(source_r[$status_property], 'active') = 'active'
          WITH [s, o] AS source_seed_nodes, source_node
          UNWIND source_seed_nodes AS seed
          WITH seed, source_node,
               reduce(source_vector_score = 0.0, source_score IN $source_score_rows |
                 CASE
                   WHEN source_score.source_id = source_node.source_id
                        AND coalesce(toFloat(source_score.score), 0.0) > source_vector_score
                     THEN coalesce(toFloat(source_score.score), 0.0)
                   ELSE source_vector_score
                 END
               ) AS source_vector_score
          RETURN elementId(seed) AS element_id, 3.0 + source_vector_score * 2.0 AS seed_score
          UNION
          UNWIND $entity_seed_rows AS entity_seed
          RETURN entity_seed.element_id AS element_id,
                 coalesce(toFloat(entity_seed.score), 0.0) AS seed_score
          UNION
          UNWIND $fact_seed_rows AS fact_seed
          MATCH ()-[r:FACT]-()
          WHERE elementId(r) = fact_seed.element_id
            AND coalesce(r[$status_property], 'active') = 'active'
          WITH [startNode(r), endNode(r)] AS fact_seeds, fact_seed
          UNWIND fact_seeds AS seed
          WITH seed, fact_seed
          WHERE seed:Entity
          RETURN elementId(seed) AS element_id,
                 coalesce(toFloat(fact_seed.score), 0.0) AS seed_score
        }
        WITH element_id, max(seed_score) AS seed_score
        ORDER BY seed_score DESC
        LIMIT $seed_limit
        RETURN element_id, seed_score
        """
        rows = self._run_with_reconnect(
            lambda session: list(
                session.run(
                    query,
                    source_ids=source_ids,
                    source_score_rows=source_score_rows,
                    entity_seed_rows=entity_seed_rows,
                    fact_seed_rows=fact_seed_rows,
                    seed_limit=seed_limit,
                    status_property=_FACT_STATUS_PROPERTY,
                    timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                )
            )
        )
        return _cached_multi_hop_seed_rows(rows)

    def _cached_multi_hop_expansion_paths(
        self,
        *,
        seed_element_ids: list[str],
        depth: int,
        path_limit: int,
        load_misses: bool = True,
        use_persistent_cache: bool = True,
        alias_path_limits: list[int] | None = None,
    ) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
        cache_mode = self._multi_hop_expansion_cache_mode()
        use_persistent_cache = use_persistent_cache and cache_mode == "persistent"
        normalized_seed_ids = _unique_text_values(seed_element_ids)
        stats: dict[str, Any] = {
            "memory_hit_count": 0,
            "persistent_hit_count": 0,
            "loaded_count": 0,
            "complete_seed_ids": [],
        }
        normalized_alias_path_limits = []
        for alias_path_limit in alias_path_limits or []:
            try:
                parsed_alias_path_limit = int(alias_path_limit)
            except (TypeError, ValueError):
                continue
            if (
                parsed_alias_path_limit > 0
                and parsed_alias_path_limit != int(path_limit)
                and parsed_alias_path_limit not in normalized_alias_path_limits
            ):
                normalized_alias_path_limits.append(parsed_alias_path_limit)
        if not normalized_seed_ids or cache_mode == "off":
            return [], True, stats

        cached_by_seed: dict[str, dict[str, Any]] = {}
        missed_seed_ids = []
        for seed_element_id in normalized_seed_ids:
            key = _multi_hop_expansion_cache_key(
                revision=self._graph_revision,
                seed_element_id=seed_element_id,
                depth=depth,
                path_limit=path_limit,
            )
            cached = self._retrieval_cache.get("multi_hop_expansion", key)
            if cached is None:
                missed_seed_ids.append(seed_element_id)
                continue
            cached_by_seed[seed_element_id] = dict(cached)
            stats["memory_hit_count"] += 1

        if missed_seed_ids and use_persistent_cache:
            persistent_by_seed = self._load_persistent_multi_hop_expansion_paths(
                seed_element_ids=missed_seed_ids,
                depth=depth,
                path_limit=path_limit,
            )
            if persistent_by_seed:
                for seed_element_id, value in persistent_by_seed.items():
                    key = _multi_hop_expansion_cache_key(
                        revision=self._graph_revision,
                        seed_element_id=seed_element_id,
                        depth=depth,
                        path_limit=path_limit,
                    )
                    self._retrieval_cache.set("multi_hop_expansion", key, value)
                    cached_by_seed[seed_element_id] = value
                    stats["persistent_hit_count"] += 1
                missed_seed_ids = [
                    seed_element_id
                    for seed_element_id in missed_seed_ids
                    if seed_element_id not in cached_by_seed
                ]

        if missed_seed_ids and load_misses:
            loaded_by_seed = self._load_multi_hop_expansion_paths(
                seed_element_ids=missed_seed_ids,
                depth=depth,
                path_limit=path_limit,
            )
            loaded_values_by_seed: dict[str, dict[str, Any]] = {}
            for seed_element_id in missed_seed_ids:
                value = loaded_by_seed.get(
                    seed_element_id,
                    {"complete": True, "paths": []},
                )
                key = _multi_hop_expansion_cache_key(
                    revision=self._graph_revision,
                    seed_element_id=seed_element_id,
                    depth=depth,
                    path_limit=path_limit,
                )
                self._retrieval_cache.set("multi_hop_expansion", key, value)
                cached_by_seed[seed_element_id] = value
                loaded_values_by_seed[seed_element_id] = value
                stats["loaded_count"] += 1
            if use_persistent_cache:
                self._store_persistent_multi_hop_expansion_paths(
                    values_by_seed=loaded_values_by_seed,
                    depth=depth,
                    path_limit=path_limit,
                )
            complete_loaded_values_by_seed = {
                seed_element_id: value
                for seed_element_id, value in loaded_values_by_seed.items()
                if bool(value.get("complete", True)) and isinstance(value.get("paths"), list)
            }
            if complete_loaded_values_by_seed and normalized_alias_path_limits:
                for alias_path_limit in normalized_alias_path_limits:
                    for seed_element_id, value in complete_loaded_values_by_seed.items():
                        key = _multi_hop_expansion_cache_key(
                            revision=self._graph_revision,
                            seed_element_id=seed_element_id,
                            depth=depth,
                            path_limit=alias_path_limit,
                        )
                        self._retrieval_cache.set("multi_hop_expansion", key, value)
                    if use_persistent_cache:
                        self._store_persistent_multi_hop_expansion_paths(
                            values_by_seed=complete_loaded_values_by_seed,
                            depth=depth,
                            path_limit=alias_path_limit,
                        )

        expansion_rows: list[dict[str, Any]] = []
        complete = True
        for seed_element_id in normalized_seed_ids:
            cached_value = cached_by_seed.get(seed_element_id)
            if cached_value is None:
                complete = False
                continue
            if not bool(cached_value.get("complete", True)):
                complete = False
                continue
            paths = cached_value.get("paths")
            if not isinstance(paths, list):
                complete = False
                continue
            stats["complete_seed_ids"].append(seed_element_id)
            for path_index, path in enumerate(paths):
                cached_path = _multi_hop_expansion_cache_path_row(
                    seed_element_id,
                    path_index,
                    path,
                )
                if cached_path is not None:
                    expansion_rows.append(cached_path)
        return expansion_rows, complete, stats

    def _seed_identities_by_element_id(
        self,
        seed_element_ids: list[str],
    ) -> dict[str, dict[str, str]]:
        normalized_seed_ids = _unique_text_values(seed_element_ids)
        if not normalized_seed_ids:
            return {}
        query = """
        UNWIND $seed_element_ids AS seed_element_id
        MATCH (seed:Entity)
        WHERE elementId(seed) = seed_element_id
        RETURN seed_element_id,
               seed.name_key AS seed_name_key,
               seed.type_key AS seed_type_key
        """
        rows = self._run_with_reconnect(
            lambda session: list(
                session.run(
                    query,
                    seed_element_ids=normalized_seed_ids,
                    timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                )
            )
        )
        identities: dict[str, dict[str, str]] = {}
        for row in rows:
            seed_element_id = str(_row_value(row, "seed_element_id") or "").strip()
            identity = _seed_identity(
                _row_value(row, "seed_name_key"),
                _row_value(row, "seed_type_key"),
            )
            if seed_element_id and identity is not None:
                identities[seed_element_id] = identity
        return identities

    def _load_persistent_multi_hop_expansion_paths(
        self,
        *,
        seed_element_ids: list[str],
        depth: int,
        path_limit: int,
    ) -> dict[str, dict[str, Any]]:
        if not seed_element_ids or not self._persistent_multi_hop_expansion_cache_enabled():
            return {}
        try:
            seed_identities = self._seed_identities_by_element_id(seed_element_ids)
        except Exception as e:
            logger.debug("Neo4j graph persistent multi-hop seed identity read skipped: %s", e)
            return {}
        cache_keys_by_seed = {
            seed_element_id: _persistent_multi_hop_expansion_cache_key(
                revision=self._graph_revision,
                seed_identity=seed_identity,
                depth=depth,
                path_limit=path_limit,
            )
            for seed_element_id, seed_identity in seed_identities.items()
        }
        if not cache_keys_by_seed:
            return {}
        seed_by_cache_key = {key: seed for seed, key in cache_keys_by_seed.items()}
        query = """
        MATCH (cache:GraphExpansionCache)
        WHERE cache.key IN $cache_keys
        RETURN properties(cache) AS cache_properties
        """
        try:
            rows = self._run_with_reconnect(
                lambda session: list(
                    session.run(
                        query,
                        cache_keys=list(seed_by_cache_key),
                        timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                    )
                )
            )
        except Exception as e:
            logger.debug("Neo4j graph persistent multi-hop cache read skipped: %s", e)
            return {}

        values: dict[str, dict[str, Any]] = {}
        for row in rows:
            raw_cache_properties = _row_value(row, "cache_properties", {}) or {}
            cache_properties = (
                raw_cache_properties if isinstance(raw_cache_properties, dict) else {}
            )
            cache_key = str(cache_properties.get("key") or "").strip()
            seed_element_id = seed_by_cache_key.get(cache_key, "")
            if not seed_element_id:
                continue
            complete = bool(cache_properties.get("complete", True))
            raw_paths_json = str(cache_properties.get("paths_json") or "[]")
            try:
                raw_paths = json.loads(raw_paths_json)
            except (TypeError, ValueError):
                continue
            paths = [] if not complete else _normalized_multi_hop_expansion_paths(raw_paths)
            values[seed_element_id] = {"complete": complete, "paths": paths}
        return values

    def _store_persistent_multi_hop_expansion_paths(
        self,
        *,
        values_by_seed: dict[str, dict[str, Any]],
        depth: int,
        path_limit: int,
    ) -> None:
        if not values_by_seed or not self._persistent_multi_hop_expansion_cache_enabled():
            return
        now = time.time()
        entries = []
        for value in values_by_seed.values():
            seed_identity = _seed_identity_from_value(value.get("seed_identity"))
            if seed_identity is None:
                continue
            paths = value.get("paths")
            persistent_paths = _persistent_multi_hop_expansion_paths(paths)
            if persistent_paths is None:
                continue
            cache_key = _persistent_multi_hop_expansion_cache_key(
                revision=self._graph_revision,
                seed_identity=seed_identity,
                depth=depth,
                path_limit=path_limit,
            )
            entries.append(
                {
                    "key": cache_key,
                    "graph_revision": self._graph_revision,
                    "seed_name_key": seed_identity["name_key"],
                    "seed_type_key": seed_identity["type_key"],
                    "depth": depth,
                    "path_limit": path_limit,
                    "complete": bool(value.get("complete", True)),
                    "paths_json": json.dumps(
                        persistent_paths,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "updated_at": now,
                }
            )
        if not entries:
            return
        query = """
        UNWIND $expansion_cache_entries AS entry
        MERGE (cache:GraphExpansionCache {key: entry.key})
        SET cache.graph_revision = entry.graph_revision,
            cache.seed_name_key = entry.seed_name_key,
            cache.seed_type_key = entry.seed_type_key,
            cache.depth = entry.depth,
            cache.path_limit = entry.path_limit,
            cache.complete = entry.complete,
            cache.paths_json = entry.paths_json,
            cache.updated_at = entry.updated_at
        """
        try:
            self._run_with_reconnect(
                lambda session: list(
                    session.run(
                        query,
                        expansion_cache_entries=entries,
                        timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                    )
                )
            )
        except Exception as e:
            logger.debug("Neo4j graph persistent multi-hop cache write skipped: %s", e)

    def _persistent_multi_hop_expansion_cache_enabled(self) -> bool:
        return self._multi_hop_expansion_cache_mode() == "persistent"

    def _multi_hop_expansion_cache_mode(self) -> str:
        mode = str(self.config.get("multi_hop_expansion_cache_mode") or "").strip().lower()
        if mode in _MULTI_HOP_EXPANSION_CACHE_MODES:
            return mode
        enabled = _legacy_persistent_cache_enabled(
            self.config.get("persistent_multi_hop_expansion_cache_enabled")
        )
        if enabled is False:
            return "memory"
        return "persistent"

    def _load_multi_hop_expansion_paths(
        self,
        *,
        seed_element_ids: list[str],
        depth: int,
        path_limit: int,
    ) -> dict[str, dict[str, Any]]:
        if not seed_element_ids:
            return {}
        query = f"""
        UNWIND $seed_element_ids AS seed_element_id
        MATCH (seed:Entity)
        WHERE elementId(seed) = seed_element_id
        CALL (seed) {{
          MATCH path = (seed)-[:FACT*1..{depth}]-(o:Entity)
          WITH relationships(path) AS rels, length(path) AS hop
          WHERE hop > 1
            AND all(rel IN rels WHERE coalesce(rel[$status_property], 'active') = 'active')
          WITH rels, hop
          LIMIT $path_limit_plus_one
          RETURN collect({{
            hop: hop,
            rel_ids: [rel_index IN range(0, size(rels) - 1) |
              {{
                element_id: elementId(rels[rel_index]),
                fact_key: rels[rel_index].fact_key,
                rel_index: rel_index
              }}]
          }}) AS paths
        }}
        RETURN seed_element_id, paths,
               seed.name_key AS seed_name_key,
               seed.type_key AS seed_type_key,
               size(paths) > $path_limit AS incomplete
        """
        rows = self._run_with_reconnect(
            lambda session: list(
                session.run(
                    query,
                    seed_element_ids=seed_element_ids,
                    path_limit=path_limit,
                    path_limit_plus_one=path_limit + 1,
                    status_property=_FACT_STATUS_PROPERTY,
                    timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                )
            )
        )
        loaded: dict[str, dict[str, Any]] = {}
        for row in rows:
            seed_element_id = str(_row_value(row, "seed_element_id") or "").strip()
            if not seed_element_id:
                continue
            incomplete = bool(_row_value(row, "incomplete", False))
            raw_paths = _row_value(row, "paths", []) or []
            paths = [] if incomplete else _normalized_multi_hop_expansion_paths(raw_paths)
            seed_identity = _seed_identity(
                _row_value(row, "seed_name_key"),
                _row_value(row, "seed_type_key"),
            )
            value: dict[str, Any] = {"complete": not incomplete, "paths": paths}
            if seed_identity is not None:
                value["seed_identity"] = seed_identity
            loaded[seed_element_id] = value
        return loaded

    def _prewarm_multi_hop_expansion_cache(
        self,
        seed_element_ids: list[str],
        *,
        use_persistent_cache: bool = True,
    ) -> None:
        cache_mode = self._multi_hop_expansion_cache_mode()
        if cache_mode == "off":
            return
        use_persistent_cache = use_persistent_cache and cache_mode == "persistent"
        normalized_seed_ids = _unique_text_values(seed_element_ids)
        if not normalized_seed_ids:
            return
        max_depth = _retrieval_depth(
            self.config.get("retrieval_depth", GRAPH_RETRIEVAL_DEFAULT_DEPTH)
        )
        if max_depth <= 1:
            return
        seed_limit = _multi_hop_expansion_cache_preload_seed_limit(
            self.config.get(
                "multi_hop_expansion_cache_prewarm_seed_limit",
                self.config.get("multi_hop_expansion_cache_preload_seed_limit"),
            )
        )
        if seed_limit <= 0:
            return
        hot_seed_ids = normalized_seed_ids[:seed_limit]
        max_path_limit = _multi_hop_expansion_cache_path_limit(
            self.config.get("multi_hop_expansion_cache_path_limit")
        )
        path_budget = _multi_hop_expansion_cache_preload_path_limit(
            self.config.get("multi_hop_expansion_cache_preload_path_limit")
        )
        path_limit = min(
            max_path_limit,
            max(1, path_budget // len(hot_seed_ids)),
        )
        for depth in range(2, max_depth + 1):
            self._cached_multi_hop_expansion_paths(
                seed_element_ids=hot_seed_ids,
                depth=depth,
                path_limit=path_limit,
                load_misses=True,
                use_persistent_cache=use_persistent_cache,
                alias_path_limits=[max_path_limit],
            )

    def _bump_persistent_graph_revision(self) -> int | None:
        if self.driver is None:
            return None
        query = """
        MERGE (meta:GraphMetadata {key: $key})
          ON CREATE SET meta.value = 0
        SET meta.value = coalesce(meta.value, 0) + 1,
            meta.updated_at = $updated_at
        RETURN meta.value AS value
        """
        try:
            rows = self._run_with_reconnect(
                lambda session: list(
                    session.run(
                        query,
                        key=_GRAPH_REVISION_METADATA_KEY,
                        updated_at=time.time(),
                    )
                )
            )
            if rows:
                return int(_row_value(rows[0], "value", self._graph_revision) or 0)
        except Exception as e:
            logger.debug("Neo4j graph revision bump skipped: %s", e)
        return None

    def _refresh_graph_revision_from_store(self) -> bool:
        if self.driver is None:
            return False
        query = """
        MATCH (meta:GraphMetadata {key: $key})
        RETURN meta.value AS value
        """
        try:
            rows = self._run_with_reconnect(
                lambda session: list(
                    session.run(
                        query,
                        key=_GRAPH_REVISION_METADATA_KEY,
                        timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                    )
                )
            )
        except Exception as e:
            logger.debug("Neo4j graph revision refresh skipped: %s", e)
            return False
        if not rows:
            return False
        try:
            store_revision = int(_row_value(rows[0], "value", self._graph_revision) or 0)
        except (TypeError, ValueError):
            return False
        if store_revision <= self._graph_revision:
            return False
        self._graph_revision = store_revision
        self._retrieval_cache.clear()
        return True

    def _prune_persistent_multi_hop_expansion_cache(
        self,
        *,
        current_revision: int,
    ) -> None:
        if not self._persistent_multi_hop_expansion_cache_enabled():
            return
        query = """
        MATCH (cache:GraphExpansionCache)
        WITH cache, properties(cache) AS cache_properties
        WITH cache,
             toInteger(cache_properties[$graph_revision_property]) AS cache_graph_revision
        WHERE cache_graph_revision IS NULL OR cache_graph_revision < $current_revision
        DELETE cache
        """
        try:
            self._run_with_reconnect(
                lambda session: list(
                    session.run(
                        query,
                        current_revision=int(current_revision),
                        graph_revision_property="graph_revision",
                        timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                    )
                )
            )
        except Exception as e:
            logger.debug("Neo4j graph persistent multi-hop cache prune skipped: %s", e)

    def _clear_persistent_multi_hop_expansion_cache(self) -> None:
        if not self._persistent_multi_hop_expansion_cache_enabled():
            return
        query = """
        MATCH (cache:GraphExpansionCache)
        DELETE cache
        """
        try:
            self._run_with_reconnect(
                lambda session: list(
                    session.run(
                        query,
                        timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                    )
                )
            )
        except Exception as e:
            logger.warning(
                "Neo4j graph persistent multi-hop cache clear skipped after "
                "revision bump failure: %s",
                e,
            )

    def test_connection(self, config: dict[str, Any] | None = None) -> dict:
        cfg = {**self.config, **(config or {}), "enabled": True}
        uri = str(cfg.get("uri") or "neo4j://localhost:7687")
        username = str(cfg.get("username") or "neo4j")
        password = str(cfg.get("password") or "")
        database = str(cfg.get("database") or "neo4j")
        driver = self.driver_factory(uri, (username, password))
        try:
            driver.verify_connectivity()
            with driver.session(database=database) as session:
                session.run("RETURN 1 AS ok")
            return {"ok": True, "database": database}
        finally:
            driver.close()

    def ensure_constraints(self) -> None:
        if self._constraints_ready or self.driver is None:
            return
        statements = [
            "MATCH (e:Entity) WHERE e.type_key IS NULL AND e.type IS NOT NULL "
            "SET e.type_key = toLower(trim(e.type))",
            "MATCH (e:Entity) WHERE e.type_key IS NULL SET e.type_key = 'entity'",
            "MATCH ()-[r:FACT]-() "
            "WHERE r.chain_ids_text IS NULL AND size(coalesce(r.chain_ids, [])) > 0 "
            "SET r.chain_ids_text = reduce("
            "text = '', chain_id IN coalesce(r.chain_ids, []) | "
            "text + ' ' + toString(chain_id))",
            "DROP CONSTRAINT entity_name_key IF EXISTS",
            "CREATE CONSTRAINT entity_identity IF NOT EXISTS "
            "FOR (e:Entity) REQUIRE (e.name_key, e.type_key) IS UNIQUE",
            "CREATE CONSTRAINT fact_key IF NOT EXISTS "
            "FOR ()-[r:FACT]-() REQUIRE r.fact_key IS UNIQUE",
            "CREATE CONSTRAINT graph_source_id IF NOT EXISTS "
            "FOR (source:GraphSource) REQUIRE source.source_id IS UNIQUE",
            "CREATE CONSTRAINT graph_fact_key IF NOT EXISTS "
            "FOR (fact:GraphFact) REQUIRE fact.fact_key IS UNIQUE",
            "CREATE CONSTRAINT graph_evidence_key IF NOT EXISTS "
            "FOR (evidence:GraphEvidence) REQUIRE evidence.evidence_key IS UNIQUE",
            "CREATE CONSTRAINT graph_metadata_key IF NOT EXISTS "
            "FOR (meta:GraphMetadata) REQUIRE meta.key IS UNIQUE",
            "CREATE CONSTRAINT graph_expansion_cache_key IF NOT EXISTS "
            "FOR (cache:GraphExpansionCache) REQUIRE cache.key IS UNIQUE",
            "CREATE INDEX graph_expansion_cache_seed IF NOT EXISTS "
            "FOR (cache:GraphExpansionCache) ON (cache.seed_name_key, cache.seed_type_key)",
            "CREATE INDEX fact_source_id IF NOT EXISTS FOR ()-[r:FACT]-() ON (r.source_id)",
            "CREATE INDEX fact_updated_at IF NOT EXISTS FOR ()-[r:FACT]-() ON (r.updated_at)",
            "CREATE INDEX fact_predicate IF NOT EXISTS FOR ()-[r:FACT]-() ON (r.predicate)",
            "CREATE INDEX graph_evidence_fact_key IF NOT EXISTS "
            "FOR (evidence:GraphEvidence) ON (evidence.fact_key)",
            "CREATE INDEX graph_evidence_source_id IF NOT EXISTS "
            "FOR (evidence:GraphEvidence) ON (evidence.source_id)",
            "CREATE INDEX graph_evidence_fact_source_text IF NOT EXISTS "
            "FOR (evidence:GraphEvidence) ON "
            "(evidence.fact_key, evidence.source_id, evidence.normalized_text)",
            "CREATE INDEX entity_name_key IF NOT EXISTS FOR (e:Entity) ON (e.name_key)",
            "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS "
            "FOR (e:Entity) ON EACH [e.name, e.name_key, e.type, e.type_key]",
            "CREATE FULLTEXT INDEX fact_text IF NOT EXISTS "
            "FOR ()-[r:FACT]-() ON EACH "
            "[r.predicate, r.evidence, r.hyper_event, r.hyper_role, "
            "r.chain_id, r.chain_ids_text, r.evidence_text]",
        ]
        fulltext_ready = True
        fulltext_unavailable_reasons: list[str] = []
        with self._session() as session:
            for statement in statements:
                is_fulltext_index = "CREATE FULLTEXT INDEX" in statement
                try:
                    session.run(statement)
                except Exception as e:
                    if is_fulltext_index:
                        fulltext_ready = False
                        index_name = _fulltext_index_name(statement)
                        error_text = str(e) or e.__class__.__name__
                        fulltext_unavailable_reasons.append(f"{index_name}: {error_text}")
                        logger.warning(
                            "Neo4j graph fulltext index %s skipped; "
                            "falling back to scan retrieval: %s",
                            index_name,
                            e,
                        )
                    else:
                        logger.debug("Neo4j graph constraint skipped: %s", e)
            self._ensure_source_projection_backfill(session)
            self._ensure_evidence_ledger_backfill(session)
            try:
                revision_rows = list(
                    session.run(
                        """
                        MERGE (meta:GraphMetadata {key: $key})
                          ON CREATE SET meta.value = 0,
                                        meta.updated_at = $updated_at
                        RETURN meta.value AS value
                        """,
                        key=_GRAPH_REVISION_METADATA_KEY,
                        updated_at=time.time(),
                    )
                )
                if revision_rows:
                    self._graph_revision = int(
                        _row_value(revision_rows[0], "value", self._graph_revision) or 0
                    )
            except Exception as e:
                logger.debug("Neo4j graph revision load skipped: %s", e)
        self._constraints_ready = True
        self._fulltext_indexes_ready = fulltext_ready
        self._fulltext_index_unavailable_reason = (
            None if fulltext_ready else "; ".join(fulltext_unavailable_reasons)
        )

    def _ensure_source_projection_backfill(self, session: Any) -> None:
        if self._source_projection_backfill_marked_complete(session):
            return
        try:
            if self._source_projection_summary_is_complete(session):
                self._mark_source_projection_backfill_complete(session)
                return
        except Exception as e:
            logger.debug("Neo4j graph source projection summary skipped: %s", e)
        try:
            session.run(
                """
                MATCH (s:Entity)-[r:FACT]->(o:Entity)
                WHERE r.fact_key IS NOT NULL
                MERGE (fact_node:GraphFact {fact_key: r.fact_key})
                  ON CREATE SET fact_node.created_at = coalesce(r.created_at, r.updated_at)
                SET fact_node.updated_at = coalesce(r.updated_at, fact_node.updated_at),
                    fact_node.predicate = r.predicate
                MERGE (fact_node)-[:FACT_SUBJECT]->(s)
                MERGE (fact_node)-[:FACT_OBJECT]->(o)
                WITH r, fact_node,
                     CASE
                       WHEN size(coalesce(r.retrieval_source_ids, [])) > 0
                         THEN coalesce(r.retrieval_source_ids, [])
                       WHEN size(coalesce(r.source_ids, [])) > 0 THEN coalesce(r.source_ids, [])
                       WHEN size(coalesce(r.batch_source_ids, [])) > 0
                            AND coalesce(r.source_scope, '') = 'batch_fallback'
                         THEN coalesce(r.batch_source_ids, [])
                       WHEN r.source_id IS NULL THEN []
                       ELSE [r.source_id]
                     END AS source_ids,
                     coalesce(r.source_scope, '') IN ['exact', 'inferred']
                       AS exact_source_scope
                UNWIND source_ids AS source_id
                WITH DISTINCT fact_node, r, exact_source_scope,
                     trim(toString(source_id)) AS source_id
                WHERE source_id <> ''
                MERGE (source_node:GraphSource {source_id: source_id})
                MERGE (source_node)-[support:SUPPORTS_FACT]->(fact_node)
                SET support.exact_source = exact_source_scope
                    AND source_id IN coalesce(r.source_ids, []),
                    support.source_scope = CASE
                      WHEN exact_source_scope AND source_id IN coalesce(r.source_ids, [])
                        THEN r.source_scope
                      ELSE 'batch_fallback'
                    END
                """,
                timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
            )
            self._mark_source_projection_backfill_complete(session)
        except Exception as e:
            logger.debug("Neo4j graph source projection backfill skipped: %s", e)

    def _source_projection_backfill_marked_complete(self, session: Any) -> bool:
        rows = list(
            session.run(
                """
                MATCH (meta:GraphMetadata {key: $key})
                RETURN meta.value AS value
                """,
                key=_GRAPH_SOURCE_PROJECTION_BACKFILL_METADATA_KEY,
                timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
            )
        )
        if not rows:
            return False
        value = _row_value(rows[0], "value")
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)

    def _source_projection_summary_is_complete(self, session: Any) -> bool:
        rows = list(
            session.run(
                """
                CALL () {
                  MATCH ()-[r:FACT]->()
                  WHERE r.fact_key IS NOT NULL
                  RETURN count(r) AS fact_count
                }
                CALL () {
                  MATCH (f:GraphFact)
                  RETURN count(f) AS graph_fact_count
                }
                CALL () {
                  MATCH (:GraphFact)-[r:FACT_SUBJECT]->(:Entity)
                  RETURN count(r) AS subject_link_count
                }
                CALL () {
                  MATCH (:GraphFact)-[r:FACT_OBJECT]->(:Entity)
                  RETURN count(r) AS object_link_count
                }
                CALL () {
                  MATCH ()-[r:FACT]->()
                  WHERE r.fact_key IS NOT NULL
                  RETURN sum(
                    CASE
                      WHEN size(coalesce(r.retrieval_source_ids, [])) > 0
                        THEN size(r.retrieval_source_ids)
                      WHEN size(coalesce(r.batch_source_ids, [])) > 0
                           AND coalesce(r.source_scope, '') = 'batch_fallback'
                        THEN size(r.batch_source_ids)
                      WHEN size(coalesce(r.source_ids, [])) > 0 THEN size(r.source_ids)
                      WHEN r.source_count IS NOT NULL THEN toInteger(r.source_count)
                      WHEN r.source_id IS NULL OR trim(toString(r.source_id)) = '' THEN 0
                      ELSE 1
                    END
                  ) AS expected_source_link_count
                }
                CALL () {
                  MATCH (:GraphSource)-[r:SUPPORTS_FACT]->(:GraphFact)
                  RETURN count(r) AS actual_source_link_count
                }
                CALL () {
                  MATCH (:GraphSource)-[r:SUPPORTS_FACT]->(:GraphFact)
                  WHERE r.exact_source IS NULL
                     OR r.source_scope IS NULL
                     OR trim(toString(r.source_scope)) = ''
                  RETURN count(r) AS missing_support_property_count
                }
                RETURN fact_count,
                       graph_fact_count,
                       subject_link_count,
                       object_link_count,
                       expected_source_link_count,
                       actual_source_link_count,
                       missing_support_property_count
                """,
                timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
            )
        )
        if not rows:
            return False
        fact_count = _row_int(rows[0], "fact_count", -1)
        graph_fact_count = _row_int(rows[0], "graph_fact_count", -1)
        subject_link_count = _row_int(rows[0], "subject_link_count", -1)
        object_link_count = _row_int(rows[0], "object_link_count", -1)
        expected_source_link_count = _row_int(rows[0], "expected_source_link_count", -1)
        actual_source_link_count = _row_int(rows[0], "actual_source_link_count", -1)
        missing_support_property_count = _row_int(
            rows[0],
            "missing_support_property_count",
            -1,
        )
        return (
            fact_count >= 0
            and fact_count == graph_fact_count
            and subject_link_count >= fact_count
            and object_link_count >= fact_count
            and expected_source_link_count >= 0
            and actual_source_link_count >= expected_source_link_count
            and missing_support_property_count == 0
        )

    def _mark_source_projection_backfill_complete(self, session: Any) -> None:
        session.run(
            """
            MERGE (meta:GraphMetadata {key: $key})
              ON CREATE SET meta.created_at = $updated_at
            SET meta.value = $marker_value,
                meta.updated_at = $updated_at
            """,
            key=_GRAPH_SOURCE_PROJECTION_BACKFILL_METADATA_KEY,
            marker_value=1,
            updated_at=time.time(),
            timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
        )

    def _ensure_evidence_ledger_backfill(self, session: Any) -> None:
        if self._evidence_ledger_backfill_marked_complete(session):
            return
        try:
            session.run(
                """
                MATCH (s:Entity)-[r:FACT]->(o:Entity)
                WHERE r.fact_key IS NOT NULL
                  AND coalesce(r.evidence, '') <> ''
                MERGE (fact_node:GraphFact {fact_key: r.fact_key})
                  ON CREATE SET fact_node.created_at = coalesce(r.created_at, r.updated_at)
                SET fact_node.updated_at = coalesce(r.updated_at, fact_node.updated_at),
                    fact_node.predicate = r.predicate
                MERGE (fact_node)-[:FACT_SUBJECT]->(s)
                MERGE (fact_node)-[:FACT_OBJECT]->(o)
                OPTIONAL MATCH (existing_evidence:GraphEvidence)-[:SUPPORTS_FACT]->(fact_node)
                WITH r, fact_node, count(existing_evidence) AS existing_evidence_count
                WITH r, fact_node, existing_evidence_count,
                     CASE
                       WHEN coalesce(r.source_scope, '') IN [
                         'exact',
                         'inferred',
                         'batch_fallback',
                         'legacy'
                       ] THEN r.source_scope
                       ELSE 'legacy'
                     END AS source_scope,
                     CASE
                       WHEN size(coalesce(r.batch_source_ids, [])) > 0
                         THEN coalesce(r.batch_source_ids, [])
                       WHEN size(coalesce(r.retrieval_source_ids, [])) > 0
                         THEN coalesce(r.retrieval_source_ids, [])
                       WHEN size(coalesce(r.source_ids, [])) > 0
                         THEN coalesce(r.source_ids, [])
                       WHEN r.source_id IS NULL OR trim(toString(r.source_id)) = '' THEN []
                       ELSE [trim(toString(r.source_id))]
                     END AS batch_source_ids,
                     CASE
                       WHEN size(coalesce(r.retrieval_source_ids, [])) > 0
                         THEN coalesce(r.retrieval_source_ids, [])
                       WHEN size(coalesce(r.source_ids, [])) > 0
                         THEN coalesce(r.source_ids, [])
                       WHEN size(coalesce(r.batch_source_ids, [])) > 0
                            AND coalesce(r.source_scope, '') = 'batch_fallback'
                         THEN coalesce(r.batch_source_ids, [])
                       WHEN r.source_id IS NULL OR trim(toString(r.source_id)) = '' THEN []
                       ELSE [trim(toString(r.source_id))]
                     END AS retrieval_source_ids
                WITH r, fact_node, source_scope, batch_source_ids, retrieval_source_ids,
                     CASE
                       WHEN existing_evidence_count > 0 THEN []
                       WHEN source_scope = 'batch_fallback' THEN ['']
                       WHEN source_scope IN ['exact', 'inferred']
                            AND size(coalesce(r.source_ids, [])) > 0
                         THEN coalesce(r.source_ids, [])
                       WHEN source_scope IN ['exact', 'inferred']
                            AND r.source_id IS NOT NULL
                            AND trim(toString(r.source_id)) <> ''
                         THEN [trim(toString(r.source_id))]
                       WHEN source_scope = 'legacy'
                            AND size(coalesce(r.source_ids, [])) > 0
                         THEN coalesce(r.source_ids, [])
                       WHEN source_scope = 'legacy'
                            AND r.source_id IS NOT NULL
                            AND trim(toString(r.source_id)) <> ''
                         THEN [trim(toString(r.source_id))]
                       ELSE ['']
                     END AS evidence_source_ids
                CALL (r, fact_node, source_scope, batch_source_ids, evidence_source_ids) {
                  UNWIND evidence_source_ids AS raw_evidence_source_id
                  WITH r, fact_node, source_scope, batch_source_ids,
                       trim(toString(raw_evidence_source_id)) AS evidence_source_id
                  WITH r, fact_node, source_scope, batch_source_ids, evidence_source_id,
                       'legacy:' + toString(elementId(r)) + ':' + evidence_source_id
                       AS evidence_key
                  MERGE (evidence:GraphEvidence {evidence_key: evidence_key})
                    ON CREATE SET evidence.created_at = coalesce(r.created_at, r.updated_at)
                  SET evidence.updated_at = coalesce(r.updated_at, evidence.updated_at),
                      evidence.fact_key = r.fact_key,
                      evidence.source_id = evidence_source_id,
                      evidence.source_kind = coalesce(r.source_kind, ''),
                      evidence.source_ref = coalesce(r.source_ref, ''),
                      evidence.source_scope = source_scope,
                      evidence.text = r.evidence,
                      evidence.normalized_text = toLower(trim(toString(r.evidence))),
                      evidence.confidence = coalesce(toFloat(r.confidence), 1.0),
                      evidence.exact_source = source_scope IN ['exact', 'inferred']
                        AND evidence_source_id <> '',
                      evidence.batch_source_ids = batch_source_ids
                  MERGE (evidence)-[:SUPPORTS_FACT]->(fact_node)
                  WITH evidence, evidence_source_id
                  CALL (evidence, evidence_source_id) {
                    WITH evidence, evidence_source_id
                    WHERE evidence_source_id <> ''
                      AND coalesce(evidence.exact_source, false) = true
                    MERGE (source_node:GraphSource {source_id: evidence_source_id})
                    MERGE (source_node)-[:PROVIDES_EVIDENCE]->(evidence)
                    RETURN count(*) AS provided_evidence_source_count
                  }
                  RETURN count(DISTINCT evidence) AS backfilled_evidence_count
                }
                WITH DISTINCT r, fact_node, retrieval_source_ids
                CALL (fact_node) {
                  OPTIONAL MATCH (evidence:GraphEvidence)-[:SUPPORTS_FACT]->(fact_node)
                  WITH [item IN collect(evidence) WHERE item IS NOT NULL] AS evidences
                  WITH evidences,
                       [item IN evidences WHERE coalesce(item.source_id, '') <> '']
                         AS sourced_evidences,
                       [
                         item IN evidences
                         WHERE coalesce(item.exact_source, false) = true
                           AND coalesce(item.source_id, '') <> ''
                       ] AS strong_evidences
                  WITH evidences,
                       reduce(evidence_source_ids = [], item IN sourced_evidences |
                         CASE
                           WHEN item.source_id IN evidence_source_ids THEN evidence_source_ids
                           ELSE evidence_source_ids + [item.source_id]
                         END
                       ) AS evidence_source_ids,
                       reduce(strong_source_ids = [], item IN strong_evidences |
                         CASE
                           WHEN item.source_id IN strong_source_ids THEN strong_source_ids
                           ELSE strong_source_ids + [item.source_id]
                         END
                       ) AS strong_source_ids,
                       reduce(total_confidence = 0.0, item IN evidences |
                         total_confidence + coalesce(toFloat(item.confidence), 0.0)
                       ) AS total_confidence,
                       reduce(confidence_max = 0.0, item IN evidences |
                         CASE
                           WHEN coalesce(toFloat(item.confidence), 0.0) > confidence_max
                             THEN coalesce(toFloat(item.confidence), 0.0)
                           ELSE confidence_max
                         END
                       ) AS confidence_max,
                       [item IN evidences WHERE coalesce(item.text, '') <> '' | item.text][..3]
                         AS evidence_samples
                  WITH evidences, evidence_source_ids, strong_source_ids, total_confidence,
                       confidence_max, evidence_samples, size(evidences) AS evidence_count
                  WITH evidence_count, evidence_source_ids, strong_source_ids, confidence_max,
                       CASE
                         WHEN evidence_count = 0 THEN 0.0
                         ELSE total_confidence / toFloat(evidence_count)
                       END AS confidence_avg,
                       evidence_samples,
                       reduce(evidence_text = '', sample IN evidence_samples |
                         evidence_text
                         + CASE WHEN evidence_text = '' THEN '' ELSE ' ' END
                         + sample
                       ) AS evidence_text
                  RETURN {
                    mention_count: evidence_count,
                    evidence_count: evidence_count,
                    source_ids: strong_source_ids,
                    source_count: size(strong_source_ids),
                    strong_source_count: size(strong_source_ids),
                    confidence_max: confidence_max,
                    confidence_avg: confidence_avg,
                    evidence_samples: evidence_samples,
                    representative_evidence: CASE
                      WHEN size(evidence_samples) > 0 THEN evidence_samples[0]
                      ELSE NULL
                    END,
                    evidence_text: evidence_text,
                    support_weight: CASE
                      WHEN evidence_count = 0 THEN 0.0
                      ELSE log(1.0 + toFloat(evidence_count)) * 0.45
                        + CASE
                            WHEN size(strong_source_ids) > 5 THEN 1.0
                            ELSE toFloat(size(strong_source_ids)) / 5.0
                          END * 0.35
                        + confidence_max * 0.20
                    END
                  } AS aggregate
                }
                WITH r, retrieval_source_ids, aggregate,
                     CASE
                       WHEN size(retrieval_source_ids) > 5 THEN 1.0
                       ELSE toFloat(size(retrieval_source_ids)) / 5.0
                     END AS weak_source_count_score
                SET r.source_ids = CASE
                      WHEN size(aggregate.source_ids) > 0 THEN aggregate.source_ids
                      ELSE coalesce(r.source_ids, [])
                    END,
                    r.retrieval_source_ids = retrieval_source_ids,
                    r.source_count = CASE
                      WHEN aggregate.source_count > 0 THEN aggregate.source_count
                      ELSE size(retrieval_source_ids)
                    END,
                    r.strong_source_count = aggregate.strong_source_count,
                    r.mention_count = aggregate.mention_count,
                    r.evidence_count = aggregate.evidence_count,
                    r.support_weight = CASE
                      WHEN aggregate.source_count = 0 AND aggregate.evidence_count > 0
                        THEN aggregate.support_weight + weak_source_count_score * 0.15
                      ELSE aggregate.support_weight
                    END,
                    r.confidence_max = aggregate.confidence_max,
                    r.confidence_avg = aggregate.confidence_avg,
                    r.evidence_samples = aggregate.evidence_samples,
                    r.evidence_text = aggregate.evidence_text,
                    r.evidence = coalesce(aggregate.representative_evidence, r.evidence),
                    r.confidence = CASE
                      WHEN aggregate.evidence_count > 0 THEN aggregate.confidence_max
                      ELSE r.confidence
                    END
                """,
                timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
            )
            self._mark_evidence_ledger_backfill_complete(session)
        except Exception as e:
            logger.warning("Neo4j graph evidence ledger backfill skipped: %s", e)

    def _evidence_ledger_backfill_marked_complete(self, session: Any) -> bool:
        rows = list(
            session.run(
                """
                MATCH (meta:GraphMetadata {key: $key})
                RETURN meta.value AS value
                """,
                key=_GRAPH_EVIDENCE_LEDGER_BACKFILL_METADATA_KEY,
                timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
            )
        )
        if not rows:
            return False
        value = _row_value(rows[0], "value")
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)

    def _mark_evidence_ledger_backfill_complete(self, session: Any) -> None:
        session.run(
            """
            MERGE (meta:GraphMetadata {key: $key})
              ON CREATE SET meta.created_at = $updated_at
            SET meta.value = $marker_value,
                meta.updated_at = $updated_at
            """,
            key=_GRAPH_EVIDENCE_LEDGER_BACKFILL_METADATA_KEY,
            marker_value=1,
            updated_at=time.time(),
            timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
        )

    def upsert_facts(self, facts: list[dict]) -> int:
        if not facts or not self.enabled:
            return 0
        self.initialize()
        if self.driver is None:
            return 0
        now = time.time()
        rows = []
        for fact in facts:
            row = dict(fact)
            row["now"] = now
            row["subject_type_key"] = _entity_type_key(
                row.get("subject_type_key") or row.get("subject_type")
            )
            row["object_type_key"] = _entity_type_key(
                row.get("object_type_key") or row.get("object_type")
            )
            row["source_ids"] = _fact_source_ids(row)
            row["batch_source_ids"] = _fact_batch_source_ids(row)
            row["source_refs"] = _fact_source_refs(row)
            row["source_ref"] = row["source_refs"][0] if row["source_refs"] else ""
            row["source_scope"] = _source_scope(row.get("source_scope"))
            row["evidence_items"] = _fact_evidence_items(row)
            row["chain_id"] = _optional_text(row.get("chain_id"))
            row["chain_ids"] = _fact_chain_ids(row)
            row["chain_order"] = _optional_int(row.get("chain_order"))
            row["chain_order_keys"] = _fact_chain_order_keys(row)
            row["chain_from_role"] = _optional_text(row.get("chain_from_role"))
            row["chain_to_role"] = _optional_text(row.get("chain_to_role"))
            row["hyper_event"] = _optional_text(row.get("hyper_event"))
            row["hyper_event_type"] = _optional_text(row.get("hyper_event_type"))
            row["hyper_role"] = _optional_text(row.get("hyper_role"))
            row["derived_from_hyper_tuple"] = _optional_bool(row.get("derived_from_hyper_tuple"))
            row["structural"] = _optional_bool(row.get("structural"))
            row["status"] = _fact_status(row)
            row["conflict_policy"] = _fact_conflict_policy(row)
            row["slot_key"] = _fact_slot_key(row, row["conflict_policy"])
            row["valid_from"] = row.get("valid_from")
            row["valid_to"] = row.get("valid_to")
            row["superseded_by"] = _optional_text(row.get("superseded_by"))
            row["metadata_json"] = json.dumps(row.get("metadata") or {}, ensure_ascii=False)
            rows.append(row)
        rows = _fold_current_single_fact_rows(rows)
        if not rows:
            return 0
        query = """
        UNWIND $facts AS fact
        MERGE (s:Entity {name_key: fact.subject_key, type_key: fact.subject_type_key})
          ON CREATE SET s.name = fact.subject,
                        s.type = fact.subject_type,
                        s.created_at = fact.now
          SET s.name = coalesce(s.name, fact.subject),
              s.type = coalesce(s.type, fact.subject_type),
              s.updated_at = fact.now
        MERGE (o:Entity {name_key: fact.object_key, type_key: fact.object_type_key})
          ON CREATE SET o.name = fact.object,
                        o.type = fact.object_type,
                        o.created_at = fact.now
          SET o.name = coalesce(o.name, fact.object),
              o.type = coalesce(o.type, fact.object_type),
                        o.updated_at = fact.now
        MERGE (s)-[r:FACT {fact_key: fact.fact_key}]->(o)
          ON CREATE SET r.created_at = fact.now
        WITH s, o, r, fact
        CALL (s, fact) {
          WITH s, fact
          MATCH (s)-[old:FACT]->(:Entity)
          WHERE fact.conflict_policy = 'current_single'
            AND coalesce(fact.status, 'active') = 'active'
            AND fact.slot_key IS NOT NULL
            AND (
              old.slot_key = fact.slot_key
              OR (old.slot_key IS NULL AND old.predicate = fact.predicate)
            )
            AND old.fact_key <> fact.fact_key
            AND coalesce(old[$status_property], 'active') = 'active'
          SET old.status = 'superseded',
              old.conflict_policy = coalesce(old.conflict_policy, 'current_single'),
              old.slot_key = coalesce(old.slot_key, fact.slot_key),
              old.valid_to = fact.now,
              old.superseded_by = fact.fact_key,
              old.updated_at = fact.now
          RETURN count(old) AS superseded_count
        }
        WITH s, o, r, fact,
             coalesce(r.source_ids, []) + coalesce(fact.source_ids, [])
             AS raw_source_ids,
             coalesce(r.batch_source_ids, []) + coalesce(fact.batch_source_ids, [])
             AS raw_batch_source_ids,
             coalesce(r.source_refs, []) + coalesce(fact.source_refs, [])
             AS raw_source_refs,
             coalesce(r.chain_ids, []) + coalesce(fact.chain_ids, []) AS raw_chain_ids,
             coalesce(r.chain_order_keys, []) + coalesce(fact.chain_order_keys, [])
             AS raw_chain_order_keys
        WITH s, o, r, fact,
             reduce(source_ids = [], source_id IN raw_source_ids |
                  CASE
                    WHEN source_id IN source_ids THEN source_ids
                    ELSE source_ids + [source_id]
                  END) AS source_ids,
             reduce(batch_source_ids = [], source_id IN raw_batch_source_ids |
                  CASE
                    WHEN source_id IN batch_source_ids THEN batch_source_ids
                    ELSE batch_source_ids + [source_id]
                  END) AS batch_source_ids,
             reduce(source_refs = [], source_ref IN raw_source_refs |
                  CASE
                    WHEN source_ref IN source_refs THEN source_refs
                    ELSE source_refs + [source_ref]
                  END) AS source_refs,
             raw_chain_ids,
             raw_chain_order_keys
        WITH s, o, r, fact, source_ids, batch_source_ids, source_refs,
             coalesce(r.retrieval_source_ids, [])
             + source_ids + batch_source_ids AS raw_retrieval_source_ids,
             raw_chain_ids,
             raw_chain_order_keys
        WITH s, o, r, fact, source_ids, batch_source_ids, source_refs,
             reduce(retrieval_source_ids = [], source_id IN raw_retrieval_source_ids |
                  CASE
                    WHEN source_id IN retrieval_source_ids THEN retrieval_source_ids
                    ELSE retrieval_source_ids + [source_id]
                  END) AS retrieval_source_ids,
             reduce(chain_ids = [], chain_id IN raw_chain_ids |
                  CASE
                    WHEN chain_id IN chain_ids THEN chain_ids
                    ELSE chain_ids + [chain_id]
                  END) AS chain_ids,
             raw_chain_order_keys,
             CASE
               WHEN coalesce(r.source_scope, '') = 'exact'
                 OR coalesce(fact.source_scope, '') = 'exact'
                 THEN 'exact'
               WHEN coalesce(r.source_scope, '') = 'inferred'
                 OR coalesce(fact.source_scope, '') = 'inferred'
                 THEN 'inferred'
               WHEN coalesce(r.source_scope, '') = 'batch_fallback'
                 OR coalesce(fact.source_scope, '') = 'batch_fallback'
                 THEN 'batch_fallback'
               ELSE coalesce(fact.source_scope, r.source_scope, 'legacy')
             END AS source_scope
        WITH s, o, r, fact, source_ids, batch_source_ids, source_refs,
             retrieval_source_ids, chain_ids,
             source_scope,
             reduce(chain_order_keys = [], chain_order_key IN raw_chain_order_keys |
                  CASE
                    WHEN chain_order_key IN chain_order_keys THEN chain_order_keys
                    ELSE chain_order_keys + [chain_order_key]
                  END) AS chain_order_keys
          SET r.predicate = fact.predicate,
              r.status = coalesce(fact.status, 'active'),
              r.conflict_policy = fact.conflict_policy,
              r.slot_key = fact.slot_key,
              r.valid_from = coalesce(fact.valid_from, r.valid_from),
              r.valid_to = fact.valid_to,
              r.superseded_by = fact.superseded_by,
              r.source_id = fact.source_id,
              r.source_ids = source_ids,
              r.retrieval_source_ids = retrieval_source_ids,
              r.batch_source_ids = batch_source_ids,
              r.source_refs = source_refs,
              r.source_ref = CASE
                WHEN size(source_refs) > 0 THEN source_refs[0]
                ELSE coalesce(fact.source_ref, r.source_ref, '')
              END,
              r.source_scope = source_scope,
              r.source_kind = fact.source_kind,
              r.evidence = fact.evidence,
              r.confidence = fact.confidence,
              r.metadata_json = fact.metadata_json,
              r.chain_id = coalesce(fact.chain_id, r.chain_id),
              r.chain_ids = chain_ids,
              r.chain_ids_text = CASE
                WHEN size(chain_ids) > 0 THEN reduce(
                  text = '',
                  chain_id IN chain_ids |
                    text + ' ' + toString(chain_id)
                )
                ELSE NULL
              END,
              r.chain_order = CASE
                WHEN size(chain_order_keys) = 1
                  THEN toInteger(split(chain_order_keys[0], $chain_order_separator)[1])
                WHEN size(chain_order_keys) > 1 THEN NULL
                WHEN fact.chain_order IS NULL THEN r.chain_order
                ELSE fact.chain_order
              END,
              r.chain_order_keys = chain_order_keys,
              r.chain_from_role = coalesce(fact.chain_from_role, r.chain_from_role),
              r.chain_to_role = coalesce(fact.chain_to_role, r.chain_to_role),
              r.hyper_event = coalesce(fact.hyper_event, r.hyper_event),
              r.hyper_event_type = coalesce(fact.hyper_event_type, r.hyper_event_type),
              r.hyper_role = coalesce(fact.hyper_role, r.hyper_role),
              r.derived_from_hyper_tuple = coalesce(
                fact.derived_from_hyper_tuple,
                r.derived_from_hyper_tuple,
                false
              ),
              r.structural = coalesce(fact.structural, r.structural, false),
              r.updated_at = fact.now
        MERGE (fact_node:GraphFact {fact_key: fact.fact_key})
          ON CREATE SET fact_node.created_at = fact.now
        SET fact_node.updated_at = fact.now,
            fact_node.predicate = fact.predicate
        MERGE (fact_node)-[:FACT_SUBJECT]->(s)
        MERGE (fact_node)-[:FACT_OBJECT]->(o)
        WITH s, o, r, fact_node, source_ids, retrieval_source_ids, source_scope
        CALL (fact_node, source_ids, retrieval_source_ids, source_scope) {
          UNWIND retrieval_source_ids AS source_id
          WITH DISTINCT fact_node, source_ids, source_scope, trim(toString(source_id)) AS source_id
          WHERE source_id <> ''
          MERGE (source_node:GraphSource {source_id: source_id})
          MERGE (source_node)-[support:SUPPORTS_FACT]->(fact_node)
          SET support.exact_source = source_id IN source_ids,
              support.source_scope = CASE
                WHEN source_id IN source_ids THEN source_scope
                ELSE 'batch_fallback'
              END
          RETURN count(*) AS linked_source_count
        }
        WITH s, o, r, fact, fact_node, source_ids, retrieval_source_ids
        CALL (fact_node, fact) {
          UNWIND coalesce(fact.evidence_items, []) AS evidence_row
          WITH fact_node, fact, evidence_row
          WHERE coalesce(evidence_row.evidence_key, '') <> ''
          OPTIONAL MATCH (existing_evidence:GraphEvidence {
            fact_key: evidence_row.fact_key,
            source_id: coalesce(evidence_row.source_id, '')
          })
          WHERE existing_evidence.normalized_text = evidence_row.normalized_text
          WITH fact_node, fact, evidence_row,
               coalesce(existing_evidence.evidence_key, evidence_row.evidence_key)
                 AS evidence_key
          MERGE (evidence:GraphEvidence {evidence_key: evidence_key})
            ON CREATE SET evidence.created_at = fact.now
          WITH fact_node, fact, evidence, evidence_row,
               CASE
                 WHEN coalesce(evidence.source_scope, '') = 'exact'
                   OR coalesce(evidence_row.source_scope, fact.source_scope) = 'exact'
                   THEN 'exact'
                 WHEN coalesce(evidence.source_scope, '') = 'inferred'
                   OR coalesce(evidence_row.source_scope, fact.source_scope) = 'inferred'
                   THEN 'inferred'
                 WHEN coalesce(evidence.source_scope, '') = 'batch_fallback'
                   OR coalesce(evidence_row.source_scope, fact.source_scope) = 'batch_fallback'
                   THEN 'batch_fallback'
                 ELSE coalesce(evidence_row.source_scope, evidence.source_scope, fact.source_scope)
               END AS evidence_source_scope,
               coalesce(evidence.batch_source_ids, [])
               + coalesce(evidence_row.batch_source_ids, []) AS raw_evidence_batch_source_ids
          WITH fact_node, fact, evidence, evidence_row, evidence_source_scope,
               reduce(evidence_batch_source_ids = [],
                 source_id IN raw_evidence_batch_source_ids |
                   CASE
                     WHEN source_id IN evidence_batch_source_ids THEN evidence_batch_source_ids
                     ELSE evidence_batch_source_ids + [source_id]
                   END
               ) AS evidence_batch_source_ids
          SET evidence.updated_at = fact.now,
              evidence.fact_key = evidence_row.fact_key,
              evidence.source_id = coalesce(evidence_row.source_id, ''),
              evidence.source_kind = coalesce(evidence_row.source_kind, fact.source_kind),
              evidence.source_ref = CASE
                WHEN coalesce(evidence.source_ref, '') <> '' THEN evidence.source_ref
                ELSE coalesce(evidence_row.source_ref, '')
              END,
              evidence.source_scope = evidence_source_scope,
              evidence.text = evidence_row.text,
              evidence.normalized_text = evidence_row.normalized_text,
              evidence.confidence = CASE
                WHEN coalesce(toFloat(evidence.confidence), 0.0)
                     > coalesce(toFloat(evidence_row.confidence), 1.0)
                  THEN coalesce(toFloat(evidence.confidence), 0.0)
                ELSE coalesce(toFloat(evidence_row.confidence), 1.0)
              END,
              evidence.exact_source = evidence_source_scope IN ['exact', 'inferred'],
              evidence.batch_source_ids = evidence_batch_source_ids
          MERGE (evidence)-[:SUPPORTS_FACT]->(fact_node)
          WITH evidence, evidence_row
          CALL (evidence, evidence_row) {
            WITH evidence, evidence_row
            WHERE coalesce(evidence_row.source_id, '') <> ''
              AND coalesce(evidence_row.exact_source, false) = true
            MERGE (source_node:GraphSource {source_id: evidence_row.source_id})
            MERGE (source_node)-[:PROVIDES_EVIDENCE]->(evidence)
            RETURN count(*) AS evidence_source_link_count
          }
          RETURN count(DISTINCT evidence) AS evidence_write_count
        }
        WITH s, o, r, fact, fact_node, source_ids, retrieval_source_ids
        CALL (fact_node) {
          OPTIONAL MATCH (evidence:GraphEvidence)-[:SUPPORTS_FACT]->(fact_node)
          WITH [item IN collect(evidence) WHERE item IS NOT NULL] AS evidences
          WITH evidences,
               [item IN evidences WHERE coalesce(item.source_id, '') <> '']
                 AS sourced_evidences,
               [
                 item IN evidences
                 WHERE coalesce(item.exact_source, false) = true
                   AND coalesce(item.source_id, '') <> ''
               ] AS strong_evidences
          WITH evidences,
               reduce(evidence_source_ids = [], item IN sourced_evidences |
                 CASE
                   WHEN item.source_id IN evidence_source_ids THEN evidence_source_ids
                   ELSE evidence_source_ids + [item.source_id]
                 END
               ) AS evidence_source_ids,
               reduce(strong_source_ids = [], item IN strong_evidences |
                 CASE
                   WHEN item.source_id IN strong_source_ids THEN strong_source_ids
                   ELSE strong_source_ids + [item.source_id]
                 END
               ) AS strong_source_ids,
               reduce(total_confidence = 0.0, item IN evidences |
                 total_confidence + coalesce(toFloat(item.confidence), 0.0)
               ) AS total_confidence,
               reduce(confidence_max = 0.0, item IN evidences |
                 CASE
                   WHEN coalesce(toFloat(item.confidence), 0.0) > confidence_max
                     THEN coalesce(toFloat(item.confidence), 0.0)
                   ELSE confidence_max
                 END
               ) AS confidence_max,
               [item IN evidences WHERE coalesce(item.text, '') <> '' | item.text][..3]
                 AS evidence_samples
          WITH evidences, evidence_source_ids, strong_source_ids, total_confidence,
               confidence_max, evidence_samples, size(evidences) AS evidence_count
          WITH evidence_count, evidence_source_ids, strong_source_ids, confidence_max,
               CASE
                 WHEN evidence_count = 0 THEN 0.0
                 ELSE total_confidence / toFloat(evidence_count)
               END AS confidence_avg,
               evidence_samples,
               reduce(evidence_text = '', sample IN evidence_samples |
                 evidence_text
                 + CASE WHEN evidence_text = '' THEN '' ELSE ' ' END
                 + sample
               ) AS evidence_text
          RETURN {
            mention_count: evidence_count,
            evidence_count: evidence_count,
            source_ids: strong_source_ids,
            source_count: size(strong_source_ids),
            strong_source_count: size(strong_source_ids),
            confidence_max: confidence_max,
            confidence_avg: confidence_avg,
            evidence_samples: evidence_samples,
            representative_evidence: CASE
              WHEN size(evidence_samples) > 0 THEN evidence_samples[0]
              ELSE NULL
            END,
            evidence_text: evidence_text,
            support_weight: CASE
              WHEN evidence_count = 0 THEN 0.0
              ELSE log(1.0 + toFloat(evidence_count)) * 0.45
                + CASE
                    WHEN size(strong_source_ids) > 5 THEN 1.0
                    ELSE toFloat(size(strong_source_ids)) / 5.0
                  END * 0.35
                + confidence_max * 0.20
            END
          } AS aggregate
        }
        WITH s, o, r, fact, source_ids, retrieval_source_ids, aggregate,
             CASE
               WHEN size(retrieval_source_ids) > 5 THEN 1.0
               ELSE toFloat(size(retrieval_source_ids)) / 5.0
             END AS weak_source_count_score,
             reduce(
               merged_source_ids = [],
               source_id IN source_ids + coalesce(aggregate.source_ids, []) |
                 CASE
                   WHEN source_id IN merged_source_ids THEN merged_source_ids
                   ELSE merged_source_ids + [source_id]
                 END
             ) AS merged_source_ids
        SET r.source_ids = CASE
              WHEN aggregate.evidence_count > 0 THEN merged_source_ids
              ELSE source_ids
            END,
            r.source_count = CASE
              WHEN aggregate.source_count > 0 THEN aggregate.source_count
              ELSE size(retrieval_source_ids)
            END,
            r.strong_source_count = aggregate.strong_source_count,
            r.mention_count = aggregate.mention_count,
            r.evidence_count = aggregate.evidence_count,
            r.support_weight = CASE
              WHEN aggregate.source_count = 0 AND aggregate.evidence_count > 0
                THEN aggregate.support_weight + weak_source_count_score * 0.15
              ELSE aggregate.support_weight
            END,
            r.confidence_max = aggregate.confidence_max,
            r.confidence_avg = aggregate.confidence_avg,
            r.evidence_samples = aggregate.evidence_samples,
            r.evidence_text = aggregate.evidence_text,
            r.evidence = coalesce(aggregate.representative_evidence, r.evidence),
            r.confidence = CASE
              WHEN aggregate.evidence_count > 0 THEN aggregate.confidence_max
              ELSE r.confidence
            END
        RETURN count(DISTINCT r) AS count,
               collect(DISTINCT elementId(s)) + collect(DISTINCT elementId(o))
                 AS seed_element_ids
        """
        result = self._run_with_reconnect(
            lambda session: list(
                session.run(
                    query,
                    facts=rows,
                    chain_order_separator=CHAIN_ORDER_KEY_SEPARATOR,
                    status_property=_FACT_STATUS_PROPERTY,
                )
            )
        )
        count = _result_count(result, len(rows))
        if count > 0:
            seed_element_ids = _result_seed_element_ids(result)
            use_persistent_cache = self._bump_graph_revision()
            try:
                self._prewarm_multi_hop_expansion_cache(
                    seed_element_ids,
                    use_persistent_cache=use_persistent_cache,
                )
            except Exception as e:
                logger.debug("Neo4j graph multi-hop cache prewarm skipped: %s", e)
        return count

    def retrieve_context(
        self,
        *,
        query: str,
        source_ids: list[str] | None = None,
        source_scores: dict[str, float] | None = None,
        max_facts: int = 8,
        retrieval_depth: int = GRAPH_RETRIEVAL_DEFAULT_DEPTH,
        ranking_policy: str = "hybrid",
        expansion_candidate_limit: int | None = None,
        include_entity_types: bool = False,
        timings: dict[str, Any] | None = None,
    ) -> str:
        if not self.enabled:
            return ""
        started_at = time.perf_counter()
        self.initialize()
        if self.driver is None:
            return ""
        self._refresh_graph_revision_from_store()
        term_rows = _query_term_rows(query)
        terms = [str(row["term"]) for row in term_rows]
        multihop_seed_count = 0
        multihop_cache_hit = False
        multihop_cached_seed_count = 0
        multihop_live_seed_limit = 0
        multihop_partial_cache_hit = False
        multihop_persistent_cache_hit_count = 0
        if not terms and not source_ids:
            _record_timing(timings, "graph_total_ms", started_at)
            _record_count(timings, "graph_multihop_seed_count", multihop_seed_count)
            _set_bool(timings, "graph_multihop_cache_hit", multihop_cache_hit)
            _record_count(
                timings,
                "graph_multihop_cached_seed_count",
                multihop_cached_seed_count,
            )
            _record_count(
                timings,
                "graph_multihop_live_seed_limit",
                multihop_live_seed_limit,
            )
            _set_bool(
                timings,
                "graph_multihop_partial_cache_hit",
                multihop_partial_cache_hit,
            )
            _record_count(
                timings,
                "graph_multihop_persistent_cache_hit_count",
                multihop_persistent_cache_hit_count,
            )
            _set_bool(timings, "graph_multihop_degraded", False)
            return ""
        depth = _retrieval_depth(retrieval_depth)
        policy = _ranking_policy(ranking_policy)
        source_score_rows = _source_score_rows(source_ids or [], source_scores or {})
        query_limit = max(1, int(max_facts or 8))
        expansion_query_limit = _expansion_candidate_limit(
            expansion_candidate_limit
            if expansion_candidate_limit is not None
            else self.config.get("expansion_candidate_limit")
        )
        expansion_cache_path_limit = _multi_hop_expansion_cache_path_limit(
            self.config.get("multi_hop_expansion_cache_path_limit")
        )
        expansion_cache_preload_seed_limit = _multi_hop_expansion_cache_preload_seed_limit(
            self.config.get("multi_hop_expansion_cache_preload_seed_limit")
        )
        expansion_cache_preload_path_limit = _multi_hop_expansion_cache_preload_path_limit(
            self.config.get("multi_hop_expansion_cache_preload_path_limit")
        )
        multi_hop_expansion_cache_mode = self._multi_hop_expansion_cache_mode()
        multi_hop_expansion_cache_enabled = multi_hop_expansion_cache_mode != "off"
        use_persistent_multi_hop_expansion_cache = multi_hop_expansion_cache_mode == "persistent"
        seed_limit = max(query_limit, expansion_query_limit)
        live_seed_limit = seed_limit if depth > 1 else 0
        multihop_live_seed_limit = live_seed_limit
        fulltext_query = _fulltext_query(terms) if self._fulltext_indexes_ready else ""
        multihop_degraded = bool(depth > 1 and terms and not fulltext_query and not source_ids)
        if terms and not self._fulltext_indexes_ready:
            logger.warning(
                "Neo4j graph fulltext index unavailable; using scan fallback "
                "for graph retrieval: %s",
                self._fulltext_index_unavailable_reason or "fulltext indexes are not ready",
            )
        final_cache_key = _final_context_cache_key(
            revision=self._graph_revision,
            query=query,
            source_ids=source_ids or [],
            source_score_rows=source_score_rows,
            max_facts=query_limit,
            retrieval_depth=depth,
            ranking_policy=policy,
            expansion_candidate_limit=expansion_query_limit,
            multi_hop_expansion_cache_mode=multi_hop_expansion_cache_mode,
            multi_hop_expansion_cache_preload_seed_limit=expansion_cache_preload_seed_limit,
            multi_hop_expansion_cache_path_limit=expansion_cache_path_limit,
            multi_hop_expansion_cache_preload_path_limit=expansion_cache_preload_path_limit,
            include_entity_types=include_entity_types,
            fulltext_ready=self._fulltext_indexes_ready,
        )
        cached_context = self._retrieval_cache.get("final_context", final_cache_key)
        if cached_context is not None:
            _record_timing(timings, "graph_total_ms", started_at)
            _set_timing(timings, "graph_single_hop_ms", 0.0)
            _set_timing(timings, "graph_multi_hop_ms", 0.0)
            _set_timing(timings, "graph_scan_fallback_ms", 0.0)
            _set_timing(timings, "graph_format_ms", 0.0)
            _record_count(timings, "graph_rows", 0)
            _record_count(
                timings,
                "graph_returned_facts",
                _graph_context_fact_count(cached_context),
            )
            _set_bool(timings, "graph_used_fulltext", bool(fulltext_query))
            _set_bool(timings, "graph_used_scan_fallback", False)
            _set_bool(timings, "graph_cache_hit", True)
            _record_count(timings, "graph_multihop_seed_count", multihop_seed_count)
            _set_bool(timings, "graph_multihop_cache_hit", multihop_cache_hit)
            _record_count(
                timings,
                "graph_multihop_cached_seed_count",
                multihop_cached_seed_count,
            )
            _record_count(
                timings,
                "graph_multihop_live_seed_limit",
                multihop_live_seed_limit,
            )
            _set_bool(
                timings,
                "graph_multihop_partial_cache_hit",
                multihop_partial_cache_hit,
            )
            _record_count(
                timings,
                "graph_multihop_persistent_cache_hit_count",
                multihop_persistent_cache_hit_count,
            )
            _set_bool(timings, "graph_multihop_degraded", multihop_degraded)
            return str(cached_context)
        _set_bool(timings, "graph_cache_hit", False)
        fulltext_seed_rows = self._cached_fulltext_seed_rows(fulltext_query, seed_limit)
        entity_seed_rows = fulltext_seed_rows["entity_seed_rows"]
        fact_seed_rows = fulltext_seed_rows["fact_seed_rows"]
        cached_expansion_paths: list[dict[str, Any]] = []
        cached_expansion_seed_rows: list[dict[str, Any]] = []
        cached_expansion_seed_ids: list[str] = []
        single_hop_order_by = (
            "ORDER BY structural_role ASC, r.updated_at DESC"
            if policy == "latest"
            else "ORDER BY structural_role ASC, graph_score DESC, r.updated_at DESC"
        )
        multi_hop_path_order_by = (
            "ORDER BY structural_role ASC, chain_path_score DESC, "
            "chain_order_score DESC, hop DESC, r.updated_at DESC"
            if policy == "latest"
            else "ORDER BY structural_role ASC, chain_path_score DESC, "
            "chain_order_score DESC, hop DESC, graph_score DESC, r.updated_at DESC"
        )
        multi_hop_edge_order_by = (
            "ORDER BY structural_role ASC, graph_score DESC, hop ASC, r.updated_at DESC"
        )
        if fulltext_query:
            single_hop_seed_cypher = """
        CALL () {
          MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
          WHERE source_node.source_id IN $source_ids
          MATCH (s:Entity)-[r:FACT]->(o:Entity)
          WHERE r.fact_key = fact_node.fact_key
          RETURN s, r, o, 3.0 AS index_score
          UNION
          UNWIND $entity_seed_rows AS entity_seed
          MATCH (seed:Entity)
          WHERE elementId(seed) = entity_seed.element_id
          MATCH (seed)-[r:FACT]-(other:Entity)
          WITH startNode(r) AS s, r, endNode(r) AS o, entity_seed
          WHERE s:Entity AND o:Entity
          RETURN s, r, o, coalesce(toFloat(entity_seed.score), 0.0) AS index_score
          UNION
          UNWIND $fact_seed_rows AS fact_seed
          MATCH ()-[r:FACT]-()
          WHERE elementId(r) = fact_seed.element_id
          WITH startNode(r) AS s, r, endNode(r) AS o, fact_seed
          WHERE s:Entity AND o:Entity
          RETURN s, r, o, coalesce(toFloat(fact_seed.score), 0.0) AS index_score
        }
        WITH s, r, o, max(index_score) AS index_score
            """
        elif terms:
            single_hop_seed_cypher = """
        CALL () {
          MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
          WHERE source_node.source_id IN $source_ids
          MATCH (s:Entity)-[r:FACT]->(o:Entity)
          WHERE r.fact_key = fact_node.fact_key
          RETURN s, r, o, 3.0 AS index_score
          UNION
          MATCH (s:Entity)-[r:FACT]->(o:Entity)
          WHERE any(term IN $terms WHERE
              toLower(s.name) CONTAINS term
              OR toLower(o.name) CONTAINS term
              OR toLower(r.predicate) CONTAINS term
              OR toLower(coalesce(r.evidence, '')) CONTAINS term
              OR toLower(coalesce(r[$hyper_event_property], '')) CONTAINS term
              OR toLower(coalesce(r[$hyper_role_property], '')) CONTAINS term
              OR toLower(coalesce(r[$chain_id_property], '')) CONTAINS term
              OR any(chain_id IN coalesce(r[$chain_ids_property], [])
                     WHERE toLower(toString(chain_id)) CONTAINS term))
          RETURN s, r, o, 0.0 AS index_score
        }
        WITH s, r, o, max(index_score) AS index_score
            """
        else:
            single_hop_seed_cypher = """
        MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
        WHERE source_node.source_id IN $source_ids
        MATCH (s:Entity)-[r:FACT]->(o:Entity)
        WHERE r.fact_key = fact_node.fact_key
        WITH s, r, o, 3.0 AS index_score
            """
        single_hop_cypher = f"""
        {single_hop_seed_cypher}
        WHERE coalesce(r[$status_property], 'active') = 'active'
        WITH s, r, o, index_score,
             CASE
               WHEN size(coalesce(r.source_ids, [])) > 0
                    AND coalesce(r.source_scope, '') IN ['exact', 'inferred']
                 THEN coalesce(r.source_ids, [])
               WHEN size(coalesce(r.retrieval_source_ids, [])) > 0
                 THEN coalesce(r.retrieval_source_ids, [])
               WHEN size(coalesce(r.batch_source_ids, [])) > 0
                 THEN coalesce(r.batch_source_ids, [])
               WHEN size(coalesce(r.source_ids, [])) > 0 THEN coalesce(r.source_ids, [])
               WHEN r.source_id IS NULL THEN []
               ELSE [r.source_id]
             END AS fact_source_ids
        WITH s, r, o, index_score, fact_source_ids,
             CASE
               WHEN EXISTS {{
                 MATCH (source_node:GraphSource)-[support:SUPPORTS_FACT]->(fact_node:GraphFact)
                 WHERE source_node.source_id IN $source_ids
                   AND fact_node.fact_key = r.fact_key
                   AND coalesce(support.exact_source, false) = true
               }} THEN 3.0
               WHEN EXISTS {{
                 MATCH (source_node:GraphSource)-[support:SUPPORTS_FACT]->(fact_node:GraphFact)
                 WHERE source_node.source_id IN $source_ids
                   AND fact_node.fact_key = r.fact_key
               }} THEN 1.0
               ELSE 0.0
             END AS source_match_score,
             reduce(source_vector_score = 0.0, source_score IN $source_score_rows |
               CASE
                 WHEN source_score.source_id IN fact_source_ids
                      AND coalesce(toFloat(source_score.score), 0.0) > source_vector_score
                   THEN coalesce(toFloat(source_score.score), 0.0)
                 ELSE source_vector_score
               END
             ) AS source_vector_score,
             reduce(term_score = 0.0, term_row IN $term_rows |
               term_score
               + CASE WHEN toLower(coalesce(s.name, '')) CONTAINS term_row.term
                      THEN 2.0 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(o.name, '')) CONTAINS term_row.term
                      THEN 2.0 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r.predicate, '')) CONTAINS term_row.term
                      THEN 1.0 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r.evidence, '')) CONTAINS term_row.term
                      THEN 0.5 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r[$hyper_event_property], '')) CONTAINS term_row.term
                      THEN 2.0 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r[$hyper_role_property], '')) CONTAINS term_row.term
                      THEN 1.0 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r[$chain_id_property], '')) CONTAINS term_row.term
                      THEN 0.5 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN any(chain_id IN coalesce(r[$chain_ids_property], [])
                         WHERE toLower(toString(chain_id)) CONTAINS term_row.term)
                      THEN 0.5 * coalesce(toFloat(term_row.weight), 1.0)
                      ELSE 0.0
                 END
             ) AS term_match_score,
             coalesce(toFloat(r.confidence), 0.0) AS confidence_score,
             CASE
               WHEN coalesce(toFloat(r.source_count), 0.0) > 5.0 THEN 1.0
               WHEN coalesce(toFloat(r.source_count), 0.0) > 0.0
                 THEN toFloat(r.source_count) / 5.0
               WHEN toFloat(size(fact_source_ids)) > 5.0 THEN 1.0
               ELSE toFloat(size(fact_source_ids)) / 5.0
             END AS source_count_score,
             coalesce(toFloat(r.support_weight),
               CASE
                 WHEN coalesce(toFloat(r.source_count), 0.0) > 5.0 THEN 1.0
                 WHEN coalesce(toFloat(r.source_count), 0.0) > 0.0
                   THEN toFloat(r.source_count) / 5.0
                 WHEN toFloat(size(fact_source_ids)) > 5.0 THEN 1.0
                 ELSE toFloat(size(fact_source_ids)) / 5.0
               END
             ) AS support_weight_score,
             coalesce(toFloat(r.updated_at), 0.0) / 2000000000.0 AS recency_score,
             CASE
               WHEN coalesce(r[$structural_property], false) OR r.predicate = $hyper_role_predicate
                 THEN 1
               ELSE 0
             END AS structural_role,
             CASE
               WHEN coalesce(r[$structural_property], false) OR r.predicate = $hyper_role_predicate
                 THEN -2.0
               ELSE 0.0
             END AS structural_role_score
        WITH s, r, o, confidence_score, source_count_score, support_weight_score, recency_score,
             structural_role,
             source_match_score + source_vector_score * 2.0
             + term_match_score + confidence_score + support_weight_score
             + structural_role_score + coalesce(toFloat(index_score), 0.0)
             AS relevance_score
        WITH s, r, o, structural_role,
             CASE $ranking_policy
               WHEN 'relevance' THEN relevance_score
               WHEN 'latest' THEN recency_score
               ELSE relevance_score * 0.65
                    + recency_score * 0.20
                    + confidence_score * 0.10
                    + support_weight_score * 0.05
             END AS graph_score
        RETURN s.name AS subject,
               s.type AS subject_type,
               r.predicate AS predicate,
               o.name AS object,
               o.type AS object_type,
               r.evidence AS evidence,
               r[$hyper_event_property] AS hyper_event,
               r[$hyper_role_property] AS hyper_role,
               r[$chain_order_property] AS chain_order,
               r.confidence AS confidence,
               r.updated_at AS updated_at,
               structural_role AS structural_role,
               graph_score AS graph_score
        {single_hop_order_by}
        LIMIT $limit
        """
        multi_hop_scan_cypher = ""
        if depth <= 1:
            cypher = single_hop_cypher
        else:
            multi_hop_scan_seed_cypher = ""
            if fulltext_query:
                multi_hop_seed_cypher = f"""
        CALL () {{
          MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
          WHERE source_node.source_id IN $source_ids
          MATCH (s:Entity)-[source_r:FACT]->(o:Entity)
          WHERE source_r.fact_key = fact_node.fact_key
            AND coalesce(source_r[$status_property], 'active') = 'active'
          WITH [s, o] AS source_seed_nodes, source_node
          UNWIND source_seed_nodes AS seed
          WITH seed, source_node,
               reduce(source_vector_score = 0.0, source_score IN $source_score_rows |
                 CASE
                   WHEN source_score.source_id = source_node.source_id
                        AND coalesce(toFloat(source_score.score), 0.0) > source_vector_score
                     THEN coalesce(toFloat(source_score.score), 0.0)
                   ELSE source_vector_score
                 END
               ) AS source_vector_score
          RETURN seed, 3.0 + source_vector_score * 2.0 AS seed_score
          UNION
          UNWIND $entity_seed_rows AS entity_seed
          MATCH (seed:Entity)
          WHERE elementId(seed) = entity_seed.element_id
          RETURN seed, coalesce(toFloat(entity_seed.score), 0.0) AS seed_score
          UNION
          UNWIND $fact_seed_rows AS fact_seed
          MATCH ()-[r:FACT]-()
          WHERE elementId(r) = fact_seed.element_id
          WITH [startNode(r), endNode(r)] AS fact_seeds, fact_seed
          UNWIND fact_seeds AS seed
          WITH seed, fact_seed
          WHERE seed:Entity
          RETURN seed, coalesce(toFloat(fact_seed.score), 0.0) AS seed_score
        }}
        WITH seed, max(seed_score) AS seed_score
        WHERE size($cached_expansion_seed_ids) = 0
           OR NOT elementId(seed) IN $cached_expansion_seed_ids
        ORDER BY seed_score DESC
        LIMIT $live_seed_limit
        MATCH path = (seed)-[:FACT*1..{depth}]-(o:Entity)
        WITH path, relationships(path) AS rels, length(path) AS hop, seed_score
        WHERE hop > 1
                """
                multi_hop_scan_seed_cypher = f"""
        MATCH path = (s:Entity)-[:FACT*1..{depth}]->(o:Entity)
        WHERE
          (
            size($source_ids) > 0
            AND any(rel IN relationships(path) WHERE
              EXISTS {{
                MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
                WHERE source_node.source_id IN $source_ids
                  AND fact_node.fact_key = rel.fact_key
              }})
          )
          OR any(term_row IN $term_rows WHERE
              any(node IN nodes(path) WHERE toLower(node.name) CONTAINS term_row.term)
              OR any(rel IN relationships(path) WHERE
                toLower(rel.predicate) CONTAINS term_row.term
                OR toLower(coalesce(rel.evidence, '')) CONTAINS term_row.term
                OR toLower(coalesce(rel[$hyper_event_property], '')) CONTAINS term_row.term
                OR toLower(coalesce(rel[$hyper_role_property], '')) CONTAINS term_row.term
                OR toLower(coalesce(rel[$chain_id_property], '')) CONTAINS term_row.term
                OR any(chain_id IN coalesce(rel[$chain_ids_property], [])
                       WHERE toLower(toString(chain_id)) CONTAINS term_row.term)))
        WITH path, relationships(path) AS rels, length(path) AS hop, 0.0 AS seed_score
        WHERE hop > 1
                """
            elif terms and not source_ids:
                multi_hop_seed_cypher = ""
                live_seed_limit = 0
                multihop_live_seed_limit = 0
            else:
                multi_hop_seed_cypher = f"""
        CALL () {{
          MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
          WHERE source_node.source_id IN $source_ids
          MATCH (s:Entity)-[source_r:FACT]->(o:Entity)
          WHERE source_r.fact_key = fact_node.fact_key
            AND coalesce(source_r[$status_property], 'active') = 'active'
          WITH [s, o] AS source_seed_nodes, source_node
          UNWIND source_seed_nodes AS seed
          WITH seed, source_node,
               reduce(source_vector_score = 0.0, source_score IN $source_score_rows |
                 CASE
                   WHEN source_score.source_id = source_node.source_id
                        AND coalesce(toFloat(source_score.score), 0.0) > source_vector_score
                     THEN coalesce(toFloat(source_score.score), 0.0)
                   ELSE source_vector_score
                 END
               ) AS source_vector_score
          RETURN seed, 3.0 + source_vector_score * 2.0 AS seed_score
        }}
        WITH seed, max(seed_score) AS seed_score
        WHERE size($cached_expansion_seed_ids) = 0
           OR NOT elementId(seed) IN $cached_expansion_seed_ids
        ORDER BY seed_score DESC
        LIMIT $live_seed_limit
        MATCH path = (seed)-[:FACT*1..{depth}]-(o:Entity)
        WITH path, relationships(path) AS rels, length(path) AS hop, seed_score
        WHERE hop > 1
                """

            def build_multi_hop_cypher(seed_cypher: str) -> str:
                return f"""
        {seed_cypher}
        WITH rels, hop, seed_score
        WHERE all(rel IN rels WHERE coalesce(rel[$status_property], 'active') = 'active')
        WITH rels, hop, seed_score,
             reduce(path_term_score = 0.0, term_row IN $term_rows |
               path_term_score
               + CASE WHEN any(rel IN rels WHERE
                   toLower(coalesce(rel.predicate, '')) CONTAINS term_row.term
                   OR toLower(coalesce(rel.evidence, '')) CONTAINS term_row.term
                   OR toLower(coalesce(rel[$hyper_event_property], '')) CONTAINS term_row.term
                   OR toLower(coalesce(rel[$hyper_role_property], '')) CONTAINS term_row.term
                   OR toLower(coalesce(rel[$chain_id_property], '')) CONTAINS term_row.term
                   OR any(chain_id IN coalesce(rel[$chain_ids_property], [])
                          WHERE toLower(toString(chain_id)) CONTAINS term_row.term))
                 THEN 0.5 * coalesce(toFloat(term_row.weight), 1.0)
                 ELSE 0.0
                 END
             ) AS path_term_match_score,
             CASE
               WHEN size(rels) = 1 THEN false
               ELSE all(index IN range(0, size(rels) - 2) WHERE
                 any(left_chain_id IN coalesce(rels[index][$chain_ids_property], []) WHERE
                   left_chain_id IN coalesce(rels[index + 1][$chain_ids_property], [])))
             END AS chain_path,
             CASE
               WHEN size(rels) = 1
                 THEN size(coalesce(rels[0][$chain_order_keys_property], [])) > 0
               ELSE all(index IN range(0, size(rels) - 2) WHERE
                 any(left_key IN coalesce(rels[index][$chain_order_keys_property], []) WHERE
                   any(right_key IN coalesce(
                     rels[index + 1][$chain_order_keys_property],
                     []
                   ) WHERE
                     split(right_key, $chain_order_separator)[0]
                         = split(left_key, $chain_order_separator)[0]
                     AND toInteger(split(right_key, $chain_order_separator)[1])
                         = toInteger(split(left_key, $chain_order_separator)[1]) + 1)))
             END AS chain_order_path
        WITH rels, rels[size(rels) - 1] AS r, hop, chain_path, chain_order_path, seed_score,
             path_term_match_score
        WITH rels, startNode(r) AS s, r, endNode(r) AS o, hop, chain_path,
             chain_order_path, seed_score,
             path_term_match_score,
             CASE
               WHEN size(coalesce(r.source_ids, [])) > 0
                    AND coalesce(r.source_scope, '') IN ['exact', 'inferred']
                 THEN coalesce(r.source_ids, [])
               WHEN size(coalesce(r.retrieval_source_ids, [])) > 0
                 THEN coalesce(r.retrieval_source_ids, [])
               WHEN size(coalesce(r.batch_source_ids, [])) > 0
                 THEN coalesce(r.batch_source_ids, [])
               WHEN size(coalesce(r.source_ids, [])) > 0 THEN coalesce(r.source_ids, [])
               WHEN r.source_id IS NULL THEN []
               ELSE [r.source_id]
             END AS fact_source_ids
        WITH rels, s, r, o, hop, chain_path, chain_order_path, seed_score, fact_source_ids,
             path_term_match_score,
             CASE
               WHEN EXISTS {{
                 MATCH (source_node:GraphSource)-[support:SUPPORTS_FACT]->(fact_node:GraphFact)
                 WHERE source_node.source_id IN $source_ids
                   AND fact_node.fact_key = r.fact_key
                   AND coalesce(support.exact_source, false) = true
               }} THEN 3.0
               WHEN EXISTS {{
                 MATCH (source_node:GraphSource)-[support:SUPPORTS_FACT]->(fact_node:GraphFact)
                 WHERE source_node.source_id IN $source_ids
                   AND fact_node.fact_key = r.fact_key
               }} THEN 1.0
               ELSE 0.0
             END AS source_match_score,
             reduce(source_vector_score = 0.0, source_score IN $source_score_rows |
               CASE
                 WHEN source_score.source_id IN fact_source_ids
                      AND coalesce(toFloat(source_score.score), 0.0) > source_vector_score
                   THEN coalesce(toFloat(source_score.score), 0.0)
                 ELSE source_vector_score
               END
             ) AS source_vector_score,
             reduce(term_score = 0.0, term_row IN $term_rows |
               term_score
               + CASE
                   WHEN toLower(coalesce(startNode(r).name, '')) CONTAINS term_row.term
                     THEN 2.0 * coalesce(toFloat(term_row.weight), 1.0)
                   ELSE 0.0
                 END
               + CASE
                   WHEN toLower(coalesce(endNode(r).name, '')) CONTAINS term_row.term
                     THEN 2.0 * coalesce(toFloat(term_row.weight), 1.0)
                   ELSE 0.0
                 END
               + CASE WHEN toLower(coalesce(r.predicate, '')) CONTAINS term_row.term
                      THEN 1.0 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r.evidence, '')) CONTAINS term_row.term
                      THEN 0.5 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r[$hyper_event_property], '')) CONTAINS term_row.term
                      THEN 2.0 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r[$hyper_role_property], '')) CONTAINS term_row.term
                      THEN 1.0 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN toLower(coalesce(r[$chain_id_property], '')) CONTAINS term_row.term
                      THEN 0.5 * coalesce(toFloat(term_row.weight), 1.0) ELSE 0.0 END
               + CASE WHEN any(chain_id IN coalesce(r[$chain_ids_property], [])
                         WHERE toLower(toString(chain_id)) CONTAINS term_row.term)
                      THEN 0.5 * coalesce(toFloat(term_row.weight), 1.0)
                      ELSE 0.0
                 END
             ) AS term_match_score,
             coalesce(toFloat(r.confidence), 0.0) AS confidence_score,
             CASE
               WHEN coalesce(toFloat(r.source_count), 0.0) > 5.0 THEN 1.0
               WHEN coalesce(toFloat(r.source_count), 0.0) > 0.0
                 THEN toFloat(r.source_count) / 5.0
               WHEN toFloat(size(fact_source_ids)) > 5.0 THEN 1.0
               ELSE toFloat(size(fact_source_ids)) / 5.0
             END AS source_count_score,
             coalesce(toFloat(r.support_weight),
               CASE
                 WHEN coalesce(toFloat(r.source_count), 0.0) > 5.0 THEN 1.0
                 WHEN coalesce(toFloat(r.source_count), 0.0) > 0.0
                   THEN toFloat(r.source_count) / 5.0
                 WHEN toFloat(size(fact_source_ids)) > 5.0 THEN 1.0
                 ELSE toFloat(size(fact_source_ids)) / 5.0
               END
             ) AS support_weight_score,
             coalesce(toFloat(r.updated_at), 0.0) / 2000000000.0 AS recency_score,
             1.0 / toFloat(hop) AS hop_score,
             CASE WHEN chain_path THEN 1.5 ELSE 0.0 END AS chain_path_score,
             CASE WHEN chain_order_path THEN 1.0 ELSE 0.0 END AS chain_order_score,
             CASE
               WHEN coalesce(r[$structural_property], false) OR r.predicate = $hyper_role_predicate
                 THEN 1
               ELSE 0
             END AS structural_role,
             CASE
               WHEN coalesce(r[$structural_property], false) OR r.predicate = $hyper_role_predicate
                 THEN -2.0
               ELSE 0.0
             END AS structural_role_score
        WITH rels, s, r, o, hop, confidence_score, source_count_score, support_weight_score,
             recency_score, hop_score, chain_path_score, chain_order_score, structural_role,
             path_term_match_score,
             source_match_score + source_vector_score * 2.0
             + term_match_score + confidence_score
             + support_weight_score + hop_score + chain_path_score + chain_order_score
             + structural_role_score + path_term_match_score
             + coalesce(toFloat(seed_score), 0.0)
             AS relevance_score
        WITH rels, s, r, o, hop, chain_path_score, chain_order_score, structural_role,
             CASE $ranking_policy
               WHEN 'relevance' THEN relevance_score
               WHEN 'latest' THEN recency_score
               ELSE relevance_score * 0.65
                    + recency_score * 0.20
                    + confidence_score * 0.10
                    + support_weight_score * 0.05
                    + hop_score * 0.10
                    + chain_path_score * 0.10
                    + chain_order_score * 0.05
             END AS graph_score
        {multi_hop_path_order_by}
        LIMIT $limit
        UNWIND range(0, size(rels) - 1) AS rel_index
        WITH rels[rel_index] AS r, rel_index + 1 AS hop, graph_score, structural_role
        WITH r, hop, max(graph_score) AS graph_score,
             min(structural_role) AS structural_role
        RETURN startNode(r).name AS subject,
               startNode(r).type AS subject_type,
               r.predicate AS predicate,
               endNode(r).name AS object,
               endNode(r).type AS object_type,
               r.evidence AS evidence,
               r[$hyper_event_property] AS hyper_event,
               r[$hyper_role_property] AS hyper_role,
               r[$chain_order_property] AS chain_order,
               r.confidence AS confidence,
               r.updated_at AS updated_at,
               structural_role AS structural_role,
               graph_score AS graph_score,
               hop AS hop
        {multi_hop_edge_order_by}
        """

            cached_multi_hop_seed_cypher = """
        UNWIND $cached_expansion_paths AS cached_path
        WITH cached_path,
             reduce(seed_score = 0.0, seed_row IN $cached_expansion_seed_rows |
               CASE
                 WHEN seed_row.element_id = cached_path.seed_element_id
                   THEN coalesce(toFloat(seed_row.seed_score), 0.0)
                 ELSE seed_score
               END
             ) AS seed_score
        WHERE size(coalesce(cached_path.rel_ids, [])) > 1
        UNWIND cached_path.rel_ids AS rel_ref
        MATCH ()-[rel:FACT]->()
        WHERE (
            (rel_ref.element_id IS NOT NULL AND elementId(rel) = rel_ref.element_id)
            OR (rel_ref.fact_key IS NOT NULL AND rel.fact_key = rel_ref.fact_key)
          )
          AND coalesce(rel[$status_property], 'active') = 'active'
        WITH cached_path.path_key AS path_key,
             coalesce(toInteger(cached_path.hop), size(cached_path.rel_ids)) AS hop,
             seed_score,
             coalesce(toInteger(rel_ref.rel_index), 0) AS rel_index,
             rel
        ORDER BY path_key ASC, rel_index ASC
        WITH path_key, hop, seed_score, collect(rel) AS rels
        WHERE hop > 1 AND size(rels) = hop
                    """

            def build_cached_plus_live_multi_hop_seed_cypher(live_seed_cypher: str) -> str:
                return f"""
        CALL () {{
          {cached_multi_hop_seed_cypher}
          RETURN rels, hop, seed_score
          UNION
          {live_seed_cypher}
          RETURN rels, hop, seed_score
        }}
                """

            if (fulltext_query or not terms) and multi_hop_expansion_cache_enabled:
                expansion_cache_seed_probe_limit = min(
                    seed_limit,
                    expansion_cache_preload_seed_limit + 1,
                )
                cached_expansion_seed_rows = self._multi_hop_seed_rows(
                    source_ids=source_ids or [],
                    source_score_rows=source_score_rows,
                    entity_seed_rows=entity_seed_rows,
                    fact_seed_rows=fact_seed_rows,
                    seed_limit=expansion_cache_seed_probe_limit,
                )
                multihop_seed_count = len(cached_expansion_seed_rows)
                if cached_expansion_seed_rows:
                    cacheable_seed_rows = (
                        cached_expansion_seed_rows[:expansion_cache_preload_seed_limit]
                        if expansion_cache_preload_seed_limit > 0
                        else []
                    )
                    cacheable_seed_ids = [str(row["element_id"]) for row in cacheable_seed_rows]
                    seed_overflow = len(cached_expansion_seed_rows) > len(cacheable_seed_rows)
                else:
                    cacheable_seed_rows = []
                    cacheable_seed_ids = []
                    seed_overflow = False
                if cacheable_seed_rows:
                    cached_seed_loaded_count = 0
                    hot_paths, hot_complete, hot_stats = self._cached_multi_hop_expansion_paths(
                        seed_element_ids=cacheable_seed_ids,
                        depth=depth,
                        path_limit=expansion_cache_path_limit,
                        load_misses=False,
                        use_persistent_cache=use_persistent_multi_hop_expansion_cache,
                    )
                    multihop_persistent_cache_hit_count += hot_stats["persistent_hit_count"]
                    candidate_cached_expansion_paths = hot_paths
                    hot_complete_seed_ids = set(hot_stats.get("complete_seed_ids", []))
                    candidate_cached_seed_ids = [
                        seed_element_id
                        for seed_element_id in cacheable_seed_ids
                        if seed_element_id in hot_complete_seed_ids
                    ]
                    if hot_complete:
                        candidate_cached_seed_ids = cacheable_seed_ids
                    else:
                        preload_path_limit = min(
                            expansion_cache_path_limit,
                            max(
                                1,
                                expansion_cache_preload_path_limit // len(cacheable_seed_rows),
                            ),
                        )
                        cached_expansion_paths, expansion_cache_complete, load_stats = (
                            self._cached_multi_hop_expansion_paths(
                                seed_element_ids=cacheable_seed_ids,
                                depth=depth,
                                path_limit=preload_path_limit,
                                load_misses=True,
                                use_persistent_cache=use_persistent_multi_hop_expansion_cache,
                                alias_path_limits=[expansion_cache_path_limit],
                            )
                        )
                        multihop_persistent_cache_hit_count += load_stats["persistent_hit_count"]
                        cached_seed_loaded_count = int(load_stats["loaded_count"])
                        candidate_cached_expansion_paths = cached_expansion_paths
                        loaded_complete_seed_ids = set(load_stats.get("complete_seed_ids", []))
                        candidate_cached_seed_ids = [
                            seed_element_id
                            for seed_element_id in cacheable_seed_ids
                            if seed_element_id in loaded_complete_seed_ids
                        ]
                        if expansion_cache_complete:
                            candidate_cached_seed_ids = cacheable_seed_ids

                    if candidate_cached_seed_ids:
                        cached_expansion_seed_ids = candidate_cached_seed_ids
                        cached_expansion_seed_rows = _seed_rows_for_element_ids(
                            cacheable_seed_rows,
                            cached_expansion_seed_ids,
                        )
                        cached_expansion_paths = candidate_cached_expansion_paths
                        partial_cached_seed_window = len(cached_expansion_seed_ids) < len(
                            cacheable_seed_ids
                        )
                        if seed_overflow or partial_cached_seed_window:
                            multi_hop_seed_cypher = build_cached_plus_live_multi_hop_seed_cypher(
                                multi_hop_seed_cypher
                            )
                        else:
                            multi_hop_seed_cypher = cached_multi_hop_seed_cypher
                            multihop_cache_hit = cached_seed_loaded_count == 0

                if cached_expansion_seed_ids:
                    if seed_overflow:
                        live_seed_limit = max(0, seed_limit - len(cached_expansion_seed_ids))
                    elif len(cached_expansion_seed_ids) < len(cacheable_seed_rows):
                        live_seed_limit = max(
                            0,
                            len(cacheable_seed_rows) - len(cached_expansion_seed_ids),
                        )
                    else:
                        live_seed_limit = 0
                    multihop_cached_seed_count = len(cached_expansion_seed_ids)
                    multihop_partial_cache_hit = bool(
                        seed_overflow or len(cached_expansion_seed_ids) < len(cacheable_seed_rows)
                    )
                multihop_live_seed_limit = live_seed_limit

            cypher = build_multi_hop_cypher(multi_hop_seed_cypher) if multi_hop_seed_cypher else ""
            if multi_hop_scan_seed_cypher:
                multi_hop_scan_cypher = build_multi_hop_cypher(multi_hop_scan_seed_cypher)

        def run_session_query(session: Any, query: str, *, limit: int) -> list[Any]:
            return list(
                session.run(
                    query,
                    source_ids=source_ids or [],
                    source_score_rows=source_score_rows,
                    entity_seed_rows=entity_seed_rows,
                    fact_seed_rows=fact_seed_rows,
                    cached_expansion_paths=cached_expansion_paths,
                    cached_expansion_seed_rows=cached_expansion_seed_rows,
                    cached_expansion_seed_ids=cached_expansion_seed_ids,
                    terms=terms,
                    term_rows=term_rows,
                    fulltext_query=fulltext_query,
                    limit=limit,
                    seed_limit=seed_limit,
                    live_seed_limit=live_seed_limit,
                    ranking_policy=policy,
                    entity_text_index=_ENTITY_FULLTEXT_INDEX,
                    fact_text_index=_FACT_FULLTEXT_INDEX,
                    chain_order_separator=CHAIN_ORDER_KEY_SEPARATOR,
                    hyper_role_predicate=HYPER_ROLE_PREDICATE,
                    chain_id_property="chain_id",
                    chain_ids_property="chain_ids",
                    chain_order_property="chain_order",
                    chain_order_keys_property="chain_order_keys",
                    hyper_event_property="hyper_event",
                    hyper_role_property="hyper_role",
                    status_property=_FACT_STATUS_PROPERTY,
                    structural_property="structural",
                    timeout=GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
                )
            )

        def run_context_query_once(query: str, *, limit: int) -> list[Any]:
            with self._session() as session:
                return run_session_query(session, query, limit=limit)

        def run_context_query(query: str, *, limit: int) -> list[Any]:
            return self._run_with_reconnect(
                lambda session: run_session_query(session, query, limit=limit)
            )

        def run_timed_context_query_once(query: str, *, limit: int) -> tuple[list[Any], float]:
            query_started_at = time.perf_counter()
            rows = run_context_query_once(query, limit=limit)
            return rows, (time.perf_counter() - query_started_at) * 1000

        def run_parallel_context_queries(
            queries: list[tuple[str, int]],
        ) -> list[tuple[list[Any], float]]:
            results: list[tuple[list[Any], float] | None] = [None] * len(queries)
            errors: list[Exception] = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as pool:
                futures = [
                    pool.submit(run_timed_context_query_once, query, limit=limit)
                    for query, limit in queries
                ]
                for index, future in enumerate(futures):
                    try:
                        results[index] = future.result()
                    except Exception as e:
                        errors.append(e)
            if errors:
                first_error = errors[0]
                if _is_retryable_neo4j_connection_error(first_error):
                    logger.warning(
                        "Neo4j graph parallel retrieval connection lost; "
                        "reconnecting and retrying serially once: %s",
                        first_error,
                    )
                    self.close()
                    self.initialize()
                    return [
                        run_timed_context_query_once(query, limit=limit) for query, limit in queries
                    ]
                raise first_error
            return [result or ([], 0.0) for result in results]

        if depth > 1 and cypher:
            used_scan_fallback = False
            parallel_results = run_parallel_context_queries(
                [
                    (single_hop_cypher, query_limit),
                    (cypher, expansion_query_limit),
                ]
            )
            (single_hop_rows, single_hop_ms), (multi_hop_rows, multi_hop_ms) = parallel_results
            _set_timing(timings, "graph_single_hop_ms", single_hop_ms)
            _set_timing(timings, "graph_multi_hop_ms", multi_hop_ms)
            rows = [*single_hop_rows, *multi_hop_rows]
            if multi_hop_scan_cypher and len(rows) < query_limit:
                used_scan_fallback = True
                scan_fallback_started_at = time.perf_counter()
                rows.extend(run_context_query(multi_hop_scan_cypher, limit=expansion_query_limit))
                _record_timing(timings, "graph_scan_fallback_ms", scan_fallback_started_at)
            else:
                _set_timing(timings, "graph_scan_fallback_ms", 0.0)
        elif depth > 1:
            used_scan_fallback = False
            single_hop_started_at = time.perf_counter()
            rows = run_context_query(single_hop_cypher, limit=query_limit)
            _record_timing(timings, "graph_single_hop_ms", single_hop_started_at)
            _set_timing(timings, "graph_multi_hop_ms", 0.0)
            _set_timing(timings, "graph_scan_fallback_ms", 0.0)
        else:
            used_scan_fallback = False
            single_hop_started_at = time.perf_counter()
            rows = run_context_query(cypher, limit=query_limit)
            _record_timing(timings, "graph_single_hop_ms", single_hop_started_at)
            _set_timing(timings, "graph_multi_hop_ms", 0.0)
            _set_timing(timings, "graph_scan_fallback_ms", 0.0)
        format_started_at = time.perf_counter()
        entries: list[dict[str, Any]] = []
        seen_positions = set()
        for row in _rank_retrieved_rows(rows):
            subject = _canonical_retrieved_entity_name(row.get("subject"))
            subject_type = str(row.get("subject_type") or "").strip()
            predicate = str(row.get("predicate") or "").strip()
            obj = _canonical_retrieved_entity_name(row.get("object"))
            object_type = str(row.get("object_type") or "").strip()
            if not subject or not predicate or not obj:
                continue
            key = (
                subject.lower(),
                subject_type.lower() if include_entity_types else "",
                predicate.lower(),
                obj.lower(),
                object_type.lower() if include_entity_types else "",
            )
            evidence = str(row.get("evidence") or "").strip()
            detail = (
                f"{_context_entity_label(subject, subject_type, include_entity_types)} "
                f"-[{predicate}]-> "
                f"{_context_entity_label(obj, object_type, include_entity_types)}"
            )
            hop = 1
            if depth > 1:
                hop = _retrieval_depth(row.get("hop", 1))
                detail = f"[{hop}-hop] {detail}"
            position_key = (*key, hop)
            if position_key in seen_positions:
                continue
            seen_positions.add(position_key)
            notes = []
            hyper_event = str(row.get("hyper_event") or "").strip()
            hyper_role = str(row.get("hyper_role") or "").strip()
            chain_order = row.get("chain_order")
            if hyper_event:
                notes.append(f"event: {hyper_event}")
            if hyper_role:
                notes.append(f"role: {hyper_role}")
            if chain_order is not None and chain_order != "":
                notes.append(f"chain_order: {chain_order}")
            if evidence:
                notes.append(evidence)
            if notes:
                detail += f" ({'; '.join(notes)})"
            entries.append(
                {
                    "dedupe_key": key,
                    "detail": detail,
                    "hop": hop,
                    "evidence_length": len(evidence),
                    "subject_key": _entity_alias_key(subject),
                    "object_key": _entity_alias_key(obj),
                }
            )
        lines = _format_retrieved_fact_lines(entries, depth=depth, limit=query_limit)
        _record_timing(timings, "graph_format_ms", format_started_at)
        _record_count(timings, "graph_rows", len(rows))
        _record_count(timings, "graph_returned_facts", len(lines))
        _record_count(timings, "graph_multihop_seed_count", multihop_seed_count)
        _set_bool(timings, "graph_used_fulltext", bool(fulltext_query))
        _set_bool(timings, "graph_used_scan_fallback", used_scan_fallback)
        _set_bool(timings, "graph_multihop_cache_hit", multihop_cache_hit)
        _record_count(
            timings,
            "graph_multihop_cached_seed_count",
            multihop_cached_seed_count,
        )
        _record_count(
            timings,
            "graph_multihop_live_seed_limit",
            multihop_live_seed_limit,
        )
        _set_bool(
            timings,
            "graph_multihop_partial_cache_hit",
            multihop_partial_cache_hit,
        )
        _record_count(
            timings,
            "graph_multihop_persistent_cache_hit_count",
            multihop_persistent_cache_hit_count,
        )
        _set_bool(timings, "graph_multihop_degraded", multihop_degraded)
        _record_timing(timings, "graph_total_ms", started_at)
        logger.info(
            "Neo4j graph retrieval done: elapsed_ms=%.1f depth=%d max_facts=%d "
            "source_ids_count=%d row_count=%d returned_facts=%d used_fulltext=%s "
            "used_scan_fallback=%s ranking_policy=%s graph_multihop_seed_count=%d "
            "graph_multihop_cache_hit=%s graph_multihop_cached_seed_count=%d "
            "graph_multihop_live_seed_limit=%d graph_multihop_partial_cache_hit=%s "
            "graph_multihop_persistent_cache_hit_count=%d",
            (time.perf_counter() - started_at) * 1000,
            depth,
            query_limit,
            len(source_ids or []),
            len(rows),
            len(lines),
            bool(fulltext_query),
            used_scan_fallback,
            policy,
            multihop_seed_count,
            multihop_cache_hit,
            multihop_cached_seed_count,
            multihop_live_seed_limit,
            multihop_partial_cache_hit,
            multihop_persistent_cache_hit_count,
        )
        context = format_graph_context(lines)
        self._retrieval_cache.set("final_context", final_cache_key, context)
        return context

    def _session(self):
        if self.driver is None:
            raise RuntimeError("Neo4j graph client is not initialized")
        database = str(self.config.get("database") or "neo4j")
        return self.driver.session(database=database)

    def _run_with_reconnect(self, operation: Callable[..., T]) -> T:
        try:
            with self._session() as session:
                return operation(session)
        except Exception as e:
            if not _is_retryable_neo4j_connection_error(e):
                raise
            logger.warning("Neo4j graph connection lost; reconnecting and retrying once: %s", e)
            self.close()
            self.initialize()
            with self._session() as session:
                return operation(session)


_neo4j_notification_warning_hooks_installed = False
_original_showwarning = warnings.showwarning


def _compact_neo4j_warning_message(message: Any) -> str | None:
    notification = getattr(message, "notification", None)
    if notification is None:
        return None
    status = str(getattr(notification, "gql_status", "") or "").strip()
    description = str(getattr(notification, "status_description", "") or "").strip()
    if not status and not description:
        return None
    if status and description:
        return f"Neo4j {status}: {description}"
    return f"Neo4j {status or description}"


def _show_neo4j_notification_warning(
    message: Any,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: Any | None = None,
    line: str | None = None,
) -> None:
    try:
        from neo4j.warnings import Neo4jWarning
    except ImportError:
        Neo4jWarning = ()  # type: ignore[misc, assignment]

    if isinstance(message, Neo4jWarning):
        compact = _compact_neo4j_warning_message(message)
        if compact is not None:
            stream = sys.stderr if file is None else file
            stream.write(warnings.formatwarning(compact, category, filename, lineno, line))
            return
    _original_showwarning(message, category, filename, lineno, file=file, line=line)


def _configure_neo4j_notification_warnings() -> None:
    """Surface DBMS notifications as compact Python warnings.

    The driver logs full GqlStatusObject dumps (including the entire Cypher
    query) to ``neo4j.notifications``. Keep the notifications themselves, but
    mute that verbose logger and reformat ``Neo4jWarning`` output.
    """
    global _neo4j_notification_warning_hooks_installed, _original_showwarning
    if _neo4j_notification_warning_hooks_installed:
        return
    _neo4j_notification_warning_hooks_installed = True
    _original_showwarning = warnings.showwarning
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    warnings.showwarning = _show_neo4j_notification_warning


def _default_driver_factory(uri: str, auth: tuple[str, str]):
    try:
        from neo4j import GraphDatabase, NotificationMinimumSeverity
    except ImportError as e:
        raise RuntimeError("neo4j package is required for graph knowledge") from e
    _configure_neo4j_notification_warnings()
    # Route server notifications through Python's warnings module instead of
    # the driver's raw ``Received notification from DBMS server: ... for query``
    # logger dump (which reprints the full Cypher text).
    return GraphDatabase.driver(
        uri,
        auth=auth,
        warn_notification_severity=NotificationMinimumSeverity.WARNING,
    )


def _result_seed_element_ids(result: list[Any]) -> list[str]:
    seed_element_ids: list[str] = []
    for row in result:
        raw_values = _row_value(row, "seed_element_ids", []) or []
        if not isinstance(raw_values, list):
            continue
        seed_element_ids.extend(str(value or "").strip() for value in raw_values)
    return _unique_text_values(seed_element_ids)


def _seed_identity(name_key: Any, type_key: Any) -> dict[str, str] | None:
    normalized_name_key = str(name_key or "").strip()
    normalized_type_key = str(type_key or "").strip()
    if not normalized_name_key or not normalized_type_key:
        return None
    return {"name_key": normalized_name_key, "type_key": normalized_type_key}


def _seed_identity_from_value(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return _seed_identity(value.get("name_key"), value.get("type_key"))


def _persistent_multi_hop_expansion_paths(paths: Any) -> list[dict[str, Any]] | None:
    if not isinstance(paths, list):
        return None
    normalized_paths = _normalized_multi_hop_expansion_paths(paths)
    if len(normalized_paths) != len(paths):
        return None
    persistent_paths: list[dict[str, Any]] = []
    for path in normalized_paths:
        rel_refs: list[dict[str, Any]] = []
        for rel_ref in path.get("rel_ids", []):
            fact_key = str(_row_value(rel_ref, "fact_key") or "").strip()
            if not fact_key:
                return None
            rel_refs.append(
                {
                    "fact_key": fact_key,
                    "rel_index": _row_int(rel_ref, "rel_index", len(rel_refs)),
                }
            )
        if len(rel_refs) != int(path.get("hop", 0)):
            return None
        persistent_paths.append({"hop": path["hop"], "rel_ids": rel_refs})
    return persistent_paths


def _persistent_multi_hop_expansion_cache_key(
    *,
    revision: int,
    seed_identity: dict[str, str],
    depth: int,
    path_limit: int,
) -> str:
    key = (
        _PERSISTENT_MULTI_HOP_EXPANSION_CACHE_VERSION,
        int(revision),
        str(seed_identity["name_key"]),
        str(seed_identity["type_key"]),
        int(depth),
        "variable_length",
        ("FACT",),
        "undirected",
        int(path_limit),
    )
    return json.dumps(key, ensure_ascii=False, separators=(",", ":"))


def _fulltext_index_name(statement: str) -> str:
    if f"CREATE FULLTEXT INDEX {_ENTITY_FULLTEXT_INDEX}" in statement:
        return _ENTITY_FULLTEXT_INDEX
    if f"CREATE FULLTEXT INDEX {_FACT_FULLTEXT_INDEX}" in statement:
        return _FACT_FULLTEXT_INDEX
    return "unknown"


def _is_retryable_neo4j_connection_error(exc: Exception) -> bool:
    current: BaseException | None = exc
    while current is not None:
        name = current.__class__.__name__.lower()
        text = str(current).lower()
        if name in {"serviceunavailable", "sessionexpired"}:
            return True
        if any(
            marker in text
            for marker in (
                "defunct connection",
                "failed to read",
                "failed to write",
                "connectionabortederror",
                "connection aborted",
                "connection reset",
                "closed connection",
                "broken pipe",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
