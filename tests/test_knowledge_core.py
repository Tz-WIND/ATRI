from typing import Any

import pytest

import core.knowledge.store as store_module
from core.knowledge.chunking import RecursiveTextChunker
from core.knowledge.manager import KnowledgeBaseManager
from core.knowledge.rerank import OpenAIRerankClient
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
