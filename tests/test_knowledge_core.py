import asyncio
from typing import Any, ClassVar, cast

import pytest

import core.knowledge.store as store_module
from core.knowledge.chunking import RecursiveTextChunker
from core.knowledge.manager import KnowledgeBaseManager
from core.knowledge.rerank import OpenAIRerankClient
from core.knowledge.retrieval import HybridRetriever
from core.knowledge.store import KnowledgeStore


class FakeEmbeddingClient:
    async def embed_texts(self, selection, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    float(lowered.count("python")),
                    float(lowered.count("music")),
                    float(lowered.count("sqlite") + lowered.count("database")),
                ]
            )
        return vectors


class RecordingEmbeddingClient(FakeEmbeddingClient):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed_texts(self, selection, texts):
        self.calls.append(list(texts))
        return await super().embed_texts(selection, texts)


class RecordingGraphManager:
    def __init__(self) -> None:
        self.document_calls: list[dict[str, Any]] = []

    def enqueue_document(self, **kwargs):
        self.document_calls.append(kwargs)
        return "task-graph-document"


class FakeRerankClient:
    async def rerank(self, selection, query, documents):
        keyword = "sqlite" if "sqlite" in query.lower() else "python"
        return [
            {"index": index, "score": 1.0 if keyword in document.lower() else 0.1}
            for index, document in enumerate(documents)
        ]


class FailingRerankClient:
    async def rerank(self, selection, query, documents):
        raise RuntimeError("rerank offline")


class DenseHydrationStore:
    def __init__(self) -> None:
        self.hydrated_chunk_ids: list[str] = []

    def vector_chunks(self, kb_ids):
        return [
            self._row("chunk-python", [1.0, 0.0, 0.0], "Python retrieval chunk"),
            self._row("chunk-sqlite", [0.0, 0.0, 1.0], "SQLite retrieval chunk"),
        ]

    def vector_chunk_candidates(self, kb_ids):
        return [
            self._candidate("chunk-python", [1.0, 0.0, 0.0]),
            self._candidate("chunk-sqlite", [0.0, 0.0, 1.0]),
        ]

    def chunks_by_ids(self, chunk_ids):
        self.hydrated_chunk_ids = list(chunk_ids)
        return [
            self._row("chunk-python", [1.0, 0.0, 0.0], "Python retrieval chunk"),
            self._row("chunk-sqlite", [0.0, 0.0, 1.0], "SQLite retrieval chunk"),
        ]

    def keyword_search(self, query, kb_ids, limit):
        return []

    def _candidate(self, chunk_id, embedding):
        return {
            "chunk_id": chunk_id,
            "kb_id": "kb-1",
            "doc_id": "doc-1",
            "chunk_index": 0,
            "embedding": embedding,
            "embedding_norm": 1.0,
        }

    def _row(self, chunk_id, embedding, content):
        return {
            **self._candidate(chunk_id, embedding),
            "kb_name": "Docs",
            "doc_name": "notes.txt",
            "content": content,
            "char_count": len(content),
        }


class DenseBackendInjectionStore(DenseHydrationStore):
    def vector_chunk_candidates(self, kb_ids):
        raise AssertionError("injected vector backend should handle dense search")


class FailingDenseBackendStore(DenseHydrationStore):
    def dense_vector_search(self, kb_ids, query_vectors, limits, timings=None):
        if timings is not None:
            timings["vector_backend"] = "sqlite_blob_numpy"
        raise RuntimeError("dense backend offline")


class RecordingVectorBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def search(self, *, kb_ids, query_vectors, options, timings=None):
        self.calls.append(
            {
                "kb_ids": list(kb_ids),
                "query_vectors": dict(query_vectors),
                "options": dict(options),
            }
        )
        if timings is not None:
            timings["vector_backend"] = "recording"
        return [
            {
                "chunk_id": "chunk-sqlite",
                "kb_id": "kb-1",
                "doc_id": "doc-1",
                "chunk_index": 0,
                "embedding_norm": 1.0,
                "dense_score": 0.99,
            }
        ]


class FailingVectorBackend:
    def search(self, *, kb_ids, query_vectors, options, timings=None):
        raise AssertionError("HNSW backend should not fall back to exact dense search")


class FakeHnswLib:
    saved_indexes: ClassVar[dict[str, dict[str, Any]]] = {}

    class Index:
        def __init__(self, *, space, dim) -> None:
            self.space = space
            self.dim = dim
            self.items: list[list[float]] = []
            self.labels: list[int] = []
            self.ef = 0

        def init_index(self, *, max_elements, ef_construction, **kwargs) -> None:
            self.max_elements = max_elements
            self.ef_construction = ef_construction
            self.m = kwargs.get("M")

        def add_items(self, items, labels) -> None:
            self.items = [[float(value) for value in item] for item in items]
            self.labels = [int(label) for label in labels]

        def set_ef(self, ef) -> None:
            self.ef = int(ef)

        def knn_query(self, queries, k):
            query = [float(value) for value in next(iter(queries))]
            scored = []
            for label, item in zip(self.labels, self.items, strict=False):
                scored.append((1.0 - _cosine(query, item), label))
            scored.sort(key=lambda item: (item[0], item[1]))
            selected = scored[: int(k)]
            return [[label for _, label in selected]], [[distance for distance, _ in selected]]

        def save_index(self, path) -> None:
            FakeHnswLib.saved_indexes[str(path)] = {
                "space": self.space,
                "dim": self.dim,
                "items": list(self.items),
                "labels": list(self.labels),
            }
            with open(path, "w", encoding="utf-8") as marker:
                marker.write("fake hnsw index")

        def load_index(self, path, max_elements=None) -> None:
            saved = FakeHnswLib.saved_indexes[str(path)]
            self.space = saved["space"]
            self.dim = saved["dim"]
            self.items = list(saved["items"])
            self.labels = list(saved["labels"])
            self.max_elements = max_elements or len(self.labels)


def _cosine(left: list[float], right: list[float]) -> float:
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return float(sum(a * b for a, b in zip(left, right, strict=False)) / (left_norm * right_norm))


class MismatchedConfigEmbeddingClient:
    def __init__(self, *, actual: int) -> None:
        self.actual = actual

    async def embed_texts(self, selection, texts):
        return [[float(index)] * self.actual for index, _ in enumerate(texts)]


def _config():
    return {
        "providers": {
            "OpenAI": {"api_key": "sk-test", "base_url": "https://example.test/v1"},
            "Local": {"api_key": "", "base_url": "http://localhost:11434/v1"},
        },
        "active_embedding_models": [
            {
                "provider": "OpenAI",
                "model": "embed-a",
                "config": {"dimensions": 3, "batch_size": 16, "encoding_format": "float"},
            }
        ],
        "active_rerank_models": [
            {
                "provider": "Local",
                "model": "rerank-a",
                "config": {"top_n": 5, "score_threshold": 0.0, "max_input_tokens": 8192},
            }
        ],
    }


def test_recursive_chunker_keeps_overlap_and_rejects_invalid_settings():
    chunker = RecursiveTextChunker(chunk_size=12, chunk_overlap=4)

    chunks = chunker.chunk("alpha beta gamma delta")

    assert chunks == ["alpha beta", "beta gamma", "amma delta"]
    with pytest.raises(ValueError, match="chunk_overlap"):
        RecursiveTextChunker(chunk_size=10, chunk_overlap=10)


@pytest.mark.asyncio
async def test_knowledge_manager_imports_and_retrieves_with_selected_models(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
        rerank_client=FakeRerankClient(),
    )
    await manager.initialize()

    kb = await manager.create_knowledge_base(
        name="Docs",
        description="Project notes",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        rerank_provider="Local",
        rerank_model="rerank-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    task = await manager.import_document(
        kb["kb_id"],
        file_name="notes.md",
        content=(
            "Python agents can use tools.\n\n"
            "SQLite stores knowledge chunks for retrieval.\n\n"
            "Music sessions are unrelated."
        ),
    )

    result = await manager.retrieve(
        query="how does sqlite retrieval work?",
        kb_ids=[kb["kb_id"]],
        top_k=2,
    )
    documents = await manager.list_documents(kb["kb_id"])
    chunks = await manager.list_chunks(documents[0]["doc_id"])
    stored_task = await manager.get_task(task["task_id"])

    assert kb["embedding_provider"] == "OpenAI"
    assert kb["embedding_model"] == "embed-a"
    assert kb["embedding_dimensions"] == 3
    assert task["status"] == "completed"
    assert stored_task["result"]["success_count"] == 1
    assert documents[0]["chunk_count"] == len(chunks)
    assert result["results"][0]["doc_name"] == "notes.md"
    assert "SQLite stores knowledge chunks" in result["results"][0]["content"]
    assert result["context_text"].startswith("[Knowledge context]")


@pytest.mark.asyncio
async def test_async_import_records_payload_and_pending_index_without_embedding(tmp_path):
    embedding = RecordingEmbeddingClient()
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config={
            **_config(),
            "knowledge": {"indexing": {"mode": "async", "auto_start": False}},
        },
        embedding_client=embedding,
        rerank_client=FakeRerankClient(),
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Queued Docs",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    embedding.calls.clear()

    task = await manager.import_document(
        kb["kb_id"],
        file_name="queued.md",
        content="Python agents use SQLite retrieval.",
    )
    documents = await manager.list_documents(kb["kb_id"])
    doc_id = documents[0]["doc_id"]

    assert task["status"] == "queued"
    assert embedding.calls == []
    payload = manager.store.get_document_payload(doc_id)
    assert payload is not None
    assert payload["content"] == "Python agents use SQLite retrieval."
    assert await manager.list_chunks(doc_id) == []
    assert manager.store.list_document_indexes(doc_id) == [
        {
            "doc_id": doc_id,
            "index_type": "vector_fulltext",
            "status": "pending",
            "version": 1,
            "observed_version": 0,
            "error": "",
        }
    ]


@pytest.mark.asyncio
async def test_async_reconcile_builds_vector_fulltext_index_and_marks_it_active(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config={
            **_config(),
            "knowledge": {"indexing": {"mode": "async", "auto_start": False}},
        },
        embedding_client=FakeEmbeddingClient(),
        rerank_client=FakeRerankClient(),
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Reconciled Docs",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        rerank_provider="Local",
        rerank_model="rerank-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    await manager.import_document(
        kb["kb_id"],
        file_name="reconciled.md",
        content="Python agents can use tools. SQLite stores knowledge chunks.",
    )
    doc = (await manager.list_documents(kb["kb_id"]))[0]

    reconciled = await manager.reconcile_indexes_once()
    chunks = await manager.list_chunks(doc["doc_id"])
    result = await manager.retrieve(
        query="sqlite retrieval",
        kb_ids=[kb["kb_id"]],
        top_k=1,
    )
    indexes = manager.store.list_document_indexes(doc["doc_id"])

    assert reconciled == 1
    assert len(chunks) >= 1
    assert result["results"][0]["doc_name"] == "reconciled.md"
    assert indexes[0]["status"] == "active"
    assert indexes[0]["observed_version"] == indexes[0]["version"] == 1


@pytest.mark.asyncio
async def test_async_reconcile_enqueues_graph_index_after_vector_chunks_exist(tmp_path):
    graph_manager = RecordingGraphManager()
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config={
            **_config(),
            "knowledge": {
                "indexing": {"mode": "async", "auto_start": False},
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["documents"],
                },
            },
        },
        embedding_client=FakeEmbeddingClient(),
        graph_manager=graph_manager,
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Graph Queued Docs",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    await manager.import_document(
        kb["kb_id"],
        file_name="graph.md",
        content="Alice works at Acme. SQLite stores knowledge chunks.",
    )
    doc = (await manager.list_documents(kb["kb_id"]))[0]

    reconciled = await manager.reconcile_indexes_once()
    indexes = {
        item["index_type"]: item for item in manager.store.list_document_indexes(doc["doc_id"])
    }

    assert reconciled == 2
    assert [call["doc_id"] for call in graph_manager.document_calls] == [doc["doc_id"]]
    assert graph_manager.document_calls[0]["chunks"]
    assert indexes["vector_fulltext"]["status"] == "active"
    assert indexes["graph"]["status"] == "queued"


@pytest.mark.asyncio
async def test_knowledge_manager_update_config_reconfigures_local_index_worker(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
    )
    await manager.initialize()
    try:
        assert manager.index_worker is None

        manager.update_config(
            {
                **_config(),
                "knowledge": {
                    "indexing": {
                        "mode": "async",
                        "auto_start": True,
                        "reconcile_interval_seconds": 0.2,
                        "max_batch_size": 3,
                        "stale_creating_timeout_seconds": 7,
                    }
                },
            }
        )
        await asyncio.sleep(0)
        first_worker = manager.index_worker

        assert first_worker is not None
        assert first_worker.running is True
        assert first_worker.interval_seconds == 0.2
        assert first_worker.batch_size == 3
        assert manager.index_reconciler is not None
        assert manager.index_reconciler.stale_timeout_seconds == 7.0

        manager.update_config(
            {
                **_config(),
                "knowledge": {
                    "indexing": {
                        "mode": "async",
                        "auto_start": True,
                        "reconcile_interval_seconds": 0.4,
                        "max_batch_size": 4,
                        "stale_creating_timeout_seconds": 9,
                    }
                },
            }
        )

        assert manager.index_worker is first_worker
        assert first_worker.interval_seconds == 0.4
        assert first_worker.batch_size == 4
        assert manager.index_reconciler.stale_timeout_seconds == 9.0

        manager.update_config(
            {
                **_config(),
                "knowledge": {
                    "indexing": {
                        "mode": "async",
                        "auto_start": False,
                    }
                },
            }
        )
        await asyncio.sleep(0)

        assert manager.index_worker is None
        assert first_worker.running is False
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_knowledge_manager_reports_index_status_and_rebuilds_sync_document(tmp_path):
    embedding = RecordingEmbeddingClient()
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=embedding,
        rerank_client=FakeRerankClient(),
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Status Docs",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    await manager.import_document(
        kb["kb_id"],
        file_name="status.md",
        content="Python agents use SQLite retrieval.",
    )
    doc = (await manager.list_documents(kb["kb_id"]))[0]

    status = await manager.get_index_status(kb["kb_id"])
    embedding.calls.clear()
    rebuild = await manager.rebuild_document_indexes(
        kb_id=kb["kb_id"],
        doc_id=doc["doc_id"],
    )
    rebuilt_status = await manager.get_index_status(kb["kb_id"])

    assert status["summary"]["active"] == 1
    assert status["summary"]["untracked"] == 0
    assert status["documents"][0]["aggregate_status"] == "active"
    assert status["documents"][0]["rebuildable"] is True
    assert status["documents"][0]["index_statuses"][0]["index_type"] == "vector_fulltext"
    assert status["documents"][0]["index_statuses"][0]["status"] == "active"
    assert rebuild["queued"] == 1
    assert rebuild["reconciled"] == 1
    assert rebuild["skipped"] == []
    assert embedding.calls
    assert rebuilt_status["documents"][0]["index_statuses"][0]["version"] == 2
    assert rebuilt_status["documents"][0]["index_statuses"][0]["status"] == "active"


@pytest.mark.asyncio
async def test_sync_rebuild_reconciles_requested_index_when_older_pending_exists(tmp_path):
    embedding = RecordingEmbeddingClient()
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=embedding,
        rerank_client=FakeRerankClient(),
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Target Docs",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    other_kb = await manager.create_knowledge_base(
        name="Other Docs",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    await manager.import_document(
        kb["kb_id"],
        file_name="target.md",
        content="Python agents use SQLite retrieval.",
    )
    target_doc = (await manager.list_documents(kb["kb_id"]))[0]
    other_doc = manager.store.create_document(
        other_kb["kb_id"],
        "older.md",
        "md",
        38,
        "test",
    )
    manager.store.save_document_payload(other_doc["doc_id"], "Older pending SQLite document.")
    manager.store.request_document_indexes(
        kb_id=other_kb["kb_id"],
        doc_id=other_doc["doc_id"],
        index_types=["vector_fulltext"],
    )

    rebuild = await manager.rebuild_document_indexes(
        kb_id=kb["kb_id"],
        doc_id=target_doc["doc_id"],
    )
    target_index = manager.store.list_document_indexes(target_doc["doc_id"])[0]
    other_index = manager.store.list_document_indexes(other_doc["doc_id"])[0]

    assert rebuild["reconciled"] == 1
    assert target_index["version"] == 2
    assert target_index["status"] == "active"
    assert target_index["observed_version"] == 2
    assert other_index["status"] == "pending"
    assert other_index["observed_version"] == 0


@pytest.mark.asyncio
async def test_knowledge_manager_marks_payloadless_documents_as_untracked(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Legacy Docs",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    doc = manager.store.create_document(
        kb["kb_id"],
        "legacy.md",
        "md",
        42,
        "legacy",
    )

    status = await manager.get_index_status(kb["kb_id"])
    rebuild = await manager.rebuild_document_indexes(
        kb_id=kb["kb_id"],
        doc_id=doc["doc_id"],
    )

    assert status["summary"]["untracked"] == 1
    assert status["summary"]["source_missing"] == 1
    assert status["documents"][0]["aggregate_status"] == "untracked"
    assert status["documents"][0]["rebuildable"] is False
    assert status["documents"][0]["index_statuses"][0]["status"] == "untracked"
    assert rebuild["queued"] == 0
    assert rebuild["reconciled"] == 0
    assert rebuild["skipped"] == [{"doc_id": doc["doc_id"], "reason": "source_missing"}]


@pytest.mark.asyncio
async def test_knowledge_manager_records_retrieval_timing_segments(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
        rerank_client=FakeRerankClient(),
    )
    await manager.initialize()

    kb = await manager.create_knowledge_base(
        name="Timed Docs",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        rerank_provider="Local",
        rerank_model="rerank-a",
        chunk_size=80,
        chunk_overlap=10,
    )
    await manager.import_document(
        kb["kb_id"],
        file_name="timed.txt",
        content="SQLite stores knowledge chunks. Python agents use tools.",
    )

    timings: dict[str, Any] = {}
    await manager.retrieve(
        query="sqlite retrieval",
        kb_ids=[kb["kb_id"]],
        top_k=2,
        timings=timings,
    )

    for key in (
        "vector_total_ms",
        "vector_embed_ms",
        "vector_store_ms",
        "vector_dense_ms",
        "vector_sparse_ms",
        "vector_fuse_ms",
        "vector_rerank_ms",
        "vector_limit_ms",
    ):
        assert key in timings
        assert timings[key] >= 0
    assert timings["vector_rows"] >= 1
    assert timings["vector_sparse_rows"] >= 0
    assert timings["vector_returned_hits"] >= 1


@pytest.mark.asyncio
async def test_dense_retrieval_hydrates_only_top_vector_candidates():
    store = DenseHydrationStore()
    retriever = HybridRetriever(cast(KnowledgeStore, store))

    hits = await retriever.retrieve(
        query="sqlite",
        kb_records=[
            {
                "kb_id": "kb-1",
                "name": "Docs",
                "top_k_dense": 1,
                "top_k_sparse": 1,
                "top_m_final": 1,
            }
        ],
        query_vectors={"kb-1": [0.0, 0.0, 1.0]},
        top_k=1,
    )

    assert store.hydrated_chunk_ids == ["chunk-sqlite"]
    assert [hit.chunk_id for hit in hits] == ["chunk-sqlite"]
    assert hits[0].content == "SQLite retrieval chunk"


@pytest.mark.asyncio
async def test_hybrid_retriever_accepts_injected_vector_backend():
    store = DenseBackendInjectionStore()
    backend = RecordingVectorBackend()
    retriever = HybridRetriever(cast(KnowledgeStore, store), vector_backend=backend)
    timings: dict[str, Any] = {}

    hits = await retriever.retrieve(
        query="sqlite",
        kb_records=[
            {
                "kb_id": "kb-1",
                "name": "Docs",
                "top_k_dense": 3,
                "top_k_sparse": 1,
                "top_m_final": 1,
            }
        ],
        query_vectors={"kb-1": [0.0, 0.0, 1.0]},
        top_k=1,
        timings=timings,
    )

    assert backend.calls == [
        {
            "kb_ids": ["kb-1"],
            "query_vectors": {"kb-1": [0.0, 0.0, 1.0]},
            "options": {
                "kb-1": {
                    "kb_id": "kb-1",
                    "name": "Docs",
                    "top_k_dense": 3,
                    "top_k_sparse": 1,
                    "top_m_final": 1,
                }
            },
        }
    ]
    assert timings["vector_backend"] == "recording"
    assert [hit.chunk_id for hit in hits] == ["chunk-sqlite"]


def test_sqlite_json_vector_backend_ranks_without_hydrating_content():
    from core.knowledge.vector_backend import SQLiteJsonVectorBackend

    store = DenseHydrationStore()
    backend = SQLiteJsonVectorBackend(store)
    timings: dict[str, Any] = {}

    rows = backend.search(
        kb_ids=["kb-1"],
        query_vectors={"kb-1": [0.0, 0.0, 1.0]},
        options={"kb-1": {"top_k_dense": 1}},
        timings=timings,
    )

    assert [row["chunk_id"] for row in rows] == ["chunk-sqlite"]
    assert "content" not in rows[0]
    assert "embedding" not in rows[0]
    assert store.hydrated_chunk_ids == []
    assert timings["vector_backend"] == "sqlite_json_scan"
    assert timings["vector_rows"] == 2


def test_hnsw_vector_backend_loads_sidecar_index_without_full_dense_scan(tmp_path, monkeypatch):
    from core.knowledge.vector_backend import HnswVectorBackend

    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "HNSW Dense Backend",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
            "top_k_dense": 1,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
            ("Music retrieval chunk", [0.0, 1.0, 0.0]),
        ],
    )
    sqlite_chunk_id = store.list_chunks(doc["doc_id"])[1]["chunk_id"]
    index_dir = tmp_path / "indexes"
    query_vectors = {kb["kb_id"]: [0.0, 0.0, 1.0]}
    options = {kb["kb_id"]: {"top_k_dense": 1}}

    build_backend = HnswVectorBackend(
        store,
        index_dir=index_dir,
        fallback=FailingVectorBackend(),
        hnswlib_module=FakeHnswLib,
        candidate_k=2,
    )
    build_rows = build_backend.search(
        kb_ids=[kb["kb_id"]],
        query_vectors=query_vectors,
        options=options,
    )
    assert [row["chunk_id"] for row in build_rows] == [sqlite_chunk_id]

    def fail_full_index_rows(kb_id):
        raise AssertionError("saved HNSW index should avoid full index row scan")

    def fail_exact_dense_search(*args, **kwargs):
        raise AssertionError("saved HNSW index should avoid exact full dense search")

    monkeypatch.setattr(store, "vector_index_rows", fail_full_index_rows)
    monkeypatch.setattr(store, "dense_vector_search", fail_exact_dense_search)
    timings: dict[str, Any] = {}

    load_backend = HnswVectorBackend(
        store,
        index_dir=index_dir,
        fallback=FailingVectorBackend(),
        hnswlib_module=FakeHnswLib,
        candidate_k=2,
    )
    loaded_rows = load_backend.search(
        kb_ids=[kb["kb_id"]],
        query_vectors=query_vectors,
        options=options,
        timings=timings,
    )

    assert [row["chunk_id"] for row in loaded_rows] == [sqlite_chunk_id]
    assert timings["vector_backend"] == "hnsw"
    assert timings["ann_index_hit"] is True
    assert timings["ann_candidates"] == 2
    assert timings["vector_rows"] == 3


def test_build_default_vector_backend_uses_hnsw_when_enabled(tmp_path):
    from core.knowledge.vector_backend import HnswVectorBackend, build_default_vector_backend

    backend = build_default_vector_backend(
        DenseHydrationStore(),
        {
            "knowledge": {
                "ann": {
                    "enabled": True,
                    "index_dir": str(tmp_path / "indexes"),
                }
            }
        },
    )

    assert isinstance(backend, HnswVectorBackend)


@pytest.mark.asyncio
async def test_knowledge_manager_deletes_hnsw_sidecar_files_when_kb_is_deleted(tmp_path):
    from core.knowledge.vector_backend import HnswVectorBackend, _LoadedHnswIndex

    index_dir = tmp_path / "indexes"
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config={**_config(), "knowledge": {"ann": {"enabled": True, "index_dir": str(index_dir)}}},
        embedding_client=FakeEmbeddingClient(),
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Deleted HNSW Sidecar",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
    )
    assert manager.retriever is not None
    backend = manager.retriever.vector_backend
    assert isinstance(backend, HnswVectorBackend)
    index_path, metadata_path = backend._index_files(kb["kb_id"])
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("stale index", encoding="utf-8")
    metadata_path.write_text("{}", encoding="utf-8")
    backend._index_cache[kb["kb_id"]] = _LoadedHnswIndex(index=object(), metadata={})

    deleted = await manager.delete_knowledge_base(kb["kb_id"])

    assert deleted is True
    assert not index_path.exists()
    assert not metadata_path.exists()
    assert kb["kb_id"] not in backend._index_cache


@pytest.mark.asyncio
async def test_dense_retrieval_marks_json_backend_after_blob_backend_fallback(caplog):
    store = FailingDenseBackendStore()
    retriever = HybridRetriever(cast(KnowledgeStore, store))
    timings: dict[str, Any] = {}

    hits = await retriever.retrieve(
        query="sqlite",
        kb_records=[
            {
                "kb_id": "kb-1",
                "name": "Docs",
                "top_k_dense": 1,
                "top_k_sparse": 1,
                "top_m_final": 1,
            }
        ],
        query_vectors={"kb-1": [0.0, 0.0, 1.0]},
        top_k=1,
        timings=timings,
    )

    assert [hit.chunk_id for hit in hits] == ["chunk-sqlite"]
    assert timings["vector_backend"] == "sqlite_json_scan"
    assert "Knowledge dense vector backend failed" in caplog.text


def test_store_writes_float32_embedding_blobs_for_new_chunks(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Blob Vectors",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")

    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [("SQLite retrieval chunk", [0.0, 0.0, 1.0])],
    )

    columns = {row["name"] for row in store._conn().execute("PRAGMA table_info(chunks)").fetchall()}
    row = (
        store._conn()
        .execute("SELECT embedding_blob, embedding_dtype, embedding_revision FROM chunks")
        .fetchone()
    )

    assert {"embedding_blob", "embedding_dtype", "embedding_revision"} <= columns
    assert row["embedding_blob"] is not None
    assert len(row["embedding_blob"]) == 12
    assert row["embedding_dtype"] == "float32"
    assert row["embedding_revision"] == 1


def test_store_resets_stale_creating_indexes_to_pending(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Stale Index",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "stale.txt", "txt", 42, "test")
    store.request_document_indexes(
        kb_id=kb["kb_id"],
        doc_id=doc["doc_id"],
        index_types=["vector_fulltext"],
    )
    candidate = store.list_indexes_needing_reconciliation(limit=1)[0]
    claimed = store.claim_document_index(candidate["index_id"])
    assert claimed is not None
    store._conn().execute(
        """
        UPDATE document_indexes
        SET updated_at=0, last_reconciled_at=0
        WHERE index_id=?
        """,
        (candidate["index_id"],),
    )
    store._conn().commit()

    reset_count = store.reset_stale_document_indexes(timeout_seconds=1)
    indexes = store.list_document_indexes(doc["doc_id"])

    assert reset_count == 1
    assert indexes[0]["status"] == "pending"
    assert indexes[0]["observed_version"] == 0


def test_store_lazily_backfills_missing_embedding_blobs_for_legacy_rows(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Legacy Blob Backfill",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
        ],
    )
    store._conn().execute("UPDATE chunks SET embedding_blob=NULL WHERE kb_id=?", (kb["kb_id"],))
    store._conn().commit()

    rows = store.dense_vector_search(
        [kb["kb_id"]],
        {kb["kb_id"]: [0.0, 0.0, 1.0]},
        {kb["kb_id"]: 1},
    )

    backfilled_count = (
        store._conn()
        .execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE kb_id=? AND embedding_blob IS NOT NULL",
            (kb["kb_id"],),
        )
        .fetchone()["count"]
    )
    assert [row["chunk_id"] for row in rows] == [store.list_chunks(doc["doc_id"])[1]["chunk_id"]]
    assert "embedding" not in rows[0]
    assert backfilled_count == 2


@pytest.mark.asyncio
async def test_dense_retrieval_uses_numpy_blob_backend_when_available(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Numpy Dense Backend",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
            "top_k_dense": 1,
            "top_k_sparse": 1,
            "top_m_final": 1,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
        ],
    )

    def fail_json_candidate_scan(kb_ids):
        raise AssertionError("dense retrieval should not use JSON vector candidate scan")

    monkeypatch.setattr(store, "vector_chunk_candidates", fail_json_candidate_scan)
    retriever = HybridRetriever(store)
    timings: dict[str, Any] = {}

    hits = await retriever.retrieve(
        query="sqlite",
        kb_records=[kb],
        query_vectors={kb["kb_id"]: [0.0, 0.0, 1.0]},
        top_k=1,
        timings=timings,
    )

    assert [hit.content for hit in hits] == ["SQLite retrieval chunk"]
    assert timings["vector_backend"] == "sqlite_blob_numpy"
    assert timings["vector_matrix_load_ms"] >= 0
    assert timings["vector_matmul_ms"] >= 0


def test_store_invalidates_numpy_vector_matrix_cache_when_chunks_change(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Matrix Cache Invalidation",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    first_doc = store.create_document(kb["kb_id"], "first.txt", "txt", 21, "test")
    store.add_chunks(
        kb["kb_id"],
        first_doc["doc_id"],
        [("Python retrieval chunk", [1.0, 0.0, 0.0])],
    )
    store.dense_vector_search(
        [kb["kb_id"]],
        {kb["kb_id"]: [1.0, 0.0, 0.0]},
        {kb["kb_id"]: 1},
    )

    second_doc = store.create_document(kb["kb_id"], "second.txt", "txt", 21, "test")
    store.add_chunks(
        kb["kb_id"],
        second_doc["doc_id"],
        [("SQLite retrieval chunk", [0.0, 0.0, 1.0])],
    )
    rows = store.dense_vector_search(
        [kb["kb_id"]],
        {kb["kb_id"]: [0.0, 0.0, 1.0]},
        {kb["kb_id"]: 1},
    )

    assert rows[0]["chunk_id"] == store.list_chunks(second_doc["doc_id"])[0]["chunk_id"]


def test_store_invalidates_numpy_vector_matrix_cache_when_kb_is_deleted(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Deleted Matrix Cache",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 21, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [("Python retrieval chunk", [1.0, 0.0, 0.0])],
    )
    store.dense_vector_search(
        [kb["kb_id"]],
        {kb["kb_id"]: [1.0, 0.0, 0.0]},
        {kb["kb_id"]: 1},
    )

    assert kb["kb_id"] in store._vector_matrix_cache

    store.delete_kb(kb["kb_id"])

    assert kb["kb_id"] not in store._vector_matrix_cache


def test_store_delete_kb_removes_default_hnsw_sidecar_files(tmp_path, monkeypatch):
    from core.knowledge.vector_backend import hnsw_index_files

    monkeypatch.chdir(tmp_path)
    store = KnowledgeStore("knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Deleted Default Sidecar",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    index_path, metadata_path = hnsw_index_files(kb["kb_id"])
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("stale index", encoding="utf-8")
    metadata_path.write_text("{}", encoding="utf-8")

    deleted = store.delete_kb(kb["kb_id"])

    assert deleted is True
    assert not index_path.exists()
    assert not metadata_path.exists()


def test_store_reuses_decoded_embeddings_between_vector_scans(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Cached Vectors",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
        ],
    )
    original_loads = store_module.json.loads
    embedding_decode_count = 0

    def counting_loads(value, *args, **kwargs):
        nonlocal embedding_decode_count
        if isinstance(value, str) and value.startswith("["):
            embedding_decode_count += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(store_module.json, "loads", counting_loads)

    first_rows = store.vector_chunks([kb["kb_id"]])
    second_rows = store.vector_chunks([kb["kb_id"]])

    assert [row["embedding"] for row in first_rows] == [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    assert [row["embedding"] for row in second_rows] == [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    assert embedding_decode_count == 2


def test_store_reuses_vector_candidate_cache_between_scans(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Cached Candidate Vectors",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
        ],
    )
    vector_select_count = 0

    def trace(statement):
        nonlocal vector_select_count
        if (
            "c.embedding" in statement
            and "c.embedding_norm" in statement
            and "FROM chunks c" in statement
        ):
            vector_select_count += 1

    store._conn().set_trace_callback(trace)
    first_rows = store.vector_chunk_candidates([kb["kb_id"]])
    second_rows = store.vector_chunk_candidates([kb["kb_id"]])
    store._conn().set_trace_callback(None)

    assert [row["embedding"] for row in first_rows] == [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    assert [row["embedding"] for row in second_rows] == [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    assert vector_select_count == 1


def test_store_invalidates_vector_candidate_cache_when_chunks_change(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Invalidated Candidate Vectors",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    first_doc = store.create_document(kb["kb_id"], "first.txt", "txt", 21, "test")
    store.add_chunks(
        kb["kb_id"],
        first_doc["doc_id"],
        [("Python retrieval chunk", [1.0, 0.0, 0.0])],
    )

    assert len(store.vector_chunk_candidates([kb["kb_id"]])) == 1

    second_doc = store.create_document(kb["kb_id"], "second.txt", "txt", 21, "test")
    store.add_chunks(
        kb["kb_id"],
        second_doc["doc_id"],
        [("SQLite retrieval chunk", [0.0, 0.0, 1.0])],
    )
    rows_after_add = store.vector_chunk_candidates([kb["kb_id"]])

    assert [row["embedding"] for row in rows_after_add] == [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]

    assert store.delete_chunk(rows_after_add[0]["chunk_id"]) is True
    rows_after_delete = store.vector_chunk_candidates([kb["kb_id"]])

    assert [row["embedding"] for row in rows_after_delete] == [[0.0, 0.0, 1.0]]


def test_store_returns_uncached_vector_candidates_when_kb_exceeds_cache_limit(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db", embedding_cache_max_size=1)
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Oversized Candidate Vectors",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
        ],
    )

    rows = store.vector_chunk_candidates([kb["kb_id"]])

    assert [row["embedding"] for row in rows] == [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    assert store._vector_candidate_cache_size == 0


def test_store_returns_loaded_candidates_when_lru_trim_evicts_during_same_scan(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db", embedding_cache_max_size=3)
    store.initialize()
    first_kb = store.create_kb(
        {
            "name": "First Trimmed KB",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    second_kb = store.create_kb(
        {
            "name": "Second Trimmed KB",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    first_doc = store.create_document(first_kb["kb_id"], "first.txt", "txt", 42, "test")
    second_doc = store.create_document(second_kb["kb_id"], "second.txt", "txt", 42, "test")
    store.add_chunks(
        first_kb["kb_id"],
        first_doc["doc_id"],
        [
            ("First python chunk", [1.0, 0.0, 0.0]),
            ("First sqlite chunk", [0.0, 0.0, 1.0]),
        ],
    )
    store.add_chunks(
        second_kb["kb_id"],
        second_doc["doc_id"],
        [
            ("Second python chunk", [2.0, 0.0, 0.0]),
            ("Second sqlite chunk", [0.0, 0.0, 2.0]),
        ],
    )

    rows = store.vector_chunk_candidates([first_kb["kb_id"], second_kb["kb_id"]])

    assert [(row["kb_id"], row["embedding"]) for row in rows] == [
        (first_kb["kb_id"], [1.0, 0.0, 0.0]),
        (first_kb["kb_id"], [0.0, 0.0, 1.0]),
        (second_kb["kb_id"], [2.0, 0.0, 0.0]),
        (second_kb["kb_id"], [0.0, 0.0, 2.0]),
    ]
    assert store._vector_candidate_cache_size == 2


def test_keyword_fallback_search_does_not_decode_embeddings(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Keyword Fallback",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "keywords.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
        ],
    )
    store.fts_available = False
    original_loads = store_module.json.loads
    embedding_decode_count = 0

    def counting_loads(value, *args, **kwargs):
        nonlocal embedding_decode_count
        if isinstance(value, str) and value.startswith("["):
            embedding_decode_count += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(store_module.json, "loads", counting_loads)

    rows = store.keyword_search("sqlite", [kb["kb_id"]], limit=10)

    assert len(rows) == 1
    assert rows[0]["content"] == "SQLite retrieval chunk"
    assert rows[0]["doc_name"] == "keywords.txt"
    assert rows[0]["kb_name"] == "Keyword Fallback"
    assert rows[0]["sparse_score"] == 1.0
    assert embedding_decode_count == 0


def test_store_can_disable_decoded_embedding_cache(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "knowledge.db", embedding_cache_max_size=0)
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Uncached Vectors",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
        ],
    )
    original_loads = store_module.json.loads
    embedding_decode_count = 0

    def counting_loads(value, *args, **kwargs):
        nonlocal embedding_decode_count
        if isinstance(value, str) and value.startswith("["):
            embedding_decode_count += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(store_module.json, "loads", counting_loads)

    store.vector_chunks([kb["kb_id"]])
    store.vector_chunks([kb["kb_id"]])

    assert embedding_decode_count == 4
    assert len(store._embedding_cache) == 0


def test_store_trims_decoded_embedding_cache_to_configured_limit(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge.db", embedding_cache_max_size=1)
    store.initialize()
    kb = store.create_kb(
        {
            "name": "Trimmed Vectors",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 3},
            "embedding_dimensions": 3,
        }
    )
    doc = store.create_document(kb["kb_id"], "vectors.txt", "txt", 42, "test")
    store.add_chunks(
        kb["kb_id"],
        doc["doc_id"],
        [
            ("Python retrieval chunk", [1.0, 0.0, 0.0]),
            ("SQLite retrieval chunk", [0.0, 0.0, 1.0]),
        ],
    )

    store.vector_chunks([kb["kb_id"]])
    assert len(store._embedding_cache) == 1

    store.set_embedding_cache_max_size(0)
    assert store.embedding_cache_max_size == 0
    assert len(store._embedding_cache) == 0


def test_knowledge_manager_applies_embedding_cache_limit_from_config(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config={**_config(), "knowledge": {"embedding_cache_max_size": 3}},
        embedding_client=FakeEmbeddingClient(),
    )

    assert manager.store.embedding_cache_max_size == 3

    manager.update_config({"knowledge": {"embedding_cache_max_size": 0}})

    assert manager.store.embedding_cache_max_size == 0


@pytest.mark.asyncio
async def test_knowledge_manager_applies_top_m_final_to_retrieval(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
        rerank_client=FakeRerankClient(),
    )
    await manager.initialize()

    kb = await manager.create_knowledge_base(
        name="Limited",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        rerank_provider="Local",
        rerank_model="rerank-a",
        chunk_size=45,
        chunk_overlap=5,
        top_k_dense=10,
        top_k_sparse=10,
        top_m_final=1,
    )
    await manager.import_document(
        kb["kb_id"],
        file_name="limited.txt",
        content=(
            "SQLite keeps one useful fact here.\n\n"
            "SQLite keeps another useful fact here.\n\n"
            "SQLite keeps a third useful fact here."
        ),
    )

    result = await manager.retrieve(
        query="sqlite useful fact",
        kb_ids=[kb["kb_id"]],
        top_k=10,
    )

    assert result["total"] == 1
    assert len(result["results"]) == 1


@pytest.mark.asyncio
async def test_knowledge_manager_warns_when_rerank_fails(tmp_path, caplog):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
        rerank_client=FailingRerankClient(),
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Rerank Warning",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        rerank_provider="Local",
        rerank_model="rerank-a",
    )
    await manager.import_document(kb["kb_id"], file_name="notes.txt", content="sqlite python")

    with caplog.at_level("WARNING", logger="atri"):
        result = await manager.retrieve(query="sqlite", kb_ids=[kb["kb_id"]], top_k=1)

    assert result["results"]
    assert "Knowledge rerank failed" in caplog.text


@pytest.mark.asyncio
async def test_openai_rerank_client_preserves_openai_compatible_base_url(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"index": 0, "relevance_score": 0.9}]}

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            calls.append({"url": url, "json": json, "headers": headers})
            return FakeResponse()

    monkeypatch.setattr("core.knowledge.rerank.httpx.AsyncClient", FakeAsyncClient)
    client = OpenAIRerankClient()

    result = await client.rerank(
        selection=type(
            "Selection",
            (),
            {
                "provider_config": {
                    "base_url": "https://provider.example/v1",
                    "api_key": "sk-test",
                },
                "config": {"top_n": 1},
                "model": "rerank-a",
            },
        )(),
        query="sqlite",
        documents=["sqlite document"],
    )

    assert result == [{"index": 0, "score": 0.9}]
    assert calls[0]["url"] == "https://provider.example/v1/rerank"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_knowledge_manager_probes_embedding_dimensions_on_create(tmp_path, caplog):
    config = _config()
    config["active_embedding_models"][0]["config"]["dimensions"] = 1536
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=config,
        embedding_client=MismatchedConfigEmbeddingClient(actual=1024),
    )
    await manager.initialize()

    with caplog.at_level("WARNING", logger="atri"):
        kb = await manager.create_knowledge_base(
            name="Probed",
            embedding_provider="OpenAI",
            embedding_model="embed-a",
        )

    assert kb["embedding_dimensions"] == 1024
    assert kb["embedding_config"]["dimensions"] == 1024
    assert "using probed dimensions" in caplog.text


@pytest.mark.asyncio
async def test_knowledge_manager_repairs_empty_kb_dimensions_on_import(tmp_path):
    config = _config()
    config["active_embedding_models"][0]["config"]["dimensions"] = 1536
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=config,
        embedding_client=MismatchedConfigEmbeddingClient(actual=1024),
    )
    await manager.initialize()
    kb = manager.store.create_kb(
        {
            "name": "Legacy",
            "description": "",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "embedding_config": {"dimensions": 1536, "batch_size": 16, "encoding_format": "float"},
            "embedding_dimensions": 1536,
            "rerank_provider": "",
            "rerank_model": "",
            "rerank_config": {},
            "chunk_size": 80,
            "chunk_overlap": 10,
            "top_k_dense": 30,
            "top_k_sparse": 30,
            "top_m_final": 5,
        }
    )

    task = await manager.import_document(
        kb["kb_id"], file_name="notes.txt", content="sqlite python"
    )
    refreshed = await manager.get_knowledge_base(kb["kb_id"])

    assert task["status"] == "completed"
    assert refreshed["embedding_dimensions"] == 1024
    assert refreshed["embedding_config"]["dimensions"] == 1024


@pytest.mark.asyncio
async def test_knowledge_manager_rejects_invalid_numeric_settings(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
    )
    await manager.initialize()

    with pytest.raises(ValueError, match="top_k_dense must be >= 1"):
        await manager.create_knowledge_base(
            name="Bad Dense",
            embedding_provider="OpenAI",
            embedding_model="embed-a",
            top_k_dense=0,
        )

    kb = await manager.create_knowledge_base(
        name="Valid",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
        chunk_overlap=0,
    )
    assert kb["chunk_overlap"] == 0
    with pytest.raises(ValueError, match="top_m_final must be >= 1"):
        await manager.update_knowledge_base(kb["kb_id"], top_m_final=-1)

    with pytest.raises(ValueError, match="top_k must be >= 1"):
        await manager.retrieve(query="sqlite", kb_ids=[kb["kb_id"]], top_k=0)


@pytest.mark.asyncio
async def test_knowledge_manager_validates_model_pool_and_locks_embedding(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
    )
    await manager.initialize()

    with pytest.raises(ValueError, match="embedding model is not enabled"):
        await manager.create_knowledge_base(
            name="Bad",
            embedding_provider="OpenAI",
            embedding_model="missing",
        )

    kb = await manager.create_knowledge_base(
        name="Locked",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
    )
    await manager.import_document(kb["kb_id"], file_name="a.txt", content="python sqlite")

    with pytest.raises(ValueError, match="cannot change embedding model"):
        await manager.update_knowledge_base(
            kb["kb_id"],
            embedding_provider="Other",
            embedding_model="embed-b",
        )


@pytest.mark.asyncio
async def test_knowledge_manager_deletes_documents_and_bases(tmp_path):
    manager = KnowledgeBaseManager(
        db_path=tmp_path / "knowledge.db",
        config=_config(),
        embedding_client=FakeEmbeddingClient(),
    )
    await manager.initialize()
    kb = await manager.create_knowledge_base(
        name="Delete Me",
        embedding_provider="OpenAI",
        embedding_model="embed-a",
    )
    await manager.import_document(kb["kb_id"], file_name="a.txt", content="python sqlite")
    doc = (await manager.list_documents(kb["kb_id"]))[0]

    await manager.delete_document(doc["doc_id"])
    assert await manager.list_documents(kb["kb_id"]) == []

    await manager.delete_knowledge_base(kb["kb_id"])
    assert await manager.list_knowledge_bases() == []
