"""Neo4j persistence and retrieval for graph knowledge facts."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from core import logger

DriverFactory = Callable[[str, tuple[str, str]], Any]


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

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def update_config(self, config: dict[str, Any] | None) -> None:
        new_config = dict(config or {})
        if _connection_signature(self.config) != _connection_signature(new_config):
            self.close()
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
        self._constraints_ready = False

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
            "DROP CONSTRAINT entity_name_key IF EXISTS",
            "CREATE CONSTRAINT entity_identity IF NOT EXISTS "
            "FOR (e:Entity) REQUIRE (e.name_key, e.type_key) IS UNIQUE",
            "CREATE CONSTRAINT fact_key IF NOT EXISTS "
            "FOR ()-[r:FACT]-() REQUIRE r.fact_key IS UNIQUE",
        ]
        with self._session() as session:
            for statement in statements:
                try:
                    session.run(statement)
                except Exception as e:
                    logger.debug("Neo4j graph constraint skipped: %s", e)
        self._constraints_ready = True

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
        WITH r, fact,
             coalesce(r.source_ids, []) + coalesce(fact.source_ids, [fact.source_id])
             AS raw_source_ids
        WITH r, fact,
             reduce(source_ids = [], source_id IN raw_source_ids |
                  CASE
                    WHEN source_id IN source_ids THEN source_ids
                    ELSE source_ids + [source_id]
                  END) AS source_ids
          SET r.predicate = fact.predicate,
              r.source_id = fact.source_id,
              r.source_ids = source_ids,
              r.source_kind = fact.source_kind,
              r.evidence = fact.evidence,
              r.confidence = fact.confidence,
              r.metadata_json = fact.metadata_json,
              r.updated_at = fact.now,
              r.source_count = size(source_ids)
        RETURN count(r) AS count
        """
        with self._session() as session:
            result = list(session.run(query, facts=rows))
        return _result_count(result, len(rows))

    def retrieve_context(
        self,
        *,
        query: str,
        source_ids: list[str] | None = None,
        max_facts: int = 8,
        retrieval_depth: int = 1,
    ) -> str:
        if not self.enabled:
            return ""
        self.initialize()
        if self.driver is None:
            return ""
        terms = _query_terms(query)
        if not terms and not source_ids:
            return ""
        depth = _retrieval_depth(retrieval_depth)
        if depth <= 1:
            cypher = """
        MATCH (s:Entity)-[r:FACT]->(o:Entity)
        WHERE
          (
            size($source_ids) > 0
            AND (
              r.source_id IN $source_ids
              OR any(source_id IN coalesce(r.source_ids, []) WHERE source_id IN $source_ids)
            )
          )
          OR any(term IN $terms WHERE
              toLower(s.name) CONTAINS term
              OR toLower(o.name) CONTAINS term
              OR toLower(r.predicate) CONTAINS term)
        RETURN s.name AS subject,
               r.predicate AS predicate,
               o.name AS object,
               r.evidence AS evidence,
               r.confidence AS confidence
        ORDER BY r.updated_at DESC
        LIMIT $limit
        """
        else:
            cypher = f"""
        MATCH path = (s:Entity)-[:FACT*1..{depth}]->(o:Entity)
        WHERE
          (
            size($source_ids) > 0
            AND any(rel IN relationships(path) WHERE
              rel.source_id IN $source_ids
              OR any(source_id IN coalesce(rel.source_ids, []) WHERE source_id IN $source_ids))
          )
          OR any(term IN $terms WHERE
              any(node IN nodes(path) WHERE toLower(node.name) CONTAINS term)
              OR any(rel IN relationships(path) WHERE toLower(rel.predicate) CONTAINS term))
        WITH relationships(path) AS rels, length(path) AS hop
        WITH rels[size(rels) - 1] AS r, hop
        RETURN startNode(r).name AS subject,
               r.predicate AS predicate,
               endNode(r).name AS object,
               r.evidence AS evidence,
               r.confidence AS confidence,
               hop AS hop
        ORDER BY hop ASC, r.updated_at DESC
        LIMIT $limit
        """
        with self._session() as session:
            rows = list(
                session.run(
                    cypher,
                    source_ids=source_ids or [],
                    terms=terms,
                    limit=max(1, int(max_facts or 8)),
                    timeout=3,
                )
            )
        lines = []
        seen = set()
        for row in rows:
            subject = str(row.get("subject") or "").strip()
            predicate = str(row.get("predicate") or "").strip()
            obj = str(row.get("object") or "").strip()
            if not subject or not predicate or not obj:
                continue
            key = (subject.lower(), predicate.lower(), obj.lower())
            if key in seen:
                continue
            seen.add(key)
            evidence = str(row.get("evidence") or "").strip()
            detail = f"{subject} -[{predicate}]-> {obj}"
            if depth > 1:
                hop = _retrieval_depth(row.get("hop", 1))
                detail = f"[{hop}-hop] {detail}"
            if evidence:
                detail += f" ({evidence})"
            lines.append("- " + detail)
        return "[Graph context]\n" + "\n".join(lines) if lines else ""

    def _session(self):
        if self.driver is None:
            raise RuntimeError("Neo4j graph client is not initialized")
        database = str(self.config.get("database") or "neo4j")
        return self.driver.session(database=database)


def _default_driver_factory(uri: str, auth: tuple[str, str]):
    try:
        from neo4j import GraphDatabase
    except ImportError as e:
        raise RuntimeError("neo4j package is required for graph knowledge") from e
    return GraphDatabase.driver(uri, auth=auth)


def _query_terms(query: str) -> list[str]:
    terms = []
    for raw in str(query or "").lower().replace("_", " ").split():
        term = "".join(char for char in raw if char.isalnum() or "\u4e00" <= char <= "\u9fff")
        if len(term) > 1 and term not in terms:
            terms.append(term)
        if len(terms) >= 12:
            break
    return terms


def _retrieval_depth(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(3, parsed))


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
