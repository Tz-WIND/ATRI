from typing import TYPE_CHECKING, cast

import pytest

from core.knowledge.manager import KnowledgeBaseManager
from dashboard import music as music_routes
from dashboard.routes import _helpers
from dashboard.server import Dashboard
from tests.test_knowledge_core import FakeEmbeddingClient, FakeRerankClient

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle


class _FakeDashboardHost:
    def set_audio_callback(self, callback):
        self.callback = callback


class _FakeLifecycle:
    def __init__(self, tmp_path):
        self.config = {
            "dashboard": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 6185,
                "username": "admin",
                "password": _helpers.hash_password("secret"),
            },
            "workspace": str(tmp_path),
            "audio_host": {},
            "mcp_servers": {},
            "model": "chat-current",
            "model_provider": "OpenAI",
            "active_models": [{"model": "chat-current", "provider": "OpenAI"}],
            "embedding_model": "embed-a",
            "embedding_provider": "OpenAI",
            "active_embedding_models": [
                {
                    "provider": "OpenAI",
                    "model": "embed-a",
                    "config": {
                        "dimensions": 3,
                        "batch_size": 16,
                        "encoding_format": "float",
                    },
                }
            ],
            "rerank_model": "rerank-a",
            "rerank_provider": "Local",
            "active_rerank_models": [
                {
                    "provider": "Local",
                    "model": "rerank-a",
                    "config": {
                        "top_n": 5,
                        "score_threshold": 0.0,
                        "max_input_tokens": 8192,
                    },
                }
            ],
            "providers": {
                "OpenAI": {"api_key": "sk-test", "base_url": "https://example.test/v1"},
                "Local": {"api_key": "", "base_url": "http://localhost:11434/v1"},
            },
            "knowledge": {"enabled": True, "active_bases": [], "top_k": 5},
        }
        self.process_stage = None
        self.onebot11 = None
        self.webchat = None
        self.start_time = 0
        self.saved = 0
        self.knowledge_manager = KnowledgeBaseManager(
            db_path=tmp_path / "knowledge.db",
            config=self.config,
            embedding_client=FakeEmbeddingClient(),
            rerank_client=FakeRerankClient(),
        )
        self.graph_manager = _FakeGraphManager()

    def save_config(self):
        self.saved += 1


class _FakeProcessStage:
    def __init__(self):
        self.updated = []

    def update_config(self, **kwargs):
        self.updated.append(kwargs)


class _FakeGraphManager:
    def __init__(self):
        self.test_result = {"ok": True, "database": "neo4j"}
        self.updated = []

    def update_config(self, config):
        self.updated.append(config)

    async def test_connection(self, config=None):
        if isinstance(self.test_result, Exception):
            raise self.test_result
        return self.test_result


async def _dashboard(monkeypatch, tmp_path) -> Dashboard:
    monkeypatch.setattr(_helpers, "_PBKDF2_ITERATIONS", 1)
    monkeypatch.setattr("core.host.configure_host_manager", lambda **kwargs: _FakeDashboardHost())
    monkeypatch.setattr(music_routes, "init_music", lambda lifecycle: None)
    lifecycle = _FakeLifecycle(tmp_path)
    await lifecycle.knowledge_manager.initialize()
    return Dashboard(cast("Lifecycle", lifecycle))


@pytest.mark.asyncio
async def test_knowledge_routes_create_import_retrieve_and_delete(monkeypatch, tmp_path):
    dashboard = await _dashboard(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    headers = {"Authorization": f"Bearer {token}"}
    client = dashboard.app.test_client()

    create_response = await client.post(
        "/api/knowledge/bases",
        json={
            "name": "Docs",
            "description": "Project knowledge",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "rerank_provider": "Local",
            "rerank_model": "rerank-a",
            "chunk_size": 80,
            "chunk_overlap": 10,
        },
        headers=headers,
    )
    created = await create_response.get_json()
    kb_id = created["kb_id"]

    import_response = await client.post(
        f"/api/knowledge/bases/{kb_id}/documents/import",
        json={
            "file_name": "notes.md",
            "content": "Python tools are useful.\n\nSQLite stores knowledge chunks.",
        },
        headers=headers,
    )
    task = await import_response.get_json()
    task_response = await client.get(
        f"/api/knowledge/tasks/{task['task_id']}",
        headers=headers,
    )
    docs_response = await client.get(
        f"/api/knowledge/bases/{kb_id}/documents",
        headers=headers,
    )
    documents = await docs_response.get_json()
    chunks_response = await client.get(
        f"/api/knowledge/documents/{documents['items'][0]['doc_id']}/chunks",
        headers=headers,
    )
    retrieve_response = await client.post(
        "/api/knowledge/retrieve",
        json={"query": "sqlite chunks", "kb_ids": [kb_id], "top_k": 1},
        headers=headers,
    )
    list_response = await client.get("/api/knowledge/bases", headers=headers)
    delete_response = await client.delete(
        f"/api/knowledge/bases/{kb_id}",
        headers=headers,
    )

    assert create_response.status_code == 200
    assert import_response.status_code == 200
    assert task_response.status_code == 200
    assert docs_response.status_code == 200
    assert chunks_response.status_code == 200
    assert retrieve_response.status_code == 200
    assert list_response.status_code == 200
    assert delete_response.status_code == 200
    assert created["embedding_model"] == "embed-a"
    assert task["status"] == "completed"
    assert (await task_response.get_json())["result"]["success_count"] == 1
    assert documents["items"][0]["doc_name"] == "notes.md"
    assert (await chunks_response.get_json())["items"]
    retrieval = await retrieve_response.get_json()
    assert retrieval["results"][0]["doc_name"] == "notes.md"
    assert "SQLite stores knowledge chunks" in retrieval["results"][0]["content"]


@pytest.mark.asyncio
async def test_knowledge_routes_validate_create_payload(monkeypatch, tmp_path):
    dashboard = await _dashboard(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()

    response = await dashboard.app.test_client().post(
        "/api/knowledge/bases",
        json={"name": "Bad", "embedding_provider": "OpenAI", "embedding_model": "missing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = await response.get_json()

    assert response.status_code == 400
    assert "embedding model is not enabled" in payload["error"]


@pytest.mark.asyncio
async def test_knowledge_routes_reject_duplicate_base_names(monkeypatch, tmp_path):
    dashboard = await _dashboard(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    headers = {"Authorization": f"Bearer {token}"}
    client = dashboard.app.test_client()

    first_response = await client.post(
        "/api/knowledge/bases",
        json={"name": "Docs", "embedding_provider": "OpenAI", "embedding_model": "embed-a"},
        headers=headers,
    )
    second_response = await client.post(
        "/api/knowledge/bases",
        json={"name": "Docs", "embedding_provider": "OpenAI", "embedding_model": "embed-a"},
        headers=headers,
    )
    other_response = await client.post(
        "/api/knowledge/bases",
        json={"name": "Other", "embedding_provider": "OpenAI", "embedding_model": "embed-a"},
        headers=headers,
    )
    first = await first_response.get_json()
    rename_response = await client.patch(
        f"/api/knowledge/bases/{first['kb_id']}",
        json={"name": "Other"},
        headers=headers,
    )

    assert first_response.status_code == 200
    assert other_response.status_code == 200
    assert second_response.status_code == 400
    assert rename_response.status_code == 400
    assert "knowledge base name already exists" in (await second_response.get_json())["error"]
    assert "knowledge base name already exists" in (await rename_response.get_json())["error"]


@pytest.mark.asyncio
async def test_knowledge_routes_reject_invalid_numeric_parameters(monkeypatch, tmp_path):
    dashboard = await _dashboard(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    headers = {"Authorization": f"Bearer {token}"}
    client = dashboard.app.test_client()

    create_response = await client.post(
        "/api/knowledge/bases",
        json={
            "name": "Bad Numbers",
            "embedding_provider": "OpenAI",
            "embedding_model": "embed-a",
            "top_k_dense": 0,
        },
        headers=headers,
    )
    chunks_response = await client.get(
        "/api/knowledge/documents/doc-id/chunks?page=1&page_size=-1",
        headers=headers,
    )
    retrieve_response = await client.post(
        "/api/knowledge/retrieve",
        json={"query": "sqlite", "kb_ids": ["kb-1"], "top_k": 0},
        headers=headers,
    )
    bad_type_response = await client.post(
        "/api/knowledge/retrieve",
        json={"query": "sqlite", "kb_ids": ["kb-1"], "top_k": "many"},
        headers=headers,
    )

    assert create_response.status_code == 400
    assert chunks_response.status_code == 400
    assert retrieve_response.status_code == 400
    assert bad_type_response.status_code == 400
    assert "top_k_dense must be >= 1" in (await create_response.get_json())["error"]
    assert "page_size must be >= 1" in (await chunks_response.get_json())["error"]
    assert "top_k must be >= 1" in (await retrieve_response.get_json())["error"]
    assert "top_k must be an integer" in (await bad_type_response.get_json())["error"]


@pytest.mark.asyncio
async def test_settings_route_persists_knowledge_chat_context(monkeypatch, tmp_path):
    dashboard = await _dashboard(monkeypatch, tmp_path)
    process_stage = _FakeProcessStage()
    dashboard.lifecycle.process_stage = process_stage
    token = dashboard._create_auth_session()
    headers = {"Authorization": f"Bearer {token}"}
    client = dashboard.app.test_client()

    update_response = await client.post(
        "/api/settings",
        json={"knowledge": {"enabled": True, "active_bases": ["kb-1"], "top_k": 3}},
        headers=headers,
    )
    get_response = await client.get("/api/settings", headers=headers)
    payload = await get_response.get_json()

    assert update_response.status_code == 200
    assert get_response.status_code == 200
    assert dashboard.lifecycle.config["knowledge"] == {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": False,
            "uri": "neo4j://localhost:7687",
            "username": "neo4j",
            "password": "",
            "database": "neo4j",
            "extraction_model": "",
            "extraction_provider": "",
            "extraction_enabled": True,
            "extraction_sources": ["documents", "chat"],
            "retrieval_enabled": True,
            "retrieval_depth": 1,
            "max_facts": 8,
            "queue_max_size": 1000,
        },
    }
    assert payload["knowledge"] == dashboard.lifecycle.config["knowledge"]
    assert process_stage.updated[-1]["knowledge"] == dashboard.lifecycle.config["knowledge"]
    assert cast(_FakeLifecycle, dashboard.lifecycle).saved == 1


@pytest.mark.asyncio
async def test_settings_route_masks_and_preserves_graph_password(monkeypatch, tmp_path):
    dashboard = await _dashboard(monkeypatch, tmp_path)
    process_stage = _FakeProcessStage()
    dashboard.lifecycle.process_stage = process_stage
    token = dashboard._create_auth_session()
    headers = {"Authorization": f"Bearer {token}"}
    client = dashboard.app.test_client()

    update_response = await client.post(
        "/api/settings",
        json={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "uri": "bolt://localhost:7687",
                    "username": "neo4j",
                    "password": "secret",
                    "database": "atri",
                    "extraction_model": "graph-chat",
                    "extraction_provider": "OpenAI",
                    "extraction_enabled": True,
                    "extraction_sources": ["documents", "chat"],
                    "retrieval_enabled": True,
                    "retrieval_depth": 2,
                    "max_facts": 6,
                    "queue_max_size": 25,
                }
            }
        },
        headers=headers,
    )
    get_response = await client.get("/api/settings", headers=headers)
    payload = await get_response.get_json()

    preserve_response = await client.post(
        "/api/settings",
        json={"knowledge": {"graph": {"password": "***", "max_facts": 4}}},
        headers=headers,
    )

    assert update_response.status_code == 200
    assert get_response.status_code == 200
    assert preserve_response.status_code == 200
    assert payload["knowledge"]["graph"]["password"] == "*" * 3
    assert payload["knowledge"]["graph"]["extraction_model"] == "graph-chat"
    assert payload["knowledge"]["graph"]["extraction_provider"] == "OpenAI"
    assert dashboard.lifecycle.config["knowledge"]["graph"]["password"] == "sec" + "ret"
    assert dashboard.lifecycle.config["knowledge"]["graph"]["retrieval_depth"] == 2
    assert dashboard.lifecycle.config["knowledge"]["graph"]["max_facts"] == 4
    assert process_stage.updated[-1]["knowledge"] == dashboard.lifecycle.config["knowledge"]
    assert dashboard.lifecycle.graph_manager.updated[-1] == dashboard.lifecycle.config


@pytest.mark.asyncio
async def test_settings_route_rejects_empty_graph_extraction_sources_when_enabled(
    monkeypatch,
    tmp_path,
):
    dashboard = await _dashboard(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    headers = {"Authorization": f"Bearer {token}"}

    response = await dashboard.app.test_client().post(
        "/api/settings",
        json={
            "knowledge": {
                "graph": {
                    "extraction_enabled": True,
                    "extraction_sources": [],
                }
            }
        },
        headers=headers,
    )
    payload = await response.get_json()

    assert response.status_code == 400
    assert "knowledge.graph.extraction_sources must contain at least one source" in payload["error"]


@pytest.mark.asyncio
async def test_knowledge_graph_test_connection_route(monkeypatch, tmp_path):
    dashboard = await _dashboard(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    headers = {"Authorization": f"Bearer {token}"}
    client = dashboard.app.test_client()

    ok_response = await client.post(
        "/api/knowledge/graph/test-connection",
        json={"uri": "bolt://localhost:7687", "username": "neo4j", "password": "secret"},
        headers=headers,
    )
    alias_response = await client.post(
        "/api/knowledge/graph/testconnection",
        json={"uri": "bolt://localhost:7687", "username": "neo4j", "password": "secret"},
        headers=headers,
    )
    dashboard.lifecycle.graph_manager.test_result = RuntimeError("neo4j offline")
    failed_response = await client.post(
        "/api/knowledge/graph/test-connection",
        json={"uri": "bolt://localhost:7687", "username": "neo4j", "password": "secret"},
        headers=headers,
    )
    dashboard.lifecycle.graph_manager.test_result = RuntimeError("database not found")
    database_failed_response = await client.post(
        "/api/knowledge/graph/test-connection",
        json={
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        headers=headers,
    )

    assert ok_response.status_code == 200
    assert await ok_response.get_json() == {"ok": True, "database": "neo4j"}
    assert alias_response.status_code == 200
    assert await alias_response.get_json() == {"ok": True, "database": "neo4j"}
    assert failed_response.status_code == 400
    assert "neo4j offline" in (await failed_response.get_json())["error"]
    assert database_failed_response.status_code == 400
    assert (
        "Neo4j database 'atri' was not found"
        in (await database_failed_response.get_json())["error"]
    )
