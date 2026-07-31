"""Explicit read-only RAG and GraphRAG tools."""

from __future__ import annotations

import concurrent.futures
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from core.knowledge import GraphSearchResult

from .base import Tool, ToolCapabilities


def _clamp(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _clean_list(values: list[str] | None) -> list[str]:
    return [str(value).strip() for value in values or [] if str(value).strip()]


def _source_ref(hit: dict[str, Any]) -> str:
    kb = str(hit.get("kb_name") or hit.get("kb_id") or "Knowledge").strip()
    doc = str(hit.get("doc_name") or hit.get("doc_id") or "document").strip()
    index = hit.get("chunk_index")
    return f"{kb}/{doc}{f'#{index}' if index is not None else ''}"


class _ResearchAwareTool(Tool):
    def __init__(
        self,
        workspace: str = ".",
        *,
        services: Any = None,
        research_session_provider: Callable[[], Any] | None = None,
        research_branch_provider: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(workspace)
        self.services = services
        self.research_session_provider = research_session_provider
        self.research_branch_provider = research_branch_provider
        self._standalone_owner_id = f"{self.name}:{uuid4().hex}"

    def _session(self):
        return self.research_session_provider() if self.research_session_provider else None

    def _services(self, session):
        return self.services or getattr(session, "services", None)

    def _branch_id(self) -> str:
        if self.research_branch_provider:
            return str(self.research_branch_provider() or "main")
        return "main"

    def _owner_id(self, session: Any) -> str:
        turn_id = str(getattr(session, "turn_id", "") or "").strip()
        return turn_id or self._standalone_owner_id

    @staticmethod
    def _timeout(session, default: float) -> float:
        if session is None:
            return default
        return min(default, max(0.001, session.budget.seconds_until_synthesis()))

    def cancel(self):
        session = self._session()
        services = self._services(session)
        if services is not None and hasattr(services, "cancel_pending"):
            services.cancel_pending(owner_id=self._owner_id(session))


class RagSearchTool(_ResearchAwareTool):
    name = "rag_search"
    description = (
        "Search ATRI knowledge bases explicitly. Returns source-localized chunks and scores; "
        "in Deep Research mode each result receives a stable [R#] citation."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "kb_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional knowledge base IDs.",
            },
            "kb_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional knowledge base names.",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum chunks to return (1-50).",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    capabilities = ToolCapabilities(
        capability="knowledge.search",
        read_only=True,
        network=True,
        supports_parallel=True,
    )

    def execute(
        self,
        query: str,
        kb_ids: list[str] | None = None,
        kb_names: list[str] | None = None,
        top_k: int = 5,
    ) -> str:
        clean_query = str(query or "").strip()
        if not clean_query:
            return "Error: query is required."
        session = self._session()
        services = self._services(session)
        if services is None or getattr(services, "knowledge_manager", None) is None:
            return "RAG search unavailable: no knowledge service is configured."
        if session is not None:
            decision = session.reserve_tool_call(self.name)
            if not decision.allowed:
                return f"RAG search blocked: {decision.reason}."
        try:
            result = services.rag_search(
                query=clean_query,
                kb_ids=_clean_list(kb_ids),
                kb_names=_clean_list(kb_names),
                top_k=_clamp(top_k, 5, 1, 50),
                timeout=self._timeout(session, 60.0),
                owner_id=self._owner_id(session),
            )
        except (
            concurrent.futures.CancelledError,
            OSError,
            RuntimeError,
            TimeoutError,
            ValueError,
        ) as exc:
            return f"RAG search unavailable: {exc}"
        hits = list(result.get("results") or [])
        if result.get("status") == "unavailable":
            return f"RAG search unavailable: {result.get('reason') or 'not configured'}."
        if not hits:
            return f"RAG search: {clean_query}\nNo matching chunks."
        lines = [f"RAG search: {clean_query}"]
        for index, hit in enumerate(hits, start=1):
            source_ref = _source_ref(hit)
            chunk_id = str(hit.get("chunk_id") or f"result-{index}")
            excerpt = str(hit.get("content") or hit.get("excerpt") or "").strip()
            score = _optional_float(hit.get("score"))
            if session is not None:
                item = session.ledger.add_rag(
                    query=clean_query,
                    chunk_id=chunk_id,
                    title=str(hit.get("doc_name") or source_ref),
                    locator=source_ref,
                    excerpt=excerpt,
                    source_ids=[chunk_id],
                    source_refs=[source_ref],
                    score=score,
                    branch_id=self._branch_id(),
                    metadata={
                        "kb_id": hit.get("kb_id"),
                        "doc_id": hit.get("doc_id"),
                        "chunk_index": hit.get("chunk_index"),
                    },
                )
                label = item.citation_id
            else:
                label = f"RAG{index}"
            lines.extend(
                [
                    f"[{label}] {source_ref}",
                    f"score: {score:.4f}" if score is not None else "score: n/a",
                    f"excerpt: {excerpt}",
                    f"chunk_id: {chunk_id}",
                ]
            )
        return "\n".join(lines)


class GraphRagSearchTool(_ResearchAwareTool):
    name = "graphrag_search"
    description = (
        "Search structured GraphRAG facts independently or anchor expansion to prior RAG "
        "citations. Deep Research results receive stable [G#] citations."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Graph search query."},
            "anchor_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional RAG citations, for example R1.",
            },
            "max_facts": {
                "type": "integer",
                "description": "Maximum facts (1-100).",
                "default": 8,
            },
            "retrieval_depth": {
                "type": "integer",
                "description": "Graph traversal depth (1-7).",
            },
            "ranking_policy": {
                "type": "string",
                "enum": ["hybrid", "relevance", "latest"],
                "description": "Fact ranking policy.",
            },
            "expansion_candidate_limit": {
                "type": "integer",
                "description": "Expansion candidates (1-1000).",
            },
        },
        "required": ["query"],
    }
    capabilities = ToolCapabilities(
        capability="knowledge.graph.search",
        read_only=True,
        network=True,
        supports_parallel=True,
    )

    def execute(
        self,
        query: str,
        anchor_ids: list[str] | None = None,
        max_facts: int = 8,
        retrieval_depth: int | None = None,
        ranking_policy: str | None = None,
        expansion_candidate_limit: int | None = None,
    ) -> str:
        clean_query = str(query or "").strip()
        if not clean_query:
            return "Error: query is required."
        session = self._session()
        services = self._services(session)
        if services is None or getattr(services, "graph_manager", None) is None:
            return "GraphRAG search unavailable: no graph service is configured."
        anchors = _clean_list(anchor_ids)
        if anchors and session is None:
            return "GraphRAG search error: anchor_ids require a Deep Research session."
        source_ids: list[str] = []
        source_scores: dict[str, float] = {}
        if anchors:
            try:
                source_ids, source_scores = session.ledger.resolve_anchor_ids(anchors)
            except ValueError as exc:
                return f"GraphRAG search error: {exc}"
        if session is not None:
            decision = session.reserve_tool_call(self.name)
            if not decision.allowed:
                return f"GraphRAG search blocked: {decision.reason}."
        policy = str(ranking_policy or "hybrid").strip().lower()
        if policy not in {"hybrid", "relevance", "latest"}:
            policy = "hybrid"
        try:
            result: GraphSearchResult = services.graph_search(
                query=clean_query,
                source_ids=source_ids,
                source_scores=source_scores,
                max_facts=_clamp(max_facts, 8, 1, 100),
                retrieval_depth=(
                    _clamp(retrieval_depth, 1, 1, 7) if retrieval_depth is not None else None
                ),
                ranking_policy=policy,
                expansion_candidate_limit=(
                    _clamp(expansion_candidate_limit, 40, 1, 1000)
                    if expansion_candidate_limit is not None
                    else None
                ),
                timeout=self._timeout(session, 60.0),
                owner_id=self._owner_id(session),
            )
        except (
            concurrent.futures.CancelledError,
            OSError,
            RuntimeError,
            TimeoutError,
            ValueError,
        ) as exc:
            return f"GraphRAG search unavailable: {exc}"
        status = result.diagnostics.get("status")
        if status == "unavailable":
            return "GraphRAG search unavailable: graph retrieval is not configured."
        if status == "timeout":
            return "GraphRAG search degraded: graph retrieval timed out."
        if not result.facts:
            return f"GraphRAG search: {clean_query}\nNo matching facts."
        lines = [f"GraphRAG search: {clean_query}"]
        for index, fact in enumerate(result.facts, start=1):
            triple = f"{fact.subject} -[{fact.predicate}]-> {fact.object}"
            strength = (
                "derived"
                if fact.provenance_incomplete or not (fact.source_ids or fact.source_refs)
                else "full"
            )
            if session is not None:
                item = session.ledger.add_graph(
                    query=clean_query,
                    fact_key=fact.fact_key,
                    subject=fact.subject,
                    predicate=fact.predicate,
                    object_value=fact.object,
                    title=triple,
                    locator=fact.source_refs[0] if fact.source_refs else f"graph:{fact.fact_key}",
                    excerpt=fact.evidence or triple,
                    source_ids=fact.source_ids,
                    source_refs=fact.source_refs,
                    score=fact.graph_score,
                    confidence=fact.confidence,
                    strength=strength,
                    branch_id=self._branch_id(),
                    metadata={
                        "hop": fact.hop,
                        "provenance_incomplete": fact.provenance_incomplete,
                        "diagnostics": fact.diagnostics,
                    },
                )
                label = item.citation_id
            else:
                label = f"GRAPH{index}"
            lines.extend(
                [
                    f"[{label}] {triple}",
                    f"fact_key: {fact.fact_key}",
                    f"hop: {fact.hop}; graph_score: {fact.graph_score:.4f}; "
                    f"confidence: {fact.confidence:.4f}",
                    f"evidence: {fact.evidence}",
                    f"source_ids: {', '.join(fact.source_ids) or 'none'}",
                    f"source_refs: {', '.join(fact.source_refs) or 'none'}",
                    f"provenance: {strength}",
                    f"diagnostics: {json.dumps(fact.diagnostics, ensure_ascii=False)}",
                ]
            )
        return "\n".join(lines)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
