"""Background queue for non-blocking graph tuple extraction."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import logger
from core.agent.llm import LLM
from core.knowledge.extraction import GraphTupleExtractor
from core.knowledge.graph import Neo4jGraphClient
from core.runtime import TaskStore

_DOCUMENT_EXTRACTION_BATCH_CHARS = 10_000
_EXTRACTION_MAX_ATTEMPTS = 3


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

    async def retrieve_context(
        self,
        *,
        query: str,
        source_ids: list[str] | None = None,
        max_facts: int = 8,
        retrieval_depth: int | None = None,
    ) -> str:
        if not self.graph_config.get("enabled") or not self.graph_config.get("retrieval_enabled"):
            return ""
        depth = _retrieval_depth(
            retrieval_depth
            if retrieval_depth is not None
            else self.graph_config.get("retrieval_depth", 1)
        )
        try:
            return await asyncio.to_thread(
                self.graph_client.retrieve_context,
                query=query,
                source_ids=source_ids or [],
                max_facts=max_facts,
                retrieval_depth=depth,
            )
        except Exception as e:
            logger.warning("Graph knowledge retrieval skipped: %s", e)
            return ""

    def _can_enqueue(self, source: str) -> bool:
        if self._closing or self.queue is None:
            return False
        if not self.graph_config.get("enabled") or not self.graph_config.get(
            "extraction_enabled",
            True,
        ):
            return False
        if int(self.graph_config.get("queue_max_size") or 0) < 1:
            return False
        sources = self.graph_config.get("extraction_sources", ["documents", "chat"])
        if source not in sources:
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
            if job.source_kind == "document":
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
                    extracted, error = await self._extract_facts_with_retries(
                        task_id=job.task_id,
                        text=_document_batch_text(batch),
                        source_id=source_id,
                        source_kind=job.source_kind,
                        metadata=metadata,
                    )
                    if error:
                        failed_extractions.append({"source_id": source_id, "error": error})
                        continue
                    facts.extend(extracted)
            else:
                facts, error = await self._extract_facts_with_retries(
                    task_id=job.task_id,
                    text=job.text,
                    source_id=_chat_source_id(job.metadata),
                    source_kind=job.source_kind,
                    metadata=job.metadata,
                )
                if error:
                    failed_extractions.append(
                        {"source_id": _chat_source_id(job.metadata), "error": error}
                    )
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

    async def _extract_facts_with_retries(
        self,
        text: str,
        *,
        task_id: str,
        source_id: str,
        source_kind: str,
        metadata: dict[str, Any],
    ) -> tuple[list[dict], str | None]:
        last_error: Exception | None = None
        for attempt in range(1, _EXTRACTION_MAX_ATTEMPTS + 1):
            try:
                return (
                    await self.extractor.extract_facts(
                        text,
                        source_id=source_id,
                        source_kind=source_kind,
                        metadata=metadata,
                    ),
                    None,
                )
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
        model_cfg = {}
        if selected_model:
            entry = _find_active_chat_model_entry(cfg, provider, model)
            model_cfg = entry.get("config", {}) if isinstance(entry, dict) else {}
            if not isinstance(model_cfg, dict):
                model_cfg = {}
        return LLM(
            model=model,
            api_key=str(provider_cfg.get("api_key") or cfg.get("api_key") or ""),
            base_url=provider_cfg.get("base_url") or cfg.get("base_url"),
            api_format=str(provider_cfg.get("api_format") or cfg.get("api_format") or "openai"),
            temperature=float(model_cfg.get("temperature", 0.0)),
            max_tokens=max(1, int(model_cfg.get("max_tokens") or 1024)),
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
        "retrieval_depth": _retrieval_depth(graph.get("retrieval_depth", 1)),
        "max_facts": max(1, int(graph.get("max_facts") or 8)),
        "queue_max_size": int(graph.get("queue_max_size") or 1000),
    }


def _retrieval_depth(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(3, parsed))


def _document_extraction_batches(
    chunks: list[dict],
    fallback_id: str,
) -> list[list[dict[str, str]]]:
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
        if current and len(_document_batch_text(candidate)) > _DOCUMENT_EXTRACTION_BATCH_CHARS:
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


def _chat_turn_text(user_text: str, assistant_text: str) -> str:
    return (
        "User message:\n"
        + str(user_text or "").strip()
        + "\n\nAssistant response:\n"
        + str(assistant_text or "").strip()
    )
