"""Neo4j persistence and retrieval for graph knowledge facts."""

from __future__ import annotations

import concurrent.futures
import json
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar, cast

from core import logger
from core.knowledge.graph_constants import (
    ASSISTANT_CANONICAL_NAME,
    ASSISTANT_ENTITY_ALIAS_KEYS,
    CHAIN_ORDER_KEY_SEPARATOR,
    GRAPH_CYPHER_QUERY_TIMEOUT_SECONDS,
    GRAPH_EXPANSION_CANDIDATE_MAX_LIMIT,
    GRAPH_QUERY_ENUMERATION_TERMS,
    GRAPH_RETRIEVAL_DEFAULT_DEPTH,
    GRAPH_RETRIEVAL_MAX_DEPTH,
    HYPER_ROLE_PREDICATE,
    format_graph_context,
)

DriverFactory = Callable[[str, tuple[str, str]], Any]
_MAX_QUERY_TERMS = 32
_DEFAULT_MULTIHOP_EXPANSION_LIMIT = 40
_VALID_RANKING_POLICIES = {"hybrid", "relevance", "latest"}
_ENTITY_FULLTEXT_INDEX = "entity_text"
_FACT_FULLTEXT_INDEX = "fact_text"
_GRAPH_CACHE_TTL_SECONDS = 600.0
_MULTI_HOP_EXPANSION_CACHE_VERSION = "multi_hop_expansion:v1"
_MULTI_HOP_EXPANSION_CACHE_TRAVERSAL_MODE = "variable_length"
_MULTI_HOP_EXPANSION_CACHE_DIRECTION = "undirected"
_MULTI_HOP_EXPANSION_CACHE_RELATION_TYPES = ("FACT",)
_DEFAULT_MULTIHOP_EXPANSION_CACHE_PATH_LIMIT = GRAPH_EXPANSION_CANDIDATE_MAX_LIMIT
_DEFAULT_MULTIHOP_EXPANSION_CACHE_PRELOAD_SEED_LIMIT = 4
_DEFAULT_MULTIHOP_EXPANSION_CACHE_PRELOAD_PATH_LIMIT = 200
_GRAPH_CACHE_LIMITS = {
    "final_context": 256,
    "fulltext_seed": 512,
    "multi_hop_expansion": 512,
}
_QUERY_RELATION_EXPANSIONS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("根本原因", "导致", "造成", "引起", "触发", "原因", "为什么", "为何"),
        ("caused_by", "causes", "triggered_by", "leads_to", "root_cause", "trigger"),
    ),
    (
        ("影响", "依赖", "关联"),
        ("affects", "impacts", "depends_on", "related_to"),
    ),
    (
        ("负责", "负责人", "归谁", "谁管"),
        ("owner", "responsible_for", "has_owner"),
    ),
    (
        ("属于", "归属", "隶属", "组成", "部分"),
        ("belongs_to", "part_of", "member_of"),
    ),
    (
        ("使用", "用到", "基于"),
        ("uses", "built_with"),
    ),
    (
        GRAPH_QUERY_ENUMERATION_TERMS,
        (
            "count",
            "contains",
            "has",
            "includes",
            "member_of",
            "part_of",
            "has_attribute",
            "related_to",
        ),
    ),
)
_CJK_LEADING_QUERY_FILLERS = (
    "有哪些",
    "有多少",
    "多少个",
    "分别",
    "各自",
    "每个",
    "逐个",
    "列出",
    "哪些",
    "哪个",
    "什么",
    "几个",
    "数量",
    "总共",
    "一共",
    "请",
    "帮我",
    "了",
    "的",
    "是",
    "谁",
)
_CJK_TRAILING_QUERY_FILLERS = (
    "是什么原因",
    "有哪些",
    "是什么",
    "分别",
    "各自",
    "哪些",
    "什么",
    "原因",
    "吗",
    "呢",
    "么",
    "了",
    "的",
)
_QUERY_TRIGGER_NEGATION_PREFIXES = (
    "没有",
    "无需",
    "不是",
    "并非",
    "并不",
    "不再",
    "不",
    "非",
    "未",
    "无",
    "没",
)
T = TypeVar("T")


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

    def _bump_graph_revision(self) -> None:
        self._graph_revision += 1
        self._retrieval_cache.clear()

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
                  {limit: $seed_limit}
                )
                YIELD relationship AS r, score
                RETURN elementId(r) AS element_id, score
                """,
                fact_text_index=_FACT_FULLTEXT_INDEX,
                fulltext_query=fulltext_query,
                seed_limit=seed_limit,
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
          MATCH (fact_node)-[:FACT_SUBJECT|FACT_OBJECT]->(seed:Entity)
          WITH seed,
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
    ) -> tuple[list[dict[str, Any]], bool]:
        normalized_seed_ids = _unique_text_values(seed_element_ids)
        if not normalized_seed_ids:
            return [], True

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

        if missed_seed_ids and load_misses:
            loaded_by_seed = self._load_multi_hop_expansion_paths(
                seed_element_ids=missed_seed_ids,
                depth=depth,
                path_limit=path_limit,
            )
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

        expansion_rows: list[dict[str, Any]] = []
        complete = not missed_seed_ids or load_misses
        for seed_element_id in normalized_seed_ids:
            value = cached_by_seed.get(seed_element_id) or {"complete": True, "paths": []}
            if not bool(value.get("complete", True)):
                complete = False
                continue
            paths = value.get("paths")
            if not isinstance(paths, list):
                continue
            for path_index, path in enumerate(paths):
                cached_path = _multi_hop_expansion_cache_path_row(
                    seed_element_id,
                    path_index,
                    path,
                )
                if cached_path is not None:
                    expansion_rows.append(cached_path)
        return expansion_rows, complete

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
          WITH rels, hop
          LIMIT $path_limit_plus_one
          RETURN collect({{
            hop: hop,
            rel_ids: [rel_index IN range(0, size(rels) - 1) |
              {{element_id: elementId(rels[rel_index]), rel_index: rel_index}}]
          }}) AS paths
        }}
        RETURN seed_element_id, paths, size(paths) > $path_limit AS incomplete
        """
        rows = self._run_with_reconnect(
            lambda session: list(
                session.run(
                    query,
                    seed_element_ids=seed_element_ids,
                    path_limit=path_limit,
                    path_limit_plus_one=path_limit + 1,
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
            loaded[seed_element_id] = {"complete": not incomplete, "paths": paths}
        return loaded

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
            "CREATE INDEX fact_source_id IF NOT EXISTS FOR ()-[r:FACT]-() ON (r.source_id)",
            "CREATE INDEX fact_updated_at IF NOT EXISTS FOR ()-[r:FACT]-() ON (r.updated_at)",
            "CREATE INDEX fact_predicate IF NOT EXISTS FOR ()-[r:FACT]-() ON (r.predicate)",
            "CREATE INDEX entity_name_key IF NOT EXISTS FOR (e:Entity) ON (e.name_key)",
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
                   WHEN size(coalesce(r.source_ids, [])) > 0 THEN coalesce(r.source_ids, [])
                   WHEN r.source_id IS NULL THEN []
                   ELSE [r.source_id]
                 END AS source_ids
            UNWIND source_ids AS source_id
            WITH DISTINCT fact_node, trim(toString(source_id)) AS source_id
            WHERE source_id <> ''
            MERGE (source_node:GraphSource {source_id: source_id})
            MERGE (source_node)-[:SUPPORTS_FACT]->(fact_node)
            """,
            "CREATE FULLTEXT INDEX entity_text IF NOT EXISTS "
            "FOR (e:Entity) ON EACH [e.name, e.name_key, e.type, e.type_key]",
            "CREATE FULLTEXT INDEX fact_text IF NOT EXISTS "
            "FOR ()-[r:FACT]-() ON EACH "
            "[r.predicate, r.evidence, r.hyper_event, r.hyper_role, "
            "r.chain_id, r.chain_ids_text]",
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
        self._constraints_ready = True
        self._fulltext_indexes_ready = fulltext_ready
        self._fulltext_index_unavailable_reason = (
            None if fulltext_ready else "; ".join(fulltext_unavailable_reasons)
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
            row["metadata_json"] = json.dumps(row.get("metadata") or {}, ensure_ascii=False)
            rows.append(row)
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
        WITH s, o, r, fact,
             coalesce(r.source_ids, []) + coalesce(fact.source_ids, [fact.source_id])
             AS raw_source_ids
        WITH s, o, r, fact,
             reduce(source_ids = [], source_id IN raw_source_ids |
                  CASE
                    WHEN source_id IN source_ids THEN source_ids
                    ELSE source_ids + [source_id]
                  END) AS source_ids,
             coalesce(r.chain_ids, []) + coalesce(fact.chain_ids, []) AS raw_chain_ids
        WITH s, o, r, fact, source_ids,
             reduce(chain_ids = [], chain_id IN raw_chain_ids |
                  CASE
                    WHEN chain_id IN chain_ids THEN chain_ids
                    ELSE chain_ids + [chain_id]
                  END) AS chain_ids,
             coalesce(r.chain_order_keys, []) + coalesce(fact.chain_order_keys, [])
             AS raw_chain_order_keys
        WITH s, o, r, fact, source_ids, chain_ids,
             reduce(chain_order_keys = [], chain_order_key IN raw_chain_order_keys |
                  CASE
                    WHEN chain_order_key IN chain_order_keys THEN chain_order_keys
                    ELSE chain_order_keys + [chain_order_key]
                  END) AS chain_order_keys
          SET r.predicate = fact.predicate,
              r.source_id = fact.source_id,
              r.source_ids = source_ids,
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
              r.updated_at = fact.now,
              r.source_count = size(source_ids)
        MERGE (fact_node:GraphFact {fact_key: fact.fact_key})
          ON CREATE SET fact_node.created_at = fact.now
        SET fact_node.updated_at = fact.now,
            fact_node.predicate = fact.predicate
        MERGE (fact_node)-[:FACT_SUBJECT]->(s)
        MERGE (fact_node)-[:FACT_OBJECT]->(o)
        WITH r, fact_node, source_ids
        CALL (fact_node, source_ids) {
          UNWIND source_ids AS source_id
          WITH DISTINCT fact_node, trim(toString(source_id)) AS source_id
          WHERE source_id <> ''
          MERGE (source_node:GraphSource {source_id: source_id})
          MERGE (source_node)-[:SUPPORTS_FACT]->(fact_node)
          RETURN count(*) AS linked_source_count
        }
        RETURN count(DISTINCT r) AS count
        """
        result = self._run_with_reconnect(
            lambda session: list(
                session.run(
                    query,
                    facts=rows,
                    chain_order_separator=CHAIN_ORDER_KEY_SEPARATOR,
                )
            )
        )
        count = _result_count(result, len(rows))
        if count > 0:
            self._bump_graph_revision()
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
        term_rows = _query_term_rows(query)
        terms = [str(row["term"]) for row in term_rows]
        if not terms and not source_ids:
            _record_timing(timings, "graph_total_ms", started_at)
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
        seed_limit = max(query_limit, expansion_query_limit)
        fulltext_query = _fulltext_query(terms) if self._fulltext_indexes_ready else ""
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
            return str(cached_context)
        _set_bool(timings, "graph_cache_hit", False)
        fulltext_seed_rows = self._cached_fulltext_seed_rows(fulltext_query, seed_limit)
        entity_seed_rows = fulltext_seed_rows["entity_seed_rows"]
        fact_seed_rows = fulltext_seed_rows["fact_seed_rows"]
        cached_expansion_paths: list[dict[str, Any]] = []
        cached_expansion_seed_rows: list[dict[str, Any]] = []
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
        WITH s, r, o, index_score,
             CASE
               WHEN size(coalesce(r.source_ids, [])) > 0 THEN coalesce(r.source_ids, [])
               WHEN r.source_id IS NULL THEN []
               ELSE [r.source_id]
             END AS fact_source_ids
        WITH s, r, o, index_score, fact_source_ids,
             CASE WHEN EXISTS {{
               MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
               WHERE source_node.source_id IN $source_ids
                 AND fact_node.fact_key = r.fact_key
             }} THEN 3.0 ELSE 0.0 END AS source_match_score,
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
               WHEN coalesce(
                 toFloat(r.source_count),
                 toFloat(size(coalesce(r.source_ids, []))),
                 0.0
               ) > 5.0 THEN 1.0
               ELSE coalesce(
                 toFloat(r.source_count),
                 toFloat(size(coalesce(r.source_ids, []))),
                 0.0
               ) / 5.0
             END AS source_count_score,
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
        WITH s, r, o, confidence_score, source_count_score, recency_score,
             structural_role,
             source_match_score + source_vector_score * 2.0
             + term_match_score + confidence_score + source_count_score
             + structural_role_score + coalesce(toFloat(index_score), 0.0)
             AS relevance_score
        WITH s, r, o, structural_role,
             CASE $ranking_policy
               WHEN 'relevance' THEN relevance_score
               WHEN 'latest' THEN recency_score
               ELSE relevance_score * 0.65
                    + recency_score * 0.20
                    + confidence_score * 0.10
                    + source_count_score * 0.05
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
          MATCH (fact_node)-[:FACT_SUBJECT|FACT_OBJECT]->(seed:Entity)
          WITH seed,
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
        ORDER BY seed_score DESC
        LIMIT $seed_limit
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
            elif terms:
                multi_hop_seed_cypher = f"""
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
            else:
                multi_hop_seed_cypher = f"""
        CALL () {{
          MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
          WHERE source_node.source_id IN $source_ids
          MATCH (fact_node)-[:FACT_SUBJECT|FACT_OBJECT]->(seed:Entity)
          WITH seed,
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
        ORDER BY seed_score DESC
        LIMIT $seed_limit
        MATCH path = (seed)-[:FACT*1..{depth}]-(o:Entity)
        WITH path, relationships(path) AS rels, length(path) AS hop, seed_score
        WHERE hop > 1
                """

            def build_multi_hop_cypher(seed_cypher: str) -> str:
                return f"""
        {seed_cypher}
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
               WHEN size(coalesce(r.source_ids, [])) > 0 THEN coalesce(r.source_ids, [])
               WHEN r.source_id IS NULL THEN []
               ELSE [r.source_id]
             END AS fact_source_ids
        WITH rels, s, r, o, hop, chain_path, chain_order_path, seed_score, fact_source_ids,
             path_term_match_score,
             CASE WHEN EXISTS {{
               MATCH (source_node:GraphSource)-[:SUPPORTS_FACT]->(fact_node:GraphFact)
               WHERE source_node.source_id IN $source_ids
                 AND fact_node.fact_key = r.fact_key
             }} THEN 3.0 ELSE 0.0 END AS source_match_score,
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
               WHEN coalesce(
                 toFloat(r.source_count),
                 toFloat(size(coalesce(r.source_ids, []))),
                 0.0
               ) > 5.0 THEN 1.0
               ELSE coalesce(
                 toFloat(r.source_count),
                 toFloat(size(coalesce(r.source_ids, []))),
                 0.0
               ) / 5.0
             END AS source_count_score,
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
        WITH rels, s, r, o, hop, confidence_score, source_count_score, recency_score, hop_score,
             chain_path_score, chain_order_score, structural_role, path_term_match_score,
             source_match_score + source_vector_score * 2.0
             + term_match_score + confidence_score
             + source_count_score + hop_score + chain_path_score + chain_order_score
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
                    + source_count_score * 0.05
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

            if fulltext_query or not terms:
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
                if (
                    cached_expansion_seed_rows
                    and len(cached_expansion_seed_rows) <= expansion_cache_preload_seed_limit
                ):
                    preload_path_limit = min(
                        expansion_cache_path_limit,
                        max(
                            1,
                            expansion_cache_preload_path_limit // len(cached_expansion_seed_rows),
                        ),
                    )
                    cached_expansion_paths, expansion_cache_complete = (
                        self._cached_multi_hop_expansion_paths(
                            seed_element_ids=[
                                str(row["element_id"]) for row in cached_expansion_seed_rows
                            ],
                            depth=depth,
                            path_limit=preload_path_limit,
                            load_misses=True,
                        )
                    )
                    if expansion_cache_complete:
                        multi_hop_seed_cypher = """
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
        WHERE elementId(rel) = rel_ref.element_id
        WITH cached_path.path_key AS path_key,
             coalesce(toInteger(cached_path.hop), size(cached_path.rel_ids)) AS hop,
             seed_score,
             coalesce(toInteger(rel_ref.rel_index), 0) AS rel_index,
             rel
        ORDER BY path_key ASC, rel_index ASC
        WITH path_key, hop, seed_score, collect(rel) AS rels
        WHERE hop > 1 AND size(rels) = hop
                    """

            cypher = build_multi_hop_cypher(multi_hop_seed_cypher)
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
                    terms=terms,
                    term_rows=term_rows,
                    fulltext_query=fulltext_query,
                    limit=limit,
                    seed_limit=seed_limit,
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

        if depth > 1:
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
        _set_bool(timings, "graph_used_fulltext", bool(fulltext_query))
        _set_bool(timings, "graph_used_scan_fallback", used_scan_fallback)
        _record_timing(timings, "graph_total_ms", started_at)
        logger.info(
            "Neo4j graph retrieval done: elapsed_ms=%.1f depth=%d max_facts=%d "
            "source_ids_count=%d row_count=%d returned_facts=%d used_fulltext=%s "
            "used_scan_fallback=%s ranking_policy=%s",
            (time.perf_counter() - started_at) * 1000,
            depth,
            query_limit,
            len(source_ids or []),
            len(rows),
            len(lines),
            bool(fulltext_query),
            used_scan_fallback,
            policy,
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


def _default_driver_factory(uri: str, auth: tuple[str, str]):
    try:
        from neo4j import GraphDatabase
    except ImportError as e:
        raise RuntimeError("neo4j package is required for graph knowledge") from e
    return GraphDatabase.driver(uri, auth=auth)


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
        if not element_id:
            continue
        rel_refs.append(
            {
                "element_id": element_id,
                "rel_index": _row_int(rel_ref, "rel_index", fallback_index),
            }
        )
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


def _query_terms(query: str) -> list[str]:
    return [str(row["term"]) for row in _query_term_rows(query)]


def _query_term_rows(query: str) -> list[dict[str, str | float]]:
    rows: list[dict[str, str | float]] = []
    normalized = str(query or "").lower().replace("_", " ")
    raw_terms = []
    for raw in normalized.split():
        term = _clean_query_token(raw)
        if term:
            raw_terms.append(term)
    compact_query = _clean_query_token(normalized)

    for term in raw_terms:
        _append_query_term_row(rows, term, weight=1.0, kind="token")
        if term in ASSISTANT_ENTITY_ALIAS_KEYS:
            for alias in ASSISTANT_ENTITY_ALIAS_KEYS:
                _append_query_term_row(rows, alias, weight=1.0, kind="alias")
        if len(rows) >= _MAX_QUERY_TERMS:
            return rows

    _append_relation_query_terms(rows, [compact_query, *raw_terms])

    for term in raw_terms:
        for run in _cjk_runs(term):
            for size in (2, 3, 4):
                if len(run) < size:
                    continue
                for index in range(0, len(run) - size + 1):
                    _append_query_term_row(
                        rows,
                        run[index : index + size],
                        weight=0.35,
                        kind="cjk_ngram",
                    )
                    if len(rows) >= _MAX_QUERY_TERMS:
                        break
                if len(rows) >= _MAX_QUERY_TERMS:
                    break
            if len(rows) >= _MAX_QUERY_TERMS:
                break
        if len(rows) >= _MAX_QUERY_TERMS:
            break
    return rows


def _fulltext_query(terms: list[str]) -> str:
    return " OR ".join(f'"{_escape_fulltext_term(term)}"' for term in terms if term)


def _escape_fulltext_term(term: str) -> str:
    return str(term).replace("\\", "\\\\").replace('"', '\\"')


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


def _rank_retrieved_rows(rows: list[Any]) -> list[Any]:
    if not any(_has_retrieval_rank_metadata(row) for row in rows):
        return list(rows)
    indexed_rows = list(enumerate(rows))
    indexed_rows.sort(key=lambda item: _retrieved_row_sort_key(item[1], item[0]))
    return [row for _, row in indexed_rows]


def _has_retrieval_rank_metadata(row: Any) -> bool:
    return any(
        _row_value(row, key) is not None for key in ("graph_score", "updated_at", "structural_role")
    )


def _retrieved_row_sort_key(row: Any, index: int) -> tuple[Any, ...]:
    return (
        _row_int(row, "structural_role", 0),
        -_row_float(row, "graph_score", 0.0),
        -_row_float(row, "updated_at", 0.0),
        -_row_float(row, "confidence", 0.0),
        _row_int(row, "hop", 1),
        index,
    )


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row.get(key, default)
    except AttributeError:
        return default


def _row_float(row: Any, key: str, default: float = 0.0) -> float:
    try:
        value = float(_row_value(row, key, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _row_int(row: Any, key: str, default: int = 0) -> int:
    try:
        return int(_row_value(row, key, default))
    except (TypeError, ValueError):
        return default


def _canonical_retrieved_entity_name(value: Any) -> str:
    text = str(value or "").strip()
    if _entity_alias_key(text) in ASSISTANT_ENTITY_ALIAS_KEYS:
        return ASSISTANT_CANONICAL_NAME
    return text


def _context_entity_label(name: str, entity_type: str, include_entity_types: bool) -> str:
    cleaned_name = str(name or "").strip()
    cleaned_type = str(entity_type or "").strip()
    if include_entity_types and cleaned_name and cleaned_type:
        return f"{cleaned_name} ({cleaned_type})"
    return cleaned_name


def _entity_alias_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _format_retrieved_fact_lines(
    entries: list[dict[str, Any]],
    *,
    depth: int,
    limit: int,
) -> list[str]:
    line_limit = max(1, int(limit or 1))
    if depth <= 1:
        return ["- " + str(entry["detail"]) for entry in entries[:line_limit]]

    tree = _retrieved_fact_tree(entries)
    rendered_keys = set()

    def render_node(index: int, path: set[int]) -> str:
        if index in path:
            return str(entries[index]["detail"])
        entry_key = entries[index].get("dedupe_key")
        if entry_key and entry_key in rendered_keys:
            return ""
        if entry_key:
            rendered_keys.add(entry_key)
        node = tree["nodes"][index]
        detail = str(entries[index]["detail"])
        children = node["children"]
        if children:
            child_path = {*path, index}
            linked_parts = [
                child_detail
                for child_index in children
                if (child_detail := render_node(child_index, child_path))
            ]
            if linked_parts:
                detail = f"{detail} | linked: {'; '.join(linked_parts)}"
        return detail

    roots = list(tree["roots"])
    if len(roots) > line_limit or _retrieved_fact_roots_overlap(tree["nodes"], entries, roots):
        roots.sort(
            key=lambda index: _retrieved_fact_root_sort_key(
                tree["nodes"],
                index,
            )
        )

    lines: list[str] = []
    for index in roots:
        rendered = render_node(index, set())
        if not rendered:
            continue
        lines.append("- " + rendered)
        if len(lines) >= line_limit:
            break
    return lines


def _retrieved_fact_tree(entries: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: list[dict[str, list[int]]] = [{"children": []} for _ in entries]
    parent_indexes_by_object: dict[tuple[int, str], list[int]] = {}
    for index, entry in enumerate(entries):
        hop = _retrieval_depth(entry.get("hop", 1))
        object_key = str(entry.get("object_key") or "")
        if object_key:
            parent_indexes_by_object.setdefault((hop, object_key), []).append(index)

    attached: set[int] = set()
    for index, entry in enumerate(entries):
        hop = _retrieval_depth(entry.get("hop", 1))
        if hop <= 1:
            continue
        subject_key = str(entry.get("subject_key") or "")
        if not subject_key:
            continue
        parent_indexes = parent_indexes_by_object.get((hop - 1, subject_key), [])
        if not parent_indexes:
            continue
        parent_index = _best_retrieved_fact_parent_index(entries, parent_indexes)
        if parent_index == index:
            continue
        nodes[parent_index]["children"].append(index)
        attached.add(index)
    roots = [index for index in range(len(entries)) if index not in attached]
    return {"nodes": nodes, "roots": roots}


def _retrieved_fact_roots_overlap(
    nodes: list[dict[str, list[int]]],
    entries: list[dict[str, Any]],
    roots: list[int],
) -> bool:
    seen_keys: set[Any] = set()
    for root_index in roots:
        root_keys = _retrieved_fact_subtree_keys(nodes, entries, root_index, set())
        if seen_keys.intersection(root_keys):
            return True
        seen_keys.update(root_keys)
    return False


def _retrieved_fact_subtree_keys(
    nodes: list[dict[str, list[int]]],
    entries: list[dict[str, Any]],
    index: int,
    path: set[int],
) -> set[Any]:
    if index in path:
        return set()
    keys = set()
    entry_key = entries[index].get("dedupe_key")
    if entry_key:
        keys.add(entry_key)
    child_path = {*path, index}
    for child_index in nodes[index]["children"]:
        keys.update(_retrieved_fact_subtree_keys(nodes, entries, child_index, child_path))
    return keys


def _best_retrieved_fact_parent_index(
    entries: list[dict[str, Any]],
    parent_indexes: list[int],
) -> int:
    return min(
        parent_indexes,
        key=lambda index: _retrieved_fact_parent_sort_key(entries[index], index),
    )


def _retrieved_fact_parent_sort_key(entry: dict[str, Any], index: int) -> tuple[Any, ...]:
    detail = str(entry.get("detail") or "")
    return (
        -_retrieval_depth(entry.get("hop", 1)),
        -int(entry.get("evidence_length") or 0),
        -len(detail),
        str(entry.get("subject_key") or ""),
        str(entry.get("object_key") or ""),
        detail,
        index,
    )


def _retrieved_fact_root_sort_key(
    nodes: list[dict[str, list[int]]],
    index: int,
) -> tuple[Any, ...]:
    descendant_count, descendant_depth = _retrieved_fact_descendant_stats(nodes, index, set())
    return (
        0 if descendant_count else 1,
        -descendant_depth,
        -descendant_count,
        index,
    )


def _retrieved_fact_descendant_stats(
    nodes: list[dict[str, list[int]]],
    index: int,
    path: set[int],
) -> tuple[int, int]:
    if index in path:
        return (0, 0)
    children = nodes[index]["children"]
    if not children:
        return (0, 0)
    child_path = {*path, index}
    descendant_count = 0
    descendant_depth = 0
    for child_index in children:
        child_count, child_depth = _retrieved_fact_descendant_stats(
            nodes,
            child_index,
            child_path,
        )
        descendant_count += 1 + child_count
        descendant_depth = max(descendant_depth, 1 + child_depth)
    return (descendant_count, descendant_depth)


def _clean_query_token(value: str, *, allow_underscore: bool = False) -> str:
    return "".join(
        char
        for char in str(value or "")
        if char.isalnum() or "\u4e00" <= char <= "\u9fff" or (allow_underscore and char == "_")
    )


def _append_query_term_row(
    rows: list[dict[str, str | float]],
    term: str,
    *,
    weight: float,
    kind: str,
) -> None:
    cleaned = _clean_query_token(term, allow_underscore=kind == "predicate")
    if len(cleaned) <= 1:
        return
    for row in rows:
        if row["term"] != cleaned:
            continue
        if float(row["weight"]) < weight:
            row["weight"] = weight
            row["kind"] = kind
        return
    if len(rows) < _MAX_QUERY_TERMS:
        rows.append({"term": cleaned, "weight": weight, "kind": kind})


def _append_relation_query_terms(
    rows: list[dict[str, str | float]],
    values: list[str],
) -> None:
    seen_values = []
    for value in values:
        if value and value not in seen_values:
            seen_values.append(value)
    for value in seen_values:
        for triggers, expansions in _QUERY_RELATION_EXPANSIONS:
            matched_triggers = _matching_relation_triggers(value, triggers)
            if not matched_triggers:
                continue
            for trigger in matched_triggers:
                _append_query_term_row(rows, trigger, weight=1.2, kind="predicate")
            for phrase in _relation_query_phrases(value, matched_triggers):
                _append_query_term_row(rows, phrase, weight=1.8, kind="phrase")
            for expansion in expansions:
                _append_query_term_row(rows, expansion, weight=1.35, kind="predicate")
            if len(rows) >= _MAX_QUERY_TERMS:
                return


def _matching_relation_triggers(value: str, triggers: tuple[str, ...]) -> list[str]:
    matched: list[tuple[int, int, str]] = []
    for trigger in sorted(triggers, key=len, reverse=True):
        start = 0
        while start < len(value):
            index = value.find(trigger, start)
            if index < 0:
                break
            end = index + len(trigger)
            start = index + 1
            if _trigger_has_word_fragment_boundary(value, index, end):
                continue
            if _trigger_has_negation_prefix(value, index):
                continue
            overlaps_longer_trigger = any(
                index < matched_end and end > matched_start
                for matched_start, matched_end, _ in matched
            )
            if overlaps_longer_trigger:
                continue
            matched.append((index, end, trigger))
            break
    return [trigger for _, _, trigger in sorted(matched)]


def _trigger_has_word_fragment_boundary(value: str, start: int, end: int) -> bool:
    trigger = value[start:end]
    if not trigger.isascii() or not trigger.isalnum():
        return False
    if start > 0 and value[start - 1].isalnum():
        return True
    return end < len(value) and value[end].isalnum()


def _trigger_has_negation_prefix(value: str, start: int) -> bool:
    prefix = value[max(0, start - 3) : start]
    return any(prefix.endswith(marker) for marker in _QUERY_TRIGGER_NEGATION_PREFIXES)


def _relation_query_phrases(value: str, triggers: list[str]) -> list[str]:
    phrases: list[str] = []
    for trigger in sorted(triggers, key=len, reverse=True):
        if trigger not in value:
            continue
        before, _, after = value.partition(trigger)
        for phrase in (_clean_cjk_query_phrase(before), _clean_cjk_query_phrase(after)):
            _append_phrase(phrases, phrase)
            if _has_cjk(phrase) and len(phrase) > 4:
                _append_phrase(phrases, phrase[-4:])
                _append_phrase(phrases, phrase[:4])
    return phrases


def _clean_cjk_query_phrase(value: str) -> str:
    phrase = _clean_query_token(value)
    changed = True
    while changed:
        changed = False
        for filler in _CJK_LEADING_QUERY_FILLERS:
            if phrase.startswith(filler) and len(phrase) > len(filler):
                phrase = phrase[len(filler) :]
                changed = True
                break
    changed = True
    while changed:
        changed = False
        for filler in _CJK_TRAILING_QUERY_FILLERS:
            if phrase.endswith(filler) and len(phrase) > len(filler):
                phrase = phrase[: -len(filler)]
                changed = True
                break
    if phrase in _CJK_LEADING_QUERY_FILLERS or phrase in _CJK_TRAILING_QUERY_FILLERS:
        return ""
    return phrase


def _append_phrase(phrases: list[str], phrase: str) -> None:
    if len(phrase) > 1 and phrase not in phrases:
        phrases.append(phrase)


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _append_query_term(terms: list[str], term: str) -> None:
    if len(term) > 1 and term not in terms and len(terms) < _MAX_QUERY_TERMS:
        terms.append(term)


def _cjk_runs(value: str) -> list[str]:
    runs = []
    current = []
    for char in value:
        if "\u4e00" <= char <= "\u9fff":
            current.append(char)
            continue
        if current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


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
        parsed = _DEFAULT_MULTIHOP_EXPANSION_CACHE_PATH_LIMIT
    return max(1, min(10_000, parsed))


def _multi_hop_expansion_cache_preload_seed_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _DEFAULT_MULTIHOP_EXPANSION_CACHE_PRELOAD_SEED_LIMIT
    return max(0, min(16, parsed))


def _multi_hop_expansion_cache_preload_path_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _DEFAULT_MULTIHOP_EXPANSION_CACHE_PRELOAD_PATH_LIMIT
    return max(1, min(1_000, parsed))


def _fact_source_ids(fact: dict[str, Any]) -> list[str]:
    raw = fact.get("source_ids")
    values = raw if isinstance(raw, list) else []
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
