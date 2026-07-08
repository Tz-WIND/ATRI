"""State-driven document indexing for ATRI knowledge bases."""

from __future__ import annotations

import asyncio
from typing import Any

from core import logger
from core.knowledge.chunking import RecursiveTextChunker
from core.knowledge.store import KnowledgeStore

INDEX_TYPE_VECTOR_FULLTEXT = "vector_fulltext"
INDEX_TYPE_GRAPH = "graph"

_INDEX_TYPE_ORDER = {
    INDEX_TYPE_VECTOR_FULLTEXT: 0,
    INDEX_TYPE_GRAPH: 1,
}


class DocumentIndexExecutor:
    """Build a claimed document index using KnowledgeBaseManager services."""

    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.store: KnowledgeStore = manager.store

    async def execute(self, claimed_index: dict[str, Any]) -> bool:
        index_id = str(claimed_index["index_id"])
        index_type = str(claimed_index["index_type"])
        action = str(claimed_index.get("action") or "")
        target_version = claimed_index.get("target_version")
        try:
            if action == "delete":
                return self._delete_index(claimed_index)
            if target_version is None:
                raise ValueError("claimed index is missing target_version")
            if index_type == INDEX_TYPE_VECTOR_FULLTEXT:
                result = await self._build_vector_fulltext(claimed_index)
            elif index_type == INDEX_TYPE_GRAPH:
                result = await self._build_graph(claimed_index)
                return self.store.queue_document_index(
                    doc_id=str(claimed_index["doc_id"]),
                    index_type=index_type,
                    target_version=int(target_version),
                    result=result,
                )
            else:
                raise ValueError(f"unknown document index type: {index_type}")
            return self.store.complete_document_index(
                doc_id=str(claimed_index["doc_id"]),
                index_type=index_type,
                target_version=int(target_version),
                result=result,
            )
        except Exception as e:
            self.store.fail_document_index(index_id=index_id, error=str(e))
            logger.warning(
                "Knowledge document index failed: doc_id=%s index_type=%s error=%s",
                claimed_index.get("doc_id"),
                index_type,
                e,
            )
            return False

    async def _build_vector_fulltext(self, claimed_index: dict[str, Any]) -> dict[str, Any]:
        kb_id = str(claimed_index["kb_id"])
        doc_id = str(claimed_index["doc_id"])
        kb = self.store.get_kb(kb_id)
        doc = self.store.get_document(doc_id)
        payload = self.store.get_document_payload(doc_id)
        if not kb:
            raise ValueError("knowledge base not found")
        if not doc:
            raise ValueError("document not found")
        if not payload:
            raise ValueError("document payload not found")

        chunks = RecursiveTextChunker(
            chunk_size=int(kb["chunk_size"]),
            chunk_overlap=int(kb["chunk_overlap"]),
        ).chunk(str(payload["content"]))
        if not chunks:
            raise ValueError("document content is empty")

        selection = self.manager._selection_from_kb(kb)
        vectors = await self.manager.embedding_client.embed_texts(selection, chunks)
        if len(vectors) != len(chunks):
            raise ValueError("embedding result count does not match chunk count")
        kb = self.manager._ensure_kb_embedding_dimensions(kb_id, kb, vectors[0])
        for vector in vectors:
            if len(vector) != int(kb["embedding_dimensions"]):
                expected = int(kb["embedding_dimensions"])
                actual = len(vector)
                raise ValueError(
                    "embedding vector dimension does not match knowledge base "
                    f"(expected {expected}, got {actual} from "
                    f"{kb['embedding_provider']}/{kb['embedding_model']}; "
                    "recreate the knowledge base with matching embedding dimensions "
                    "or align the model pool dimensions setting)"
                )

        self.store.replace_chunks(kb_id, doc_id, list(zip(chunks, vectors, strict=True)))
        return {"chunk_count": len(chunks)}

    async def _build_graph(self, claimed_index: dict[str, Any]) -> dict[str, Any]:
        graph_manager = getattr(self.manager, "graph_manager", None)
        if graph_manager is None:
            raise RuntimeError("graph manager is not available")
        doc_id = str(claimed_index["doc_id"])
        doc = self.store.get_document(doc_id)
        if not doc:
            raise ValueError("document not found")
        chunk_count = int(doc.get("chunk_count") or 0)
        chunks = self.store.list_chunks(doc_id, offset=0, limit=max(1, chunk_count))
        if not chunks:
            raise RuntimeError("graph index is waiting for vector_fulltext chunks")
        task_id = graph_manager.enqueue_document(
            kb_id=str(claimed_index["kb_id"]),
            doc_id=doc_id,
            doc_name=str(doc["doc_name"]),
            chunks=chunks,
        )
        if task_id is None:
            raise RuntimeError("graph document extraction enqueue skipped")
        return {"task_id": task_id, "chunk_count": len(chunks)}

    def _delete_index(self, claimed_index: dict[str, Any]) -> bool:
        index_type = str(claimed_index["index_type"])
        if index_type == INDEX_TYPE_VECTOR_FULLTEXT:
            self.store.replace_chunks(
                str(claimed_index["kb_id"]),
                str(claimed_index["doc_id"]),
                [],
            )
        return self.store.delete_document_index(
            doc_id=str(claimed_index["doc_id"]),
            index_type=index_type,
        )


class DocumentIndexReconciler:
    """Claim and execute pending document indexes."""

    def __init__(
        self,
        store: KnowledgeStore,
        executor: DocumentIndexExecutor,
        *,
        stale_timeout_seconds: float = 900.0,
    ) -> None:
        self.store = store
        self.executor = executor
        self.stale_timeout_seconds = max(1.0, float(stale_timeout_seconds))

    async def reconcile_once(self, *, limit: int = 20) -> int:
        self.store.reset_stale_document_indexes(timeout_seconds=self.stale_timeout_seconds)
        candidates = self.store.list_indexes_needing_reconciliation(limit=limit)
        claimed = []
        for candidate in candidates:
            claim = self.store.claim_document_index(str(candidate["index_id"]))
            if claim is not None:
                claimed.append(claim)
        claimed.sort(
            key=lambda item: (
                str(item["doc_id"]),
                _INDEX_TYPE_ORDER.get(str(item["index_type"]), 99),
                str(item["index_type"]),
            )
        )
        for item in claimed:
            await self.executor.execute(item)
        return len(claimed)


class LocalIndexWorker:
    """Small asyncio reconciler loop for local async indexing."""

    def __init__(
        self,
        reconciler: DocumentIndexReconciler,
        *,
        interval_seconds: float = 5.0,
        batch_size: int = 20,
    ) -> None:
        self.reconciler = reconciler
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.batch_size = max(1, int(batch_size))
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(self._run())

    def update_settings(self, *, interval_seconds: float, batch_size: int) -> None:
        self.interval_seconds = max(0.1, float(interval_seconds))
        self.batch_size = max(1, int(batch_size))

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done() and not self._stop_event.is_set()

    async def close(self) -> None:
        self.stop()
        if self._task is None:
            return
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.reconciler.reconcile_once(limit=self.batch_size)
            except Exception:
                logger.warning("Knowledge index reconciler loop failed", exc_info=True)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )
            except TimeoutError:
                pass
