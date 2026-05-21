"""Business facade for ATRI knowledge bases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.knowledge.chunking import RecursiveTextChunker
from core.knowledge.embedding import (
    EmbeddingClient,
    ModelSelection,
    OpenAIEmbeddingClient,
    resolve_model_selection,
)
from core.knowledge.rerank import OpenAIRerankClient, RerankClient
from core.knowledge.retrieval import HybridRetriever
from core.knowledge.store import KnowledgeStore


class KnowledgeBaseManager:
    """Coordinate knowledge base storage, ingestion, model validation, and retrieval."""

    def __init__(
        self,
        db_path: str | Path = "data/knowledge/knowledge.db",
        config: dict[str, Any] | None = None,
        embedding_client: EmbeddingClient | None = None,
        rerank_client: RerankClient | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.config = config or {}
        self.embedding_client = embedding_client or OpenAIEmbeddingClient()
        self.rerank_client = rerank_client or OpenAIRerankClient()
        self.store = KnowledgeStore(self.db_path)
        self.retriever: HybridRetriever | None = None

    async def initialize(self) -> None:
        self.store.initialize()
        self.retriever = HybridRetriever(self.store, self.rerank_client)

    async def close(self) -> None:
        self.store.close()

    def update_config(self, config: dict[str, Any]) -> None:
        merged = dict(self.config)
        merged.update(config)
        self.config = merged

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
        dimensions = embedding.dimensions or len(
            (
                await self.embedding_client.embed_texts(
                    embedding,
                    ["dimension probe"],
                )
            )[0]
        )
        RecursiveTextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        return self.store.create_kb(
            {
                "name": cleaned_name,
                "description": description,
                "embedding_provider": embedding.provider,
                "embedding_model": embedding.model,
                "embedding_config": embedding.config,
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
            update.update(
                {
                    "embedding_provider": embedding.provider,
                    "embedding_model": embedding.model,
                    "embedding_config": embedding.config,
                    "embedding_dimensions": embedding.dimensions,
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
        return self.store.delete_kb(kb_id)

    async def import_document(
        self,
        kb_id: str,
        *,
        file_name: str,
        content: str,
        file_type: str | None = None,
        source: str = "import",
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
            selection = self._selection_from_kb(kb)
            vectors = await self.embedding_client.embed_texts(selection, chunks)
            if len(vectors) != len(chunks):
                raise ValueError("embedding result count does not match chunk count")
            for vector in vectors:
                if len(vector) != int(kb["embedding_dimensions"]):
                    raise ValueError("embedding vector dimension does not match knowledge base")
            doc = self.store.create_document(
                kb_id=kb_id,
                file_name=file_name,
                file_type=file_type or _file_type(file_name),
                file_size=len(content.encode("utf-8")),
                source=source,
            )
            self.store.add_chunks(kb_id, doc["doc_id"], list(zip(chunks, vectors, strict=True)))
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
        text = content.decode("utf-8")
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
    ) -> dict:
        if self.retriever is None:
            raise RuntimeError("knowledge manager is not initialized")
        top_k = _int_at_least(top_k, "top_k", 1)
        kb_records = self._resolve_retrieval_kbs(kb_ids or [], kb_names or [])
        kb_records = [self._with_rerank_provider_config(kb) for kb in kb_records]
        query_vectors = {}
        for kb in kb_records:
            selection = self._selection_from_kb(kb)
            vectors = await self.embedding_client.embed_texts(selection, [query])
            query_vectors[kb["kb_id"]] = vectors[0]
        hits = await self.retriever.retrieve(
            query=query,
            kb_records=kb_records,
            query_vectors=query_vectors,
            top_k=top_k,
        )
        results = [hit.to_dict() for hit in hits]
        return {
            "query": query,
            "results": results,
            "total": len(results),
            "context_text": format_context(results),
        }

    async def get_task(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError("task not found")
        return task

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

    def _selection_from_kb(self, kb: dict) -> ModelSelection:
        providers = self.config.get("providers", {})
        provider_config = (
            providers.get(kb["embedding_provider"], {}) if isinstance(providers, dict) else {}
        )
        return ModelSelection(
            provider=kb["embedding_provider"],
            model=kb["embedding_model"],
            config=dict(kb.get("embedding_config") or {}),
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
        parsed = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} must be an integer") from e
    if parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return parsed
