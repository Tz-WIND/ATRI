import pytest

from core.knowledge.chunking import RecursiveTextChunker
from core.knowledge.manager import KnowledgeBaseManager
from core.knowledge.rerank import OpenAIRerankClient


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
