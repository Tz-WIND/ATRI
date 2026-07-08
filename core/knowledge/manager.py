"""Business facade for ATRI knowledge bases."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

from core.document_text import extract_document_text
from core.knowledge.chunking import RecursiveTextChunker
from core.knowledge.embedding import (
    EmbeddingClient,
    ModelSelection,
    OpenAIEmbeddingClient,
    resolve_model_selection,
)
from core.knowledge.indexing import (
    INDEX_TYPE_GRAPH,
    INDEX_TYPE_VECTOR_FULLTEXT,
    DocumentIndexExecutor,
    DocumentIndexReconciler,
    LocalIndexWorker,
)
from core.knowledge.rerank import OpenAIRerankClient, RerankClient
from core.knowledge.retrieval import HybridRetriever
from core.knowledge.store import DEFAULT_EMBEDDING_CACHE_MAX_SIZE, KnowledgeStore
from core.knowledge.vector_backend import DEFAULT_HNSW_INDEX_DIR, delete_hnsw_sidecar_files


class KnowledgeBaseManager:
    """Coordinate knowledge base storage, ingestion, model validation, and retrieval."""

    def __init__(
        self,
        db_path: str | Path = "data/knowledge/knowledge.db",
        config: dict[str, Any] | None = None,
        embedding_client: EmbeddingClient | None = None,
        rerank_client: RerankClient | None = None,
        graph_manager: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.config = config or {}
        self.embedding_client = embedding_client or OpenAIEmbeddingClient()
        self.rerank_client = rerank_client or OpenAIRerankClient()
        self.graph_manager = graph_manager
        self.store = KnowledgeStore(
            self.db_path,
            embedding_cache_max_size=_embedding_cache_max_size_from_config(self.config),
        )
        self.retriever: HybridRetriever | None = None
        self.index_reconciler: DocumentIndexReconciler | None = None
        self.index_worker: LocalIndexWorker | None = None
        self._stopping_index_workers: list[LocalIndexWorker] = []

    async def initialize(self) -> None:
        self.store.initialize()
        self.retriever = HybridRetriever(
            self.store,
            self.rerank_client,
            vector_config=self.config,
        )
        self.index_reconciler = DocumentIndexReconciler(
            self.store,
            DocumentIndexExecutor(self),
            stale_timeout_seconds=_indexing_stale_timeout_seconds(self.config),
        )
        if _async_indexing_enabled(self.config) and _indexing_auto_start(self.config):
            self.index_worker = LocalIndexWorker(
                self.index_reconciler,
                interval_seconds=_indexing_interval_seconds(self.config),
                batch_size=_indexing_batch_size(self.config),
            )
            self.index_worker.start()

    async def close(self) -> None:
        workers = [*self._stopping_index_workers]
        if self.index_worker is not None:
            workers.append(self.index_worker)
            self.index_worker = None
        for worker in workers:
            await worker.close()
        self._stopping_index_workers.clear()
        self.store.close()

    def update_config(self, config: dict[str, Any]) -> None:
        merged = dict(self.config)
        merged.update(config)
        self.config = merged
        self.store.set_embedding_cache_max_size(_embedding_cache_max_size_from_config(self.config))
        if self.index_reconciler is not None:
            self.index_reconciler.stale_timeout_seconds = _indexing_stale_timeout_seconds(
                self.config
            )
            self._sync_index_worker_config()
        if self.retriever is not None:
            self.retriever = HybridRetriever(
                self.store,
                self.rerank_client,
                vector_config=self.config,
            )

    async def create_knowledge_base(
        self,
        *,
        name: str,
        description: str = "",
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        rerank_provider: str | None = None,
        rerank_model: str | None = None,
        chunk_size: int = 800,
        chunk_overlap: int = 120,
        top_k_dense: int = 30,
        top_k_sparse: int = 30,
        top_m_final: int = 5,
    ) -> dict:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("knowledge base name is required")
        chunk_size = _int_at_least(chunk_size, "chunk_size", 1)
        chunk_overlap = _int_at_least(chunk_overlap, "chunk_overlap", 0)
        top_k_dense = _int_at_least(top_k_dense, "top_k_dense", 1)
        top_k_sparse = _int_at_least(top_k_sparse, "top_k_sparse", 1)
        top_m_final = _int_at_least(top_m_final, "top_m_final", 1)
        embedding = self._resolve_embedding(embedding_provider, embedding_model)
        rerank = self._resolve_rerank(rerank_provider, rerank_model)
        dimensions, embedding_config = await self._resolve_embedding_dimensions(embedding)
        RecursiveTextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return self.store.create_kb(
            {
                "name": cleaned_name,
                "description": description,
                "embedding_provider": embedding.provider,
                "embedding_model": embedding.model,
                "embedding_config": embedding_config,
                "embedding_dimensions": dimensions,
                "rerank_provider": rerank.provider if rerank else "",
                "rerank_model": rerank.model if rerank else "",
                "rerank_config": rerank.config if rerank else {},
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "top_k_dense": top_k_dense,
                "top_k_sparse": top_k_sparse,
                "top_m_final": top_m_final,
            }
        )

    async def update_knowledge_base(self, kb_id: str, **changes: Any) -> dict:
        kb = self._require_kb(kb_id)
        update: dict[str, Any] = {}
        if "name" in changes and changes["name"] is not None:
            name = str(changes["name"]).strip()
            if not name:
                raise ValueError("knowledge base name is required")
            update["name"] = name
        if "description" in changes and changes["description"] is not None:
            update["description"] = str(changes["description"])
        if "chunk_size" in changes and changes["chunk_size"] is not None:
            update["chunk_size"] = _int_at_least(changes["chunk_size"], "chunk_size", 1)
        if "chunk_overlap" in changes and changes["chunk_overlap"] is not None:
            update["chunk_overlap"] = _int_at_least(changes["chunk_overlap"], "chunk_overlap", 0)
        for key in ("top_k_dense", "top_k_sparse", "top_m_final"):
            if key in changes and changes[key] is not None:
                update[key] = _int_at_least(changes[key], key, 1)
        if "chunk_size" in update or "chunk_overlap" in update:
            RecursiveTextChunker(
                chunk_size=int(update.get("chunk_size", kb["chunk_size"])),
                chunk_overlap=int(update.get("chunk_overlap", kb["chunk_overlap"])),
            )

        embedding_model = changes.get("embedding_model")
        embedding_provider = changes.get("embedding_provider")
        if embedding_model or embedding_provider:
            if kb["chunk_count"] > 0:
                raise ValueError("cannot change embedding model after documents have been indexed")
            embedding = self._resolve_embedding(embedding_provider, embedding_model)
            dimensions, embedding_config = await self._resolve_embedding_dimensions(embedding)
            update.update(
                {
                    "embedding_provider": embedding.provider,
                    "embedding_model": embedding.model,
                    "embedding_config": embedding_config,
                    "embedding_dimensions": dimensions,
                }
            )

        if "rerank_model" in changes or "rerank_provider" in changes:
            rerank = self._resolve_rerank(
                changes.get("rerank_provider"), changes.get("rerank_model")
            )
            update["rerank_provider"] = rerank.provider if rerank else ""
            update["rerank_model"] = rerank.model if rerank else ""
            update["rerank_config"] = rerank.config if rerank else {}

        updated = self.store.update_kb(kb_id, update)
        if not updated:
            raise ValueError("knowledge base not found")
        return updated

    async def list_knowledge_bases(self) -> list[dict]:
        return self.store.list_kbs()

    async def get_knowledge_base(self, kb_id: str) -> dict:
        return self._require_kb(kb_id)

    async def delete_knowledge_base(self, kb_id: str) -> bool:
        deleted = self.store.delete_kb(kb_id)
        if deleted:
            self._delete_vector_index(kb_id)
        return deleted

    async def import_document(
        self,
        kb_id: str,
        *,
        file_name: str,
        content: str,
        file_type: str | None = None,
        source: str = "import",
    ) -> dict:
        if _async_indexing_enabled(self.config):
            return await self._queue_document_import(
                kb_id,
                file_name=file_name,
                content=content,
                file_type=file_type,
                source=source,
            )

        task = self.store.create_task("import", kb_id=kb_id, status="processing")
        try:
            kb = self._require_kb(kb_id)
            chunks = RecursiveTextChunker(
                chunk_size=int(kb["chunk_size"]),
                chunk_overlap=int(kb["chunk_overlap"]),
            ).chunk(content)
            if not chunks:
                raise ValueError("document content is empty")
            selection = self._selection_from_kb(kb)
            vectors = await self.embedding_client.embed_texts(selection, chunks)
            if len(vectors) != len(chunks):
                raise ValueError("embedding result count does not match chunk count")
            kb = self._ensure_kb_embedding_dimensions(kb_id, kb, vectors[0])
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
            doc = self.store.create_document(
                kb_id=kb_id,
                file_name=file_name,
                file_type=file_type or _file_type(file_name),
                file_size=len(content.encode("utf-8")),
                source=source,
            )
            self.store.save_document_payload(doc["doc_id"], content)
            self.store.add_chunks(kb_id, doc["doc_id"], list(zip(chunks, vectors, strict=True)))
            self.store.record_document_index_active(
                kb_id=kb_id,
                doc_id=doc["doc_id"],
                index_type=INDEX_TYPE_VECTOR_FULLTEXT,
                result={"chunk_count": len(chunks)},
            )
            graph_task_id = self._enqueue_graph_document(
                kb_id,
                doc["doc_id"],
                file_name,
                len(chunks),
            )
            if graph_task_id is not None:
                self.store.record_document_index_queued(
                    kb_id=kb_id,
                    doc_id=doc["doc_id"],
                    index_type=INDEX_TYPE_GRAPH,
                    result={"task_id": graph_task_id, "chunk_count": len(chunks)},
                )
            result = {
                "uploaded": [self.store.get_document(doc["doc_id"])],
                "failed": [],
                "success_count": 1,
                "failed_count": 0,
            }
            return (
                self.store.update_task(task["task_id"], status="completed", result=result) or task
            )
        except Exception as e:
            failed = self.store.update_task(task["task_id"], status="failed", error=str(e))
            if failed:
                failed["error"] = str(e)
            raise

    async def upload_document(
        self,
        kb_id: str,
        *,
        file_name: str,
        content: bytes,
        file_type: str | None = None,
    ) -> dict:
        text = extract_document_text(file_name, content)
        return await self.import_document(
            kb_id,
            file_name=file_name,
            content=text,
            file_type=file_type,
            source="upload",
        )

    async def list_documents(self, kb_id: str) -> list[dict]:
        self._require_kb(kb_id)
        return self.store.list_documents(kb_id)

    async def get_index_status(self, kb_id: str) -> dict[str, Any]:
        kb = self._require_kb(kb_id)
        documents = [self._document_index_status(doc) for doc in self.store.list_documents(kb_id)]
        summary = {
            "document_count": len(documents),
            "index_count": 0,
            "active": 0,
            "pending": 0,
            "creating": 0,
            "queued": 0,
            "failed": 0,
            "untracked": 0,
            "source_missing": 0,
        }
        for document in documents:
            if document["source_missing"]:
                summary["source_missing"] += 1
            for index in document["index_statuses"]:
                summary["index_count"] += 1
                status = str(index["status"])
                if status in summary:
                    summary[status] += 1
        return {
            "kb_id": kb_id,
            "kb_name": kb["name"],
            "indexing": {
                "mode": "async" if _async_indexing_enabled(self.config) else "sync",
                "auto_start": _indexing_auto_start(self.config),
                "worker_running": bool(self.index_worker is not None and self.index_worker.running),
            },
            "summary": summary,
            "documents": documents,
        }

    async def rebuild_document_indexes(
        self,
        *,
        kb_id: str,
        doc_id: str | None = None,
        failed_only: bool = False,
    ) -> dict[str, Any]:
        self._require_kb(kb_id)
        documents = self.store.list_documents(kb_id)
        if doc_id is not None:
            documents = [doc for doc in documents if doc["doc_id"] == doc_id]
            if not documents:
                raise ValueError("document not found")

        queued = 0
        skipped: list[dict[str, str]] = []
        targets: dict[str, list[str]] = {}
        for doc in documents:
            current_doc_id = str(doc["doc_id"])
            if self.store.get_document_payload(current_doc_id) is None:
                skipped.append({"doc_id": current_doc_id, "reason": "source_missing"})
                continue
            existing = self.store.list_document_indexes(current_doc_id)
            index_types = self._rebuild_index_types(existing, failed_only=failed_only)
            if not index_types:
                continue
            self.store.request_document_indexes(
                kb_id=kb_id,
                doc_id=current_doc_id,
                index_types=index_types,
            )
            queued += len(index_types)
            targets[current_doc_id] = index_types

        reconciled = 0
        if queued and not _async_indexing_enabled(self.config):
            reconciled = await self._reconcile_requested_indexes(targets)

        return {
            "ok": True,
            "kb_id": kb_id,
            "doc_id": doc_id or "",
            "queued": queued,
            "reconciled": reconciled,
            "skipped": skipped,
            "status": await self.get_index_status(kb_id),
        }

    async def delete_document(self, doc_id: str) -> bool:
        return self.store.delete_document(doc_id)

    async def list_chunks(self, doc_id: str, offset: int = 0, limit: int = 100) -> list[dict]:
        offset = _int_at_least(offset, "offset", 0)
        limit = _int_at_least(limit, "limit", 1)
        return self.store.list_chunks(doc_id, offset=offset, limit=limit)

    async def delete_chunk(self, chunk_id: str) -> bool:
        return self.store.delete_chunk(chunk_id)

    async def retrieve(
        self,
        *,
        query: str,
        kb_ids: list[str] | None = None,
        kb_names: list[str] | None = None,
        top_k: int = 5,
        timings: dict[str, Any] | None = None,
    ) -> dict:
        started_at = time.perf_counter()
        if self.retriever is None:
            raise RuntimeError("knowledge manager is not initialized")
        top_k = _int_at_least(top_k, "top_k", 1)
        kb_records = self._resolve_retrieval_kbs(kb_ids or [], kb_names or [])
        kb_records = [self._with_rerank_provider_config(kb) for kb in kb_records]
        query_vectors = {}
        embed_ms = 0.0
        for kb in kb_records:
            selection = self._selection_from_kb(kb)
            embed_started_at = time.perf_counter()
            vectors = await self.embedding_client.embed_texts(selection, [query])
            embed_ms += (time.perf_counter() - embed_started_at) * 1000
            query_vectors[kb["kb_id"]] = vectors[0]
        _set_timing(timings, "vector_embed_ms", embed_ms)
        hits = await self.retriever.retrieve(
            query=query,
            kb_records=kb_records,
            query_vectors=query_vectors,
            top_k=top_k,
            timings=timings,
        )
        format_started_at = time.perf_counter()
        results = [hit.to_dict() for hit in hits]
        context_text = format_context(results)
        _record_timing(timings, "vector_format_ms", format_started_at)
        _record_timing(timings, "vector_total_ms", started_at)
        return {
            "query": query,
            "results": results,
            "total": len(results),
            "context_text": context_text,
        }

    async def get_task(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError("task not found")
        return task

    async def reconcile_indexes_once(self, *, limit: int | None = None) -> int:
        if self.index_reconciler is None:
            raise RuntimeError("knowledge manager is not initialized")
        return await self.index_reconciler.reconcile_once(
            limit=limit or _indexing_batch_size(self.config)
        )

    def _resolve_embedding(self, provider: str | None, model: str | None) -> ModelSelection:
        selection = resolve_model_selection(
            config=self.config,
            pool_key="active_embedding_models",
            provider=provider,
            model=model,
            required=True,
            missing_message="embedding model is not enabled in the embedding model pool",
        )
        if selection is None:
            raise ValueError("embedding model is not enabled in the embedding model pool")
        return selection

    def _resolve_rerank(self, provider: str | None, model: str | None) -> ModelSelection | None:
        if not model and not provider:
            return None
        return resolve_model_selection(
            config=self.config,
            pool_key="active_rerank_models",
            provider=provider,
            model=model,
            required=True,
            missing_message="rerank model is not enabled in the rerank model pool",
        )

    async def _resolve_embedding_dimensions(
        self, embedding: ModelSelection
    ) -> tuple[int, dict[str, Any]]:
        vectors = await self.embedding_client.embed_texts(embedding, ["dimension probe"])
        probed = len(vectors[0])
        configured = embedding.dimensions
        if configured and configured != probed:
            from core import logger

            logger.warning(
                "Embedding model %s/%s config dimensions=%s but API returned %s; "
                "using probed dimensions",
                embedding.provider,
                embedding.model,
                configured,
                probed,
            )
        config = dict(embedding.config)
        config["dimensions"] = probed
        return probed, config

    def _ensure_kb_embedding_dimensions(
        self, kb_id: str, kb: dict, sample_vector: list[float]
    ) -> dict:
        probed = len(sample_vector)
        stored = int(kb.get("embedding_dimensions") or 0)
        if probed == stored:
            return kb
        if int(kb.get("chunk_count") or 0) > 0:
            return kb
        config = dict(kb.get("embedding_config") or {})
        config["dimensions"] = probed
        updated = self.store.update_kb(
            kb_id,
            {
                "embedding_dimensions": probed,
                "embedding_config": config,
            },
        )
        return updated or kb

    def _selection_from_kb(self, kb: dict) -> ModelSelection:
        providers = self.config.get("providers", {})
        provider_config = (
            providers.get(kb["embedding_provider"], {}) if isinstance(providers, dict) else {}
        )
        config = dict(kb.get("embedding_config") or {})
        stored_dims = int(kb.get("embedding_dimensions") or 0)
        if stored_dims and int(config.get("dimensions") or 0) != stored_dims:
            config["dimensions"] = stored_dims
        return ModelSelection(
            provider=kb["embedding_provider"],
            model=kb["embedding_model"],
            config=config,
            provider_config=dict(provider_config if isinstance(provider_config, dict) else {}),
        )

    def _resolve_retrieval_kbs(self, kb_ids: list[str], kb_names: list[str]) -> list[dict]:
        all_kbs = self.store.list_kbs()
        selected = []
        for kb in all_kbs:
            if kb_ids and kb["kb_id"] in kb_ids:
                selected.append(kb)
            elif kb_names and kb["name"] in kb_names:
                selected.append(kb)
        return selected

    def _with_rerank_provider_config(self, kb: dict) -> dict:
        if not kb.get("rerank_provider"):
            return kb
        providers = self.config.get("providers", {})
        provider_config = (
            providers.get(kb["rerank_provider"], {}) if isinstance(providers, dict) else {}
        )
        enriched = dict(kb)
        enriched["rerank_provider_config"] = dict(
            provider_config if isinstance(provider_config, dict) else {}
        )
        return enriched

    def _require_kb(self, kb_id: str) -> dict:
        kb = self.store.get_kb(kb_id)
        if not kb:
            raise ValueError("knowledge base not found")
        return kb

    def _sync_index_worker_config(self) -> None:
        self._stopping_index_workers = [
            worker for worker in self._stopping_index_workers if worker.running
        ]
        if not (_async_indexing_enabled(self.config) and _indexing_auto_start(self.config)):
            self._stop_index_worker_nowait()
            return
        if self.index_reconciler is None:
            return
        interval_seconds = _indexing_interval_seconds(self.config)
        batch_size = _indexing_batch_size(self.config)
        if self.index_worker is None or not self.index_worker.running:
            self.index_worker = LocalIndexWorker(
                self.index_reconciler,
                interval_seconds=interval_seconds,
                batch_size=batch_size,
            )
            self.index_worker.start()
            return
        self.index_worker.update_settings(
            interval_seconds=interval_seconds,
            batch_size=batch_size,
        )

    def _stop_index_worker_nowait(self) -> None:
        if self.index_worker is None:
            return
        self.index_worker.stop()
        self._stopping_index_workers.append(self.index_worker)
        self.index_worker = None

    async def _reconcile_requested_indexes(self, targets: dict[str, list[str]]) -> int:
        if self.index_reconciler is None:
            raise RuntimeError("knowledge manager is not initialized")
        self.store.reset_stale_document_indexes(
            timeout_seconds=self.index_reconciler.stale_timeout_seconds
        )
        claimed = []
        for doc_id, index_types in targets.items():
            candidates = self.store.list_indexes_needing_reconciliation_for_document(
                doc_id=doc_id,
                index_types=index_types,
            )
            for candidate in candidates:
                claim = self.store.claim_document_index(str(candidate["index_id"]))
                if claim is not None:
                    claimed.append(claim)
        claimed.sort(key=_index_claim_sort_key)
        for item in claimed:
            await self.index_reconciler.executor.execute(item)
        return len(claimed)

    def _document_index_status(self, doc: dict[str, Any]) -> dict[str, Any]:
        doc_id = str(doc["doc_id"])
        existing_indexes = self.store.list_document_indexes(doc_id)
        existing_by_type = {str(item["index_type"]): item for item in existing_indexes}
        source_missing = self.store.get_document_payload(doc_id) is None
        index_statuses = []
        for index_type in self._status_index_types(existing_indexes):
            existing = existing_by_type.get(index_type)
            if existing is None:
                index_statuses.append(
                    {
                        "doc_id": doc_id,
                        "index_type": index_type,
                        "status": "untracked",
                        "version": 0,
                        "observed_version": 0,
                        "error": "source document payload is missing" if source_missing else "",
                        "tracked": False,
                        "rebuildable": not source_missing,
                    }
                )
                continue
            index_statuses.append(
                {
                    **existing,
                    "tracked": True,
                    "rebuildable": not source_missing,
                }
            )
        return {
            "doc_id": doc_id,
            "doc_name": doc["doc_name"],
            "file_type": doc["file_type"],
            "chunk_count": int(doc.get("chunk_count") or 0),
            "source_missing": source_missing,
            "rebuildable": not source_missing,
            "aggregate_status": _aggregate_index_status(index_statuses),
            "index_statuses": index_statuses,
        }

    def _status_index_types(self, existing_indexes: list[dict]) -> list[str]:
        index_types = [INDEX_TYPE_VECTOR_FULLTEXT]
        if self._graph_document_index_enabled() or any(
            item.get("index_type") == INDEX_TYPE_GRAPH for item in existing_indexes
        ):
            index_types.append(INDEX_TYPE_GRAPH)
        for item in existing_indexes:
            index_type = str(item.get("index_type") or "").strip()
            if index_type and index_type not in index_types:
                index_types.append(index_type)
        return index_types

    def _rebuild_index_types(self, existing_indexes: list[dict], *, failed_only: bool) -> list[str]:
        if failed_only:
            return [
                str(item["index_type"])
                for item in existing_indexes
                if str(item.get("status") or "") == "failed"
            ]
        index_types = [INDEX_TYPE_VECTOR_FULLTEXT]
        if self._graph_document_index_enabled():
            index_types.append(INDEX_TYPE_GRAPH)
        return index_types

    async def _queue_document_import(
        self,
        kb_id: str,
        *,
        file_name: str,
        content: str,
        file_type: str | None,
        source: str,
    ) -> dict:
        task = self.store.create_task("import", kb_id=kb_id, status="processing")
        try:
            kb = self._require_kb(kb_id)
            chunks = RecursiveTextChunker(
                chunk_size=int(kb["chunk_size"]),
                chunk_overlap=int(kb["chunk_overlap"]),
            ).chunk(content)
            if not chunks:
                raise ValueError("document content is empty")
            doc = self.store.create_document(
                kb_id=kb_id,
                file_name=file_name,
                file_type=file_type or _file_type(file_name),
                file_size=len(content.encode("utf-8")),
                source=source,
            )
            self.store.save_document_payload(doc["doc_id"], content)
            index_types = [INDEX_TYPE_VECTOR_FULLTEXT]
            if self._graph_document_index_enabled():
                index_types.append(INDEX_TYPE_GRAPH)
            index_statuses = self.store.request_document_indexes(
                kb_id=kb_id,
                doc_id=doc["doc_id"],
                index_types=index_types,
            )
            result = {
                "uploaded": [self.store.get_document(doc["doc_id"])],
                "failed": [],
                "success_count": 1,
                "failed_count": 0,
                "index_statuses": index_statuses,
            }
            return self.store.update_task(task["task_id"], status="queued", result=result) or task
        except Exception as e:
            failed = self.store.update_task(task["task_id"], status="failed", error=str(e))
            if failed:
                failed["error"] = str(e)
            raise

    def _delete_vector_index(self, kb_id: str) -> None:
        backend = self.retriever.vector_backend if self.retriever is not None else None
        delete_index = getattr(backend, "delete_index", None)
        if callable(delete_index):
            delete_index(kb_id)
            return
        delete_hnsw_sidecar_files(kb_id, _hnsw_index_dir_from_config(self.config))

    def _enqueue_graph_document(
        self,
        kb_id: str,
        doc_id: str,
        file_name: str,
        chunk_count: int,
    ) -> str | None:
        graph_manager = getattr(self, "graph_manager", None)
        if graph_manager is None:
            return None
        try:
            chunks = self.store.list_chunks(doc_id, offset=0, limit=max(1, chunk_count))
            return graph_manager.enqueue_document(
                kb_id=kb_id,
                doc_id=doc_id,
                doc_name=file_name,
                chunks=chunks,
            )
        except Exception as e:
            from core import logger

            logger.warning("Graph document extraction enqueue skipped: %s", e)
        return None

    def _graph_document_index_enabled(self) -> bool:
        if self.graph_manager is None:
            return False
        knowledge = self.config.get("knowledge", {}) if isinstance(self.config, dict) else {}
        if not isinstance(knowledge, dict):
            return False
        graph = knowledge.get("graph", {})
        if not isinstance(graph, dict):
            return False
        sources = graph.get("extraction_sources", ["documents", "chat"])
        if not isinstance(sources, list):
            sources = ["documents", "chat"]
        return (
            bool(graph.get("enabled"))
            and bool(graph.get("extraction_enabled", True))
            and "documents" in {str(source) for source in sources}
        )


def format_context(results: list[dict]) -> str:
    lines = ["[Knowledge context]"]
    for index, item in enumerate(results, start=1):
        lines.append(f"[{index}] {item['kb_name']} / {item['doc_name']}#{item['chunk_index']}")
        lines.append(str(item["content"]))
    return "\n".join(lines)


def _file_type(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return suffix or "txt"


def _int_at_least(value: object, field: str, minimum: int) -> int:
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} must be an integer") from e
    if parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return parsed


def _embedding_cache_max_size_from_config(config: dict[str, Any]) -> int:
    knowledge = config.get("knowledge", {}) if isinstance(config, dict) else {}
    if not isinstance(knowledge, dict):
        return DEFAULT_EMBEDDING_CACHE_MAX_SIZE
    try:
        parsed = int(cast(Any, knowledge.get("embedding_cache_max_size")))
    except (TypeError, ValueError):
        parsed = DEFAULT_EMBEDDING_CACHE_MAX_SIZE
    return max(0, parsed)


def _indexing_config(config: dict[str, Any]) -> dict[str, Any]:
    knowledge = config.get("knowledge", {}) if isinstance(config, dict) else {}
    if not isinstance(knowledge, dict):
        return {}
    indexing = knowledge.get("indexing", {})
    return indexing if isinstance(indexing, dict) else {}


def _async_indexing_enabled(config: dict[str, Any]) -> bool:
    return str(_indexing_config(config).get("mode") or "sync").lower() == "async"


def _indexing_auto_start(config: dict[str, Any]) -> bool:
    value = _indexing_config(config).get("auto_start", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _indexing_interval_seconds(config: dict[str, Any]) -> float:
    try:
        return max(0.1, float(_indexing_config(config).get("reconcile_interval_seconds", 5.0)))
    except (TypeError, ValueError):
        return 5.0


def _indexing_batch_size(config: dict[str, Any]) -> int:
    try:
        return max(1, int(_indexing_config(config).get("max_batch_size", 20)))
    except (TypeError, ValueError):
        return 20


def _indexing_stale_timeout_seconds(config: dict[str, Any]) -> float:
    try:
        return max(1.0, float(_indexing_config(config).get("stale_creating_timeout_seconds", 900)))
    except (TypeError, ValueError):
        return 900.0


def _hnsw_index_dir_from_config(config: dict[str, Any]) -> str | Path:
    knowledge = config.get("knowledge", {}) if isinstance(config, dict) else {}
    if not isinstance(knowledge, dict):
        return DEFAULT_HNSW_INDEX_DIR
    ann = knowledge.get("ann", {})
    if not isinstance(ann, dict):
        return DEFAULT_HNSW_INDEX_DIR
    return cast(str | Path, ann.get("index_dir") or DEFAULT_HNSW_INDEX_DIR)


def _aggregate_index_status(index_statuses: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in index_statuses}
    for status in (
        "failed",
        "creating",
        "deletion_in_progress",
        "pending",
        "deleting",
        "queued",
        "untracked",
    ):
        if status in statuses:
            return status
    return "active"


def _index_claim_sort_key(item: dict[str, Any]) -> tuple[str, int, str]:
    index_type = str(item.get("index_type") or "")
    return (
        str(item.get("doc_id") or ""),
        0
        if index_type == INDEX_TYPE_VECTOR_FULLTEXT
        else 1
        if index_type == INDEX_TYPE_GRAPH
        else 99,
        index_type,
    )


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
