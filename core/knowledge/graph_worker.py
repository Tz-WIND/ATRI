"""Background queue for non-blocking graph tuple extraction."""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from core import logger
from core.agent.llm import LLM
from core.knowledge.chunking import RecursiveTextChunker
from core.knowledge.extraction import MAX_EXTRACTION_TUPLES, GraphTupleExtractor
from core.knowledge.graph import Neo4jGraphClient
from core.knowledge.graph_constants import (
    GRAPH_EXPANSION_CANDIDATE_MAX_LIMIT,
    GRAPH_EXTRACTION_BATCH_CHARS,
    GRAPH_EXTRACTION_BATCH_OVERLAP_CHARS,
    GRAPH_EXTRACTION_SEMANTIC_CHUNK_CHARS,
    GRAPH_EXTRACTION_SEMANTIC_CHUNK_OVERLAP_CHARS,
    GRAPH_EXTRACTION_TIMEOUT_SECONDS,
    GRAPH_RETRIEVAL_DEFAULT_DEPTH,
    GRAPH_RETRIEVAL_MAX_DEPTH,
    GRAPH_RETRIEVAL_TIMEOUT_SECONDS,
)
from core.knowledge.graph_values import (
    _multi_hop_expansion_cache_path_limit,
    _multi_hop_expansion_cache_preload_path_limit,
    _multi_hop_expansion_cache_preload_seed_limit,
)
from core.runtime import TaskStore

_EXTRACTION_MAX_ATTEMPTS = 3
_GRAPH_EXTRACTION_DEFAULT_MAX_TOKENS = 4096
_EXTRACTION_CONTEXT_QUERY_MAX_CHARS = 2000
_GRAPH_EXTRACTION_TARGET_CHARS_PER_TUPLE = 400
GRAPH_EXTRACTION_TUPLE_ALIGNED_BATCH_CHARS = min(
    GRAPH_EXTRACTION_BATCH_CHARS,
    MAX_EXTRACTION_TUPLES * _GRAPH_EXTRACTION_TARGET_CHARS_PER_TUPLE,
)


@dataclass
class GraphExtractionJob:
    task_id: str
    source_kind: str
    text: str = ""
    chunks: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class GraphKnowledgeManager:
    """Owns graph extraction queue, worker, and Neo4j client lifecycle."""

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        graph_client: Neo4jGraphClient | None = None,
        extractor: GraphTupleExtractor | None = None,
        task_store: TaskStore | None = None,
        runtime_dir: str | Path | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.graph_config = _graph_config_from_app_config(self.config)
        self.graph_client = graph_client or Neo4jGraphClient(self.graph_config)
        self.extractor = extractor or GraphTupleExtractor(self._create_llm)
        self.task_store = task_store or TaskStore(runtime_dir)
        self._owns_task_store = task_store is None
        self.queue: asyncio.Queue[GraphExtractionJob] | None = None
        self._worker_task: asyncio.Task | None = None
        self._closing = False

    async def initialize(self) -> None:
        self._closing = False
        if self.graph_config.get("enabled"):
            self._ensure_worker()
            try:
                await asyncio.to_thread(self.graph_client.initialize)
            except Exception as e:
                logger.warning("Neo4j graph knowledge initialization skipped: %s", e)

    async def close(self, *, drain_seconds: float = 3.0) -> None:
        self._closing = True
        if (
            drain_seconds > 0
            and self.queue is not None
            and self.queue.qsize() > 0
            and self._worker_task is not None
            and not self._worker_task.done()
        ):
            try:
                await asyncio.wait_for(self.queue.join(), timeout=drain_seconds)
            except TimeoutError:
                logger.debug("Graph extraction queue not fully drained before shutdown")
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        self.task_store.mark_incomplete_as_interrupted(
            reason="graph extraction interrupted during shutdown",
            kind="graph_extraction",
        )
        await asyncio.to_thread(self.graph_client.close)
        if self._owns_task_store:
            self.task_store.close()

    async def drain(self, wait_seconds: float = 5.0) -> None:
        if self.queue is None:
            return
        await asyncio.wait_for(self.queue.join(), timeout=wait_seconds)

    def update_config(self, config: dict[str, Any]) -> None:
        self.config = dict(config or {})
        self.graph_config = _graph_config_from_app_config(self.config)
        self.graph_client.update_config(self.graph_config)
        if self.graph_config.get("enabled"):
            self._closing = False
            self._ensure_worker()

    async def test_connection(self, config: dict[str, Any] | None = None) -> dict:
        cfg = dict(config or {})
        if cfg.get("password") == "***":
            cfg["password"] = str(self.graph_config.get("password") or "")
        return await asyncio.to_thread(self.graph_client.test_connection, cfg)

    def enqueue_document(
        self,
        *,
        kb_id: str,
        doc_id: str,
        doc_name: str,
        chunks: list[dict],
    ) -> str | None:
        if not self._can_enqueue("documents"):
            return None
        input_text = "\n\n".join(str(chunk.get("content") or "") for chunk in chunks)
        task_id = self.task_store.create_task(
            kind="graph_extraction",
            title=f"Extract graph facts from {doc_name}",
            input_text=input_text,
            metadata={
                "source": "documents",
                "kb_id": kb_id,
                "doc_id": doc_id,
                "doc_name": doc_name,
                "chunk_count": len(chunks),
            },
        )
        job = GraphExtractionJob(
            task_id=task_id,
            source_kind="document",
            chunks=list(chunks),
            metadata={"kb_id": kb_id, "doc_id": doc_id, "doc_name": doc_name},
        )
        return self._put_job(task_id, job)

    def enqueue_chat_turn(
        self,
        *,
        user_text: str,
        assistant_text: str,
        session_id: str,
        platform: str,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if not self._can_enqueue("chat"):
            return None
        text = _chat_turn_text(user_text, assistant_text)
        task_id = self.task_store.create_task(
            kind="graph_extraction",
            title="Extract graph facts from chat turn",
            input_text=text,
            metadata={
                "source": "chat",
                "session_id": session_id,
                "platform": platform,
                **(metadata or {}),
            },
        )
        job = GraphExtractionJob(
            task_id=task_id,
            source_kind="chat",
            text=text,
            metadata={"session_id": session_id, "platform": platform, **(metadata or {})},
        )
        return self._put_job(task_id, job)

    def enqueue_manual_ingest(self, *, text: str, source_name: str = "manual.txt") -> str | None:
        cleaned = str(text or "").strip()
        if not cleaned:
            raise ValueError("content is required")
        if not self._can_enqueue(
            "manual",
            require_extraction_enabled=False,
            require_source_enabled=False,
        ):
            return None
        clean_source_name = str(source_name or "").strip() or "manual.txt"
        task_id = self.task_store.create_task(
            kind="graph_extraction",
            title=f"Manual graph ingest: {clean_source_name}",
            input_text=cleaned,
            metadata={
                "source": "manual",
                "source_name": clean_source_name,
            },
        )
        source_id = f"manual:{task_id}"
        job = GraphExtractionJob(
            task_id=task_id,
            source_kind="document",
            text=cleaned,
            metadata={
                "source": "manual",
                "source_name": clean_source_name,
                "source_id": source_id,
            },
        )
        return self._put_job(task_id, job)

    async def retrieve_context(
        self,
        *,
        query: str,
        source_ids: list[str] | None = None,
        source_scores: dict[str, float] | None = None,
        max_facts: int = 8,
        retrieval_depth: int | None = None,
        ranking_policy: str | None = None,
        expansion_candidate_limit: int | None = None,
        timings: dict[str, Any] | None = None,
    ) -> str:
        if not self.graph_config.get("enabled") or not self.graph_config.get("retrieval_enabled"):
            return ""
        depth = _retrieval_depth(
            retrieval_depth
            if retrieval_depth is not None
            else self.graph_config.get("retrieval_depth", GRAPH_RETRIEVAL_DEFAULT_DEPTH)
        )
        policy = _ranking_policy(ranking_policy or self.graph_config.get("ranking_policy"))
        candidate_limit = _expansion_candidate_limit(
            expansion_candidate_limit
            if expansion_candidate_limit is not None
            else self.graph_config.get("expansion_candidate_limit")
        )
        started_at = time.perf_counter()
        try:
            retrieval_timings = timings if timings is not None else {}
            retrieve_kwargs: dict[str, Any] = {
                "query": query,
                "source_ids": source_ids or [],
                "source_scores": source_scores or {},
                "max_facts": max_facts,
                "retrieval_depth": depth,
                "ranking_policy": policy,
                "expansion_candidate_limit": candidate_limit,
            }
            if _accepts_keyword(
                self.graph_client.retrieve_context,
                "timings",
            ):
                retrieve_kwargs["timings"] = retrieval_timings
            timeout_seconds = _timeout_seconds(
                self.graph_config.get("retrieval_timeout_seconds"),
                GRAPH_RETRIEVAL_TIMEOUT_SECONDS,
            )
            context = await asyncio.wait_for(
                asyncio.to_thread(
                    self.graph_client.retrieve_context,
                    **retrieve_kwargs,
                ),
                timeout=timeout_seconds,
            )
            logger.debug(
                "Graph knowledge retrieval done: elapsed_ms=%.1f depth=%d max_facts=%d "
                "source_ids_count=%d expansion_candidate_limit=%d ranking_policy=%s "
                "context_chars=%d returned_context=%s graph_total_ms=%.1f "
                "graph_single_hop_ms=%.1f graph_multi_hop_ms=%.1f "
                "graph_scan_fallback_ms=%.1f graph_format_ms=%.1f graph_rows=%d "
                "graph_returned_facts=%d graph_multihop_seed_count=%d "
                "graph_multihop_cache_hit=%s graph_multihop_cached_seed_count=%d "
                "graph_multihop_live_seed_limit=%d graph_multihop_partial_cache_hit=%s "
                "graph_multihop_persistent_cache_hit_count=%d",
                (time.perf_counter() - started_at) * 1000,
                depth,
                max_facts,
                len(source_ids or []),
                candidate_limit,
                policy,
                len(context),
                bool(context),
                _timing_float(retrieval_timings, "graph_total_ms"),
                _timing_float(retrieval_timings, "graph_single_hop_ms"),
                _timing_float(retrieval_timings, "graph_multi_hop_ms"),
                _timing_float(retrieval_timings, "graph_scan_fallback_ms"),
                _timing_float(retrieval_timings, "graph_format_ms"),
                _timing_int(retrieval_timings, "graph_rows"),
                _timing_int(retrieval_timings, "graph_returned_facts"),
                _timing_int(retrieval_timings, "graph_multihop_seed_count"),
                _timing_bool(retrieval_timings, "graph_multihop_cache_hit"),
                _timing_int(retrieval_timings, "graph_multihop_cached_seed_count"),
                _timing_int(retrieval_timings, "graph_multihop_live_seed_limit"),
                _timing_bool(retrieval_timings, "graph_multihop_partial_cache_hit"),
                _timing_int(
                    retrieval_timings,
                    "graph_multihop_persistent_cache_hit_count",
                ),
            )
            return context
        except TimeoutError:
            timeout_seconds = _timeout_seconds(
                self.graph_config.get("retrieval_timeout_seconds"),
                GRAPH_RETRIEVAL_TIMEOUT_SECONDS,
            )
            logger.warning(
                "Graph knowledge retrieval timed out after %.3fs",
                timeout_seconds,
            )
            return ""
        except Exception as e:
            logger.warning("Graph knowledge retrieval skipped: %s", e)
            return ""

    def _can_enqueue(
        self,
        source: str,
        *,
        require_extraction_enabled: bool = True,
        require_source_enabled: bool = True,
    ) -> bool:
        if self._closing or self.queue is None:
            return False
        if not self.graph_config.get("enabled"):
            return False
        if require_extraction_enabled and not self.graph_config.get(
            "extraction_enabled",
            True,
        ):
            return False
        if int(self.graph_config.get("queue_max_size") or 0) < 1:
            return False
        sources = self.graph_config.get("extraction_sources", ["documents", "chat"])
        if require_source_enabled and source not in sources:
            return False
        return not self.queue.full()

    def _put_job(self, task_id: str, job: GraphExtractionJob) -> str | None:
        if self.queue is None or self.queue.full():
            self.task_store.finish_task(
                task_id,
                status="canceled",
                error="graph extraction queue is full",
            )
            return None
        try:
            self.queue.put_nowait(job)
            self.task_store.append_event(
                task_id,
                "graph_extraction_queued",
                {"queue_size": self.queue.qsize()},
            )
            return task_id
        except asyncio.QueueFull:
            self.task_store.finish_task(
                task_id,
                status="canceled",
                error="graph extraction queue is full",
            )
            return None

    def _ensure_worker(self) -> None:
        if self.queue is None:
            max_size = max(0, int(self.graph_config.get("queue_max_size") or 1000))
            self.queue = asyncio.Queue(maxsize=max_size)
        if self._worker_task is not None and not self._worker_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("Graph extraction worker start skipped: no running event loop")
            return
        self._worker_task = loop.create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        while True:
            if self.queue is None:
                await asyncio.sleep(0)
                continue
            job = await self.queue.get()
            try:
                await self._process_job(job)
            finally:
                self.queue.task_done()

    async def _process_job(self, job: GraphExtractionJob) -> None:
        self.task_store.start_task(job.task_id)
        try:
            facts = []
            failed_extractions = []
            if job.source_kind == "document" and job.chunks:
                for batch in _document_extraction_batches(job.chunks, job.task_id):
                    chunk_ids = [item["chunk_id"] for item in batch]
                    source_id = chunk_ids[0]
                    metadata = {
                        **job.metadata,
                        "chunk_id": source_id,
                        "chunk_ids": chunk_ids,
                        "source_ids": chunk_ids,
                        "chunk_count": len(chunk_ids),
                    }
                    batch_facts, batch_failed = await self._extract_segmented_text(
                        job=job,
                        text=_document_batch_text(batch),
                        source_id=source_id,
                        source_kind=job.source_kind,
                        metadata=metadata,
                    )
                    facts.extend(batch_facts)
                    failed_extractions.extend(batch_failed)
            else:
                source_id = (
                    _chat_source_id(job.metadata)
                    if job.source_kind == "chat"
                    else str(job.metadata.get("source_id") or f"{job.source_kind}:{job.task_id}")
                )
                batch_facts, batch_failed = await self._extract_segmented_text(
                    job=job,
                    text=job.text,
                    source_id=source_id,
                    source_kind=job.source_kind,
                    metadata=job.metadata,
                    semantic_chunking=True,
                )
                facts.extend(batch_facts)
                failed_extractions.extend(batch_failed)
            written = await asyncio.to_thread(self.graph_client.upsert_facts, facts)
            result = f"extracted {len(facts)} facts; wrote {written}"
            if failed_extractions:
                result += f"; skipped {len(failed_extractions)} failed extraction(s)"
            self.task_store.finish_task(
                job.task_id,
                result=result,
                metadata={
                    "fact_count": len(facts),
                    "written_count": written,
                    "failed_extraction_count": len(failed_extractions),
                    "failed_extractions": failed_extractions[:5],
                },
            )
        except Exception as e:
            logger.warning("Graph extraction failed: %s", e)
            self.task_store.finish_task(job.task_id, status="failed", error=str(e))

    async def _extract_segmented_text(
        self,
        *,
        job: GraphExtractionJob,
        text: str,
        source_id: str,
        source_kind: str,
        metadata: dict[str, Any],
        semantic_chunking: bool = False,
    ) -> tuple[list[dict], list[dict]]:
        facts: list[dict] = []
        failed_extractions: list[dict] = []
        segments = _extraction_text_segments(text, semantic_chunking=semantic_chunking)
        for segment_index, segment_text in enumerate(segments):
            segment_metadata = dict(metadata)
            segment_source_id = source_id
            if len(segments) > 1:
                segment_metadata["text_part_index"] = segment_index + 1
                segment_metadata["text_part_count"] = len(segments)
                segment_source_id = f"{source_id}:part-{segment_index + 1}"
            extracted, error = await self._extract_facts_with_retries(
                task_id=job.task_id,
                text=segment_text,
                source_id=segment_source_id,
                source_kind=source_kind,
                metadata=segment_metadata,
            )
            if error:
                failed_extractions.append({"source_id": segment_source_id, "error": error})
                continue
            facts.extend(extracted)
        return facts, failed_extractions

    async def _extract_facts_with_retries(
        self,
        text: str,
        *,
        task_id: str,
        source_id: str,
        source_kind: str,
        metadata: dict[str, Any],
    ) -> tuple[list[dict], str | None]:
        existing_graph_context = await self._existing_graph_context_for_extraction(text)
        last_error: Exception | None = None
        for attempt in range(1, _EXTRACTION_MAX_ATTEMPTS + 1):
            timeout_seconds = _timeout_seconds(
                self.graph_config.get("extraction_timeout_seconds"),
                GRAPH_EXTRACTION_TIMEOUT_SECONDS,
            )
            try:
                return (
                    await asyncio.wait_for(
                        _extractor_extract_facts(
                            self.extractor,
                            text,
                            source_id=source_id,
                            source_kind=source_kind,
                            metadata=metadata,
                            existing_graph_context=existing_graph_context,
                        ),
                        timeout=timeout_seconds,
                    ),
                    None,
                )
            except TimeoutError:
                error = f"graph extraction timed out after {timeout_seconds:g}s"
                last_error = TimeoutError(error)
                logger.warning(
                    "Graph extraction timed out after %.3fs for %s (attempt %s/%s)",
                    timeout_seconds,
                    source_id,
                    attempt,
                    _EXTRACTION_MAX_ATTEMPTS,
                )
                if attempt < _EXTRACTION_MAX_ATTEMPTS:
                    await asyncio.sleep(0)
            except Exception as e:
                last_error = e
                if attempt < _EXTRACTION_MAX_ATTEMPTS:
                    logger.debug(
                        "Graph extraction attempt %s/%s failed for %s: %s",
                        attempt,
                        _EXTRACTION_MAX_ATTEMPTS,
                        source_id,
                        e,
                    )
                    await asyncio.sleep(0)
        error = str(last_error or "graph extraction failed")
        logger.warning(
            "Graph extraction skipped after %s attempts for %s: %s",
            _EXTRACTION_MAX_ATTEMPTS,
            source_id,
            error,
        )
        self.task_store.append_event(
            task_id,
            "graph_extraction_skipped",
            {
                "source_id": source_id,
                "source_kind": source_kind,
                "attempts": _EXTRACTION_MAX_ATTEMPTS,
                "error": error[:500],
            },
        )
        return [], error

    async def _existing_graph_context_for_extraction(self, text: str) -> str:
        if not self.graph_config.get("enabled") or not self.graph_config.get("retrieval_enabled"):
            return ""
        query = str(text or "").strip()[:_EXTRACTION_CONTEXT_QUERY_MAX_CHARS]
        if not query:
            return ""
        try:
            timeout_seconds = _timeout_seconds(
                self.graph_config.get("retrieval_timeout_seconds"),
                GRAPH_RETRIEVAL_TIMEOUT_SECONDS,
            )
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self.graph_client.retrieve_context,
                    query=query,
                    source_ids=[],
                    max_facts=max(1, int(self.graph_config.get("max_facts") or 8)),
                    retrieval_depth=_retrieval_depth(
                        self.graph_config.get("retrieval_depth", GRAPH_RETRIEVAL_DEFAULT_DEPTH)
                    ),
                    ranking_policy=_ranking_policy(self.graph_config.get("ranking_policy")),
                    expansion_candidate_limit=_expansion_candidate_limit(
                        self.graph_config.get("expansion_candidate_limit")
                    ),
                    include_entity_types=True,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            timeout_seconds = _timeout_seconds(
                self.graph_config.get("retrieval_timeout_seconds"),
                GRAPH_RETRIEVAL_TIMEOUT_SECONDS,
            )
            logger.warning(
                "Graph extraction context retrieval timed out after %.3fs",
                timeout_seconds,
            )
            return ""
        except Exception as e:
            logger.warning("Graph extraction context retrieval skipped: %s", e)
            return ""

    def _create_llm(self) -> LLM:
        cfg = self.config
        selected_model = str(self.graph_config.get("extraction_model") or "").strip()
        selected_provider = str(self.graph_config.get("extraction_provider") or "").strip()
        model = selected_model or str(cfg.get("model") or "").strip()
        provider = (
            selected_provider
            if selected_model or selected_provider
            else str(cfg.get("model_provider") or "").strip()
        )
        provider_cfg = {}
        providers = cfg.get("providers", {})
        if provider and isinstance(providers, dict):
            provider_entry = providers.get(provider)
            provider_cfg = provider_entry if isinstance(provider_entry, dict) else {}
        entry = _find_active_chat_model_entry(cfg, provider, model)
        raw_model_cfg = entry.get("config", {}) if isinstance(entry, dict) else {}
        model_cfg = raw_model_cfg if isinstance(raw_model_cfg, dict) else {}
        configured_max_tokens = int(
            model_cfg.get("max_tokens") or _GRAPH_EXTRACTION_DEFAULT_MAX_TOKENS
        )
        max_tokens = (
            max(1, configured_max_tokens)
            if selected_model
            else _GRAPH_EXTRACTION_DEFAULT_MAX_TOKENS
        )
        return LLM(
            model=model,
            api_key=str(provider_cfg.get("api_key") or cfg.get("api_key") or ""),
            base_url=provider_cfg.get("base_url") or cfg.get("base_url"),
            api_format=str(provider_cfg.get("api_format") or cfg.get("api_format") or "openai"),
            temperature=float(model_cfg.get("temperature", 0.0)) if selected_model else 0.0,
            max_tokens=max_tokens,
        )


def _graph_config_from_app_config(config: dict[str, Any]) -> dict[str, Any]:
    knowledge = config.get("knowledge", {}) if isinstance(config, dict) else {}
    if not isinstance(knowledge, dict):
        knowledge = {}
    graph = knowledge.get("graph", {})
    if not isinstance(graph, dict):
        graph = {}
    return {
        "enabled": bool(graph.get("enabled", False)),
        "uri": str(graph.get("uri") or "neo4j://localhost:7687"),
        "username": str(graph.get("username") or "neo4j"),
        "password": str(graph.get("password") or ""),
        "database": str(graph.get("database") or "neo4j"),
        "extraction_model": str(graph.get("extraction_model") or ""),
        "extraction_provider": str(graph.get("extraction_provider") or ""),
        "extraction_enabled": bool(graph.get("extraction_enabled", True)),
        "extraction_sources": list(graph.get("extraction_sources") or ["documents", "chat"]),
        "retrieval_enabled": bool(graph.get("retrieval_enabled", True)),
        "retrieval_depth": _retrieval_depth(
            graph.get("retrieval_depth", GRAPH_RETRIEVAL_DEFAULT_DEPTH)
        ),
        "max_facts": max(1, int(graph.get("max_facts") or 8)),
        "expansion_candidate_limit": _expansion_candidate_limit(
            graph.get("expansion_candidate_limit", 40)
        ),
        "multi_hop_expansion_cache_mode": _multi_hop_expansion_cache_mode(
            graph.get("multi_hop_expansion_cache_mode"),
            graph.get("persistent_multi_hop_expansion_cache_enabled"),
        ),
        "multi_hop_expansion_cache_preload_seed_limit": (
            _multi_hop_expansion_cache_preload_seed_limit(
                graph.get("multi_hop_expansion_cache_preload_seed_limit")
            )
        ),
        "multi_hop_expansion_cache_path_limit": _multi_hop_expansion_cache_path_limit(
            graph.get("multi_hop_expansion_cache_path_limit")
        ),
        "multi_hop_expansion_cache_preload_path_limit": (
            _multi_hop_expansion_cache_preload_path_limit(
                graph.get("multi_hop_expansion_cache_preload_path_limit")
            )
        ),
        "ranking_policy": _ranking_policy(graph.get("ranking_policy")),
        "retrieval_timeout_seconds": _timeout_seconds(
            graph.get("retrieval_timeout_seconds"),
            GRAPH_RETRIEVAL_TIMEOUT_SECONDS,
        ),
        "extraction_timeout_seconds": _timeout_seconds(
            graph.get("extraction_timeout_seconds"),
            GRAPH_EXTRACTION_TIMEOUT_SECONDS,
        ),
        "queue_max_size": int(graph.get("queue_max_size") or 1000),
    }


def _multi_hop_expansion_cache_mode(value: Any, legacy_persistent_enabled: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"off", "memory", "persistent"}:
        return mode
    legacy_enabled = _legacy_persistent_cache_enabled(legacy_persistent_enabled)
    if legacy_enabled is False:
        return "memory"
    return "persistent"


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


def _retrieval_depth(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(GRAPH_RETRIEVAL_MAX_DEPTH, parsed))


def _ranking_policy(value: Any) -> str:
    policy = str(value or "hybrid").strip().lower()
    if policy not in {"hybrid", "relevance", "latest"}:
        return "hybrid"
    return policy


def _expansion_candidate_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 40
    return max(1, min(GRAPH_EXPANSION_CANDIDATE_MAX_LIMIT, parsed))


def _timeout_seconds(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if parsed <= 0:
        return float(default)
    return parsed


def _document_extraction_batches(
    chunks: list[dict],
    fallback_id: str,
    *,
    batch_chars: int = GRAPH_EXTRACTION_TUPLE_ALIGNED_BATCH_CHARS,
) -> list[list[dict[str, str]]]:
    batch_chars = max(1, min(int(batch_chars), GRAPH_EXTRACTION_BATCH_CHARS))
    batches: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("content") or "").strip()
        if not text:
            continue
        chunk_id = str(chunk.get("chunk_id") or f"{fallback_id}:{index}").strip()
        item = {"chunk_id": chunk_id, "text": text}
        candidate = [*current, item]
        if current and len(_document_batch_text(candidate)) > batch_chars:
            batches.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def _document_batch_text(batch: list[dict[str, str]]) -> str:
    if len(batch) == 1:
        return batch[0]["text"]
    return "\n\n".join(
        f"[Chunk {index}]\n{item['text']}" for index, item in enumerate(batch, start=1)
    )


def _extraction_text_segments(
    text: str,
    *,
    semantic_chunking: bool = False,
) -> list[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    if not semantic_chunking:
        return _plain_text_extraction_batches(cleaned)

    chunker = RecursiveTextChunker(
        chunk_size=GRAPH_EXTRACTION_SEMANTIC_CHUNK_CHARS,
        chunk_overlap=GRAPH_EXTRACTION_SEMANTIC_CHUNK_OVERLAP_CHARS,
    )
    pieces = chunker.chunk(cleaned)
    if not pieces:
        return []
    pseudo_chunks = [
        {"chunk_id": f"segment-{index}", "content": piece} for index, piece in enumerate(pieces)
    ]
    segments: list[str] = []
    for batch in _document_extraction_batches(pseudo_chunks, "semantic"):
        segments.extend(_plain_text_extraction_batches(_document_batch_text(batch)))
    return segments


def _plain_text_extraction_batches(
    text: str,
    *,
    batch_chars: int = GRAPH_EXTRACTION_TUPLE_ALIGNED_BATCH_CHARS,
    overlap_chars: int = GRAPH_EXTRACTION_BATCH_OVERLAP_CHARS,
) -> list[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    batch_chars = max(1, min(int(batch_chars), GRAPH_EXTRACTION_BATCH_CHARS))
    if len(cleaned) <= batch_chars:
        return [cleaned]

    overlap_chars = max(0, min(overlap_chars, batch_chars - 1))
    batches: list[str] = []
    start = 0
    text_len = len(cleaned)
    while start < text_len:
        end = min(text_len, start + batch_chars)
        if end < text_len:
            window = cleaned[start:end]
            for separator in ("\n\n", "\n", "\u3002", ". ", "! ", "? ", " "):
                idx = window.rfind(separator)
                if idx > 0:
                    end = start + idx + (len(separator) if separator.strip() else 0)
                    break
        piece = cleaned[start:end].strip()
        if piece:
            batches.append(piece)
        if end >= text_len:
            break
        next_start = end - overlap_chars if overlap_chars else end
        if next_start <= start:
            next_start = end
        start = next_start
    return batches


def _chat_source_id(metadata: dict[str, Any]) -> str:
    session_id = str(metadata.get("session_id") or "").strip()
    turn_id = str(metadata.get("turn_id") or metadata.get("message_id") or "").strip()
    if session_id and turn_id:
        return f"chat:{session_id}:{turn_id}"
    if session_id:
        return f"chat:{session_id}"
    platform = str(metadata.get("platform") or "").strip()
    return f"chat:{platform}" if platform else "chat"


def _find_active_chat_model_entry(config: dict[str, Any], provider: str, model: str) -> dict | None:
    for entry in config.get("active_models", []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("provider") or "") == provider and str(entry.get("model") or "") == model:
            return entry
    return None


async def _extractor_extract_facts(
    extractor: Any,
    text: str,
    *,
    source_id: str,
    source_kind: str,
    metadata: dict[str, Any],
    existing_graph_context: str,
) -> list[dict]:
    method = extractor.extract_facts
    if _accepts_existing_graph_context(method):
        return cast(
            list[dict],
            await method(
                text,
                source_id=source_id,
                source_kind=source_kind,
                metadata=metadata,
                existing_graph_context=existing_graph_context,
            ),
        )
    return cast(
        list[dict],
        await method(
            text,
            source_id=source_id,
            source_kind=source_kind,
            metadata=metadata,
        ),
    )


def _accepts_existing_graph_context(method: Any) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "existing_graph_context"
        or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _accepts_keyword(method: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _timing_float(timings: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(timings.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _timing_int(timings: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(timings.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _timing_bool(timings: dict[str, Any], key: str, default: bool = False) -> bool:
    value = timings.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _chat_turn_text(user_text: str, assistant_text: str) -> str:
    return (
        "User message:\n"
        + str(user_text or "").strip()
        + "\n\nAssistant response:\n"
        + str(assistant_text or "").strip()
    )
