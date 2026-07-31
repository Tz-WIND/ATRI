"""Thread-safe bridges from synchronous agent tools to async knowledge services."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import replace
from typing import Any, TypeVar

from core.knowledge import GraphSearchResult

T = TypeVar("T")


class ResearchServices:
    """Run Knowledge and GraphRAG coroutines on their owning event loop."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        knowledge_manager: Any = None,
        graph_manager: Any = None,
        knowledge_config_provider: Callable[[], Mapping[str, Any] | None] = lambda: {},
        graph_config_provider: Callable[[], Mapping[str, Any] | None] = lambda: {},
    ) -> None:
        self.loop = loop
        self.knowledge_manager = knowledge_manager
        self.graph_manager = graph_manager
        self.knowledge_config_provider = knowledge_config_provider
        self.graph_config_provider = graph_config_provider
        self._pending: dict[concurrent.futures.Future[Any], str | None] = {}
        self._lock = threading.Lock()

    def _run(
        self,
        coroutine: Coroutine[Any, Any, T],
        timeout: float,
        *,
        owner_id: str | None = None,
    ) -> T:
        if self.loop.is_closed() or not self.loop.is_running():
            coroutine.close()
            raise RuntimeError("research service event loop is unavailable")
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        with self._lock:
            self._pending[future] = str(owner_id) if owner_id is not None else None
        try:
            return future.result(timeout=max(0.001, float(timeout)))
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError("research service call timed out") from exc
        finally:
            with self._lock:
                self._pending.pop(future, None)

    def rag_search(
        self,
        *,
        query: str,
        kb_ids: list[str] | None = None,
        kb_names: list[str] | None = None,
        top_k: int = 5,
        timeout: float | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        if self.knowledge_manager is None:
            return {"status": "unavailable", "results": [], "query": query}
        config = dict(self.knowledge_config_provider() or {})
        if isinstance(config.get("knowledge"), Mapping):
            config = dict(config["knowledge"])
        names = _unique_text(kb_names or [])
        selected_ids = _unique_text(
            kb_ids if kb_ids else ([] if names else config.get("active_bases", []))
        )
        if not selected_ids and not names:
            return {
                "status": "unavailable",
                "reason": "no active knowledge bases",
                "results": [],
                "query": query,
            }
        return self._run(
            self.knowledge_manager.retrieve(
                query=query,
                kb_ids=selected_ids,
                kb_names=names,
                top_k=top_k,
            ),
            timeout or 60.0,
            owner_id=owner_id,
        )

    def graph_search(
        self,
        *,
        query: str,
        source_ids: list[str] | None = None,
        source_scores: dict[str, float] | None = None,
        max_facts: int = 8,
        retrieval_depth: int | None = None,
        ranking_policy: str | None = None,
        expansion_candidate_limit: int | None = None,
        timeout: float | None = None,
        owner_id: str | None = None,
    ) -> GraphSearchResult:
        if self.graph_manager is None:
            return GraphSearchResult(
                query=query,
                facts=[],
                context_text="",
                diagnostics={"status": "unavailable"},
            )
        graph_config = dict(self.graph_config_provider() or {})
        if isinstance(graph_config.get("graph"), Mapping):
            graph_config = dict(graph_config["graph"])
        configured_timeout = _positive_float(graph_config.get("retrieval_timeout_seconds"), 60.0)
        call_timeout = (
            min(configured_timeout, timeout) if timeout is not None else configured_timeout
        )
        return self._run(
            self._graph_search_with_source_refs(
                query=query,
                source_ids=_unique_text(source_ids or []),
                source_scores=dict(source_scores or {}),
                max_facts=max_facts,
                retrieval_depth=retrieval_depth,
                ranking_policy=ranking_policy,
                expansion_candidate_limit=expansion_candidate_limit,
            ),
            call_timeout,
            owner_id=owner_id,
        )

    async def _graph_search_with_source_refs(self, **kwargs: Any) -> GraphSearchResult:
        result = await self.graph_manager.search_facts(**kwargs)
        if not result.facts or self.knowledge_manager is None:
            return result
        store = getattr(self.knowledge_manager, "store", None)
        if store is None or not hasattr(store, "chunks_by_ids"):
            return result
        source_ids = _unique_text(
            source_id for fact in result.facts for source_id in fact.source_ids
        )
        rows = store.chunks_by_ids(source_ids)
        references = {
            str(row.get("chunk_id") or ""): _chunk_source_ref(row)
            for row in rows
            if isinstance(row, dict) and row.get("chunk_id")
        }
        facts = []
        for fact in result.facts:
            hydrated = _unique_text(
                [
                    *fact.source_refs,
                    *(references.get(source_id, "") for source_id in fact.source_ids),
                ]
            )
            facts.append(replace(fact, source_refs=hydrated))
        return replace(result, facts=facts)

    def cancel_pending(self, *, owner_id: str | None = None) -> None:
        with self._lock:
            pending = [
                future
                for future, future_owner in self._pending.items()
                if owner_id is None or future_owner == str(owner_id)
            ]
        for future in pending:
            future.cancel()


def _unique_text(values) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _chunk_source_ref(row: Mapping[str, Any]) -> str:
    kb_name = str(row.get("kb_name") or row.get("kb_id") or "Knowledge").strip()
    doc_name = str(row.get("doc_name") or row.get("doc_id") or "document").strip()
    chunk_index = row.get("chunk_index")
    suffix = f"#{chunk_index}" if chunk_index is not None else ""
    return f"{kb_name}/{doc_name}{suffix}"
