import asyncio
import json

import pytest

from core.knowledge.extraction import normalize_extracted_facts
from core.knowledge.graph import Neo4jGraphClient
from core.knowledge.graph_worker import GraphKnowledgeManager, _chat_turn_text
from core.runtime.tasks import TaskStore


def test_normalize_extracted_facts_filters_and_deduplicates_graph_tuples():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": " Alice ",
                    "subject_type": "Person",
                    "predicate": " works_at ",
                    "object": " Acme ",
                    "object_type": "Company",
                    "confidence": "0.9",
                    "evidence": "Alice works at Acme.",
                },
                {
                    "subject": "alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "acme",
                    "object_type": "Company",
                },
                {
                    "subject": "",
                    "subject_type": "Person",
                    "predicate": "knows",
                    "object": "Bob",
                    "object_type": "Person",
                },
            ]
        },
        source_id="chunk-1",
        source_kind="document",
        default_evidence="fallback evidence",
    )

    assert len(facts) == 1
    assert facts[0]["subject"] == "Alice"
    assert facts[0]["subject_key"] == "alice"
    assert facts[0]["subject_type"] == "Person"
    assert facts[0]["subject_type_key"] == "person"
    assert facts[0]["predicate"] == "works_at"
    assert facts[0]["object"] == "Acme"
    assert facts[0]["object_key"] == "acme"
    assert facts[0]["object_type"] == "Company"
    assert facts[0]["object_type_key"] == "company"
    assert facts[0]["source_id"] == "chunk-1"
    assert facts[0]["source_kind"] == "document"
    assert facts[0]["confidence"] == 0.9
    assert facts[0]["evidence"] == "Alice works at Acme."
    assert facts[0]["fact_key"] == "person:alice|works_at|company:acme"


def test_normalize_extracted_facts_filters_chat_metadata_and_numeric_entities():
    facts = normalize_extracted_facts(
        {
            "tuples": [
                {
                    "subject": "对话记录",
                    "subject_type": "Record",
                    "predicate": "recorded_at",
                    "object": "178027 8275.00",
                    "object_type": "Timestamp",
                },
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                },
            ]
        },
        source_id="chat-task-1",
        source_kind="chat",
        default_evidence="User: Alice works at Acme.",
    )

    assert len(facts) == 1
    assert facts[0]["subject"] == "Alice"
    assert facts[0]["object"] == "Acme"


def test_chat_turn_text_does_not_include_runtime_timestamp():
    text = _chat_turn_text("Alice works at Acme.", "Noted.")

    assert "Alice works at Acme." in text
    assert "Noted." in text
    assert "Recorded at" not in text


class FakeNeo4jSession:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def run(self, query, **params):
        self.calls.append({"query": query, "params": params})
        if "FACT*1..2" in query:
            return [
                {
                    "subject": "Alice",
                    "predicate": "works_at",
                    "object": "Acme",
                    "evidence": "Alice works at Acme.",
                    "confidence": 0.9,
                    "hop": 1,
                },
                {
                    "subject": "Acme",
                    "predicate": "uses",
                    "object": "Neo4j",
                    "evidence": "Acme uses Neo4j.",
                    "confidence": 0.8,
                    "hop": 2,
                },
            ]
        if "RETURN s.name AS subject" in query:
            return [
                {
                    "subject": "Alice",
                    "predicate": "works_at",
                    "object": "Acme",
                    "evidence": "Alice works at Acme.",
                    "confidence": 0.9,
                }
            ]
        return []


class FakeNeo4jDriver:
    def __init__(self):
        self.session_obj = FakeNeo4jSession()
        self.closed = False
        self.verified = False

    def verify_connectivity(self):
        self.verified = True

    def session(self, database=None):
        self.database = database
        return self.session_obj

    def close(self):
        self.closed = True


def test_neo4j_graph_client_initializes_upserts_and_retrieves_context():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
            "max_facts": 8,
        },
        driver_factory=lambda uri, auth: driver,
    )
    fact = normalize_extracted_facts(
        [
            {
                "subject": "Alice",
                "subject_type": "Person",
                "predicate": "works_at",
                "object": "Acme",
                "object_type": "Company",
            }
        ],
        source_id="chunk-1",
        source_kind="document",
        metadata={"chunk_ids": ["chunk-1"], "label": "测试"},
    )[0]

    client.initialize()
    count = client.upsert_facts([fact])
    context = client.retrieve_context(query="Alice Acme", source_ids=["chunk-1"], max_facts=3)
    client.close()

    queries = "\n".join(call["query"] for call in driver.session_obj.calls)
    assert driver.verified is True
    assert driver.database == "atri"
    assert "CREATE CONSTRAINT" in queries
    assert "DROP CONSTRAINT entity_name_key IF EXISTS" in queries
    assert "REQUIRE (e.name_key, e.type_key) IS UNIQUE" in queries
    assert (
        "MERGE (s:Entity {name_key: fact.subject_key, type_key: fact.subject_type_key})" in queries
    )
    assert "MERGE (o:Entity {name_key: fact.object_key, type_key: fact.object_type_key})" in queries
    assert "apoc." not in queries
    assert "r.metadata_json = fact.metadata_json" in queries
    assert "source_count = coalesce" not in queries
    assert "r.source_count = size(source_ids)" in queries
    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    upsert_row = upsert_call["params"]["facts"][0]
    assert json.loads(upsert_row["metadata_json"]) == {
        "chunk_ids": ["chunk-1"],
        "label": "测试",
    }
    assert count == 1
    assert context == "[Graph context]\n- Alice -[works_at]-> Acme (Alice works at Acme.)"
    assert driver.closed is True


def test_neo4j_graph_client_keeps_same_name_different_types_separate():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )
    facts = normalize_extracted_facts(
        [
            {
                "subject": "Apple",
                "subject_type": "Company",
                "predicate": "makes",
                "object": "iPhone",
                "object_type": "Product",
            },
            {
                "subject": "Apple",
                "subject_type": "Product",
                "predicate": "has_color",
                "object": "Red",
                "object_type": "Color",
            },
        ],
        source_id="chunk-1",
        source_kind="document",
    )

    client.upsert_facts(facts)

    upsert_call = next(call for call in driver.session_obj.calls if "facts" in call["params"])
    rows = upsert_call["params"]["facts"]
    assert rows[0]["subject_key"] == rows[1]["subject_key"] == "apple"
    assert rows[0]["subject_type_key"] == "company"
    assert rows[1]["subject_type_key"] == "product"
    assert rows[0]["fact_key"] != rows[1]["fact_key"]


def test_neo4j_graph_client_retrieves_limited_multihop_context():
    driver = FakeNeo4jDriver()
    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "secret",
            "database": "atri",
        },
        driver_factory=lambda uri, auth: driver,
    )

    context = client.retrieve_context(
        query="Alice",
        source_ids=[],
        max_facts=4,
        retrieval_depth=2,
    )

    assert "FACT*1..2" in driver.session_obj.calls[-1]["query"]
    assert driver.session_obj.calls[-1]["params"]["limit"] == 4
    assert context == (
        "[Graph context]\n"
        "- [1-hop] Alice -[works_at]-> Acme (Alice works at Acme.)\n"
        "- [2-hop] Acme -[uses]-> Neo4j (Acme uses Neo4j.)"
    )


def test_neo4j_graph_client_reconnects_when_connection_config_changes():
    drivers = [FakeNeo4jDriver(), FakeNeo4jDriver()]
    calls = []

    def driver_factory(uri, auth):
        calls.append({"uri": uri, "auth": auth})
        return drivers[len(calls) - 1]

    client = Neo4jGraphClient(
        {
            "enabled": True,
            "uri": "bolt://old:7687",
            "username": "neo4j",
            "password": "old-secret",
            "database": "old-db",
        },
        driver_factory=driver_factory,
    )

    client.initialize()
    client.update_config(
        {
            "enabled": True,
            "uri": "bolt://new:7687",
            "username": "neo4j",
            "password": "new-secret",
            "database": "new-db",
        }
    )
    context = client.retrieve_context(query="Alice", source_ids=[], max_facts=1)

    assert drivers[0].closed is True
    assert drivers[1].verified is True
    assert drivers[1].database == "new-db"
    assert calls == [
        {"uri": "bolt://old:7687", "auth": ("neo4j", "old-secret")},
        {"uri": "bolt://new:7687", "auth": ("neo4j", "new-secret")},
    ]
    assert context == "[Graph context]\n- Alice -[works_at]-> Acme (Alice works at Acme.)"


class FakeExtractor:
    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
        )


class RecordingExtractor:
    def __init__(self):
        self.calls = []

    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        self.calls.append(
            {
                "text": text,
                "source_id": source_id,
                "source_kind": source_kind,
                "metadata": dict(metadata or {}),
            }
        )
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
            metadata=metadata,
        )


class HangingExtractor:
    def __init__(self):
        self.started = asyncio.Event()

    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        self.started.set()
        await asyncio.Event().wait()
        return []


class RetryThenSucceedExtractor:
    def __init__(self, failures_before_success=1):
        self.failures_before_success = failures_before_success
        self.calls = []

    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        self.calls.append({"text": text, "source_id": source_id, "source_kind": source_kind})
        if len(self.calls) <= self.failures_before_success:
            raise ValueError("invalid extraction JSON")
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
            metadata=metadata,
        )


class FailingSecondBatchExtractor:
    def __init__(self):
        self.calls = []

    async def extract_facts(self, text, *, source_id, source_kind, metadata=None):
        self.calls.append({"text": text, "source_id": source_id, "source_kind": source_kind})
        if "Bad batch" in text:
            raise ValueError("invalid extraction JSON")
        return normalize_extracted_facts(
            [
                {
                    "subject": "Alice",
                    "subject_type": "Person",
                    "predicate": "works_at",
                    "object": "Acme",
                    "object_type": "Company",
                }
            ],
            source_id=source_id,
            source_kind=source_kind,
            metadata=metadata,
        )


class FakeGraphClient:
    def __init__(self):
        self.facts = []
        self.retrieve_calls = []
        self.initialized = 0

    def update_config(self, config):
        self.config = config

    def initialize(self):
        self.initialized += 1
        return None

    def upsert_facts(self, facts):
        self.facts.extend(facts)
        return len(facts)

    def retrieve_context(self, *, query, source_ids=None, max_facts=8, retrieval_depth=1):
        self.retrieve_calls.append(
            {
                "query": query,
                "source_ids": source_ids,
                "max_facts": max_facts,
                "retrieval_depth": retrieval_depth,
            }
        )
        return "[Graph context]\n- Alice -[works_at]-> Acme"

    def close(self):
        return None


@pytest.mark.asyncio
async def test_graph_manager_skips_worker_when_graph_disabled(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": False,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=graph,
        extractor=FakeExtractor(),
        task_store=store,
    )
    try:
        await manager.initialize()

        assert manager.queue is None
        assert manager._worker_task is None
        assert graph.initialized == 0
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_update_config_starts_worker_after_graph_is_enabled(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": False,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=graph,
        extractor=FakeExtractor(),
        task_store=store,
    )
    try:
        await manager.initialize()

        assert manager.queue is None
        assert manager._worker_task is None
        manager.update_config(
            {
                "knowledge": {
                    "graph": {
                        "enabled": True,
                        "extraction_enabled": True,
                        "extraction_sources": ["chat"],
                        "queue_max_size": 10,
                    }
                }
            }
        )
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )
        await manager.drain(wait_seconds=2)

        assert manager.queue is not None
        assert manager._worker_task is not None
        assert task_id is not None
        assert store.get_task(task_id)["status"] == "completed"
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_processes_document_jobs_in_background(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["documents"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=graph,
        extractor=FakeExtractor(),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_document(
            kb_id="kb-1",
            doc_id="doc-1",
            doc_name="notes.txt",
            chunks=[{"chunk_id": "chunk-1", "content": "Alice works at Acme."}],
        )
        await manager.drain(wait_seconds=2)

        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert graph.facts[0]["source_id"] == "chunk-1"
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_batches_document_chunks_without_losing_source_ids(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = RecordingExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["documents"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=graph,
        extractor=extractor,
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_document(
            kb_id="kb-1",
            doc_id="doc-1",
            doc_name="notes.txt",
            chunks=[
                {"chunk_id": "chunk-1", "content": "Alice works at Acme."},
                {"chunk_id": "chunk-2", "content": "Acme uses Neo4j."},
                {"chunk_id": "chunk-3", "content": "Neo4j stores facts."},
            ],
        )
        await manager.drain(wait_seconds=2)

        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert len(extractor.calls) == 1
        assert "[Chunk 1]" in extractor.calls[0]["text"]
        assert "[Chunk 3]" in extractor.calls[0]["text"]
        assert "chunk-1" not in extractor.calls[0]["text"]
        assert "chunk-3" not in extractor.calls[0]["text"]
        assert extractor.calls[0]["source_id"] == "chunk-1"
        assert extractor.calls[0]["metadata"]["chunk_ids"] == [
            "chunk-1",
            "chunk-2",
            "chunk-3",
        ]
        assert graph.facts[0]["source_id"] == "chunk-1"
        assert graph.facts[0]["source_ids"] == ["chunk-1", "chunk-2", "chunk-3"]
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_chat_facts_use_stable_source_id_not_task_id(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=graph,
        extractor=FakeExtractor(),
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )
        await manager.drain(wait_seconds=2)

        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert graph.facts[0]["source_id"] == "chat:webchat:friend:session-1"
        assert graph.facts[0]["source_id"] != task_id
        assert "extraction_task_id" not in graph.facts[0]["metadata"]
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_retries_transient_extraction_failures(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = RetryThenSucceedExtractor(failures_before_success=2)
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=graph,
        extractor=extractor,
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )
        await manager.drain(wait_seconds=2)

        task = store.get_task(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert len(extractor.calls) == 3
        assert len(graph.facts) == 1
        assert graph.facts[0]["subject"] == "Alice"
    finally:
        await manager.close()
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_skips_document_batches_after_retries_fail(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    extractor = FailingSecondBatchExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["documents"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=graph,
        extractor=extractor,
        task_store=store,
    )
    try:
        await manager.initialize()
        task_id = manager.enqueue_document(
            kb_id="kb-1",
            doc_id="doc-1",
            doc_name="notes.txt",
            chunks=[
                {"chunk_id": "chunk-1", "content": "Alice works at Acme."},
                {"chunk_id": "chunk-2", "content": "Bad batch. " * 1200},
            ],
        )
        await manager.drain(wait_seconds=2)

        task = store.get_task(task_id)
        events = store.events(task_id)
        assert task is not None
        assert task["status"] == "completed"
        assert task["metadata"]["failed_extraction_count"] == 1
        assert len(extractor.calls) == 4
        assert len(graph.facts) == 1
        assert graph.facts[0]["source_id"] == "chunk-1"
        assert any(event.event_type == "graph_extraction_skipped" for event in events)
    finally:
        await manager.close()
        store.close()


def test_graph_manager_enqueue_is_safe_when_disabled_or_full(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 0,
                }
            }
        },
        graph_client=FakeGraphClient(),
        extractor=FakeExtractor(),
        task_store=store,
    )
    try:
        assert (
            manager.enqueue_chat_turn(
                user_text="Alice works at Acme.",
                assistant_text="Noted.",
                session_id="webchat:friend:default",
                platform="webchat",
                metadata={},
            )
            is None
        )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_close_marks_running_extraction_interrupted(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    extractor = HangingExtractor()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_enabled": True,
                    "extraction_sources": ["chat"],
                    "queue_max_size": 10,
                }
            }
        },
        graph_client=FakeGraphClient(),
        extractor=extractor,
        task_store=store,
    )
    unrelated = store.create_task(kind="sub_agent", title="agent", input_text="work")
    store.start_task(unrelated)
    try:
        await manager.initialize()
        task_id = manager.enqueue_chat_turn(
            user_text="Alice works at Acme.",
            assistant_text="Noted.",
            session_id="webchat:friend:session-1",
            platform="webchat",
            metadata={"message_type": "friend"},
        )
        await asyncio.wait_for(extractor.started.wait(), timeout=2)

        running_task = store.get_task(task_id)
        assert running_task is not None
        assert running_task["status"] == "running"

        await manager.close(drain_seconds=0.01)

        interrupted_task = store.get_task(task_id)
        unrelated_task = store.get_task(unrelated)
        assert interrupted_task is not None
        assert unrelated_task is not None
        assert interrupted_task["status"] == "interrupted"
        assert "graph extraction interrupted" in interrupted_task["error"]
        assert unrelated_task["status"] == "running"
    finally:
        if manager._worker_task is not None:
            await manager.close(drain_seconds=0)
        store.close()


def test_graph_manager_uses_configured_extraction_model_from_chat_pool(monkeypatch, tmp_path):
    captured = {}

    class FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("core.knowledge.graph_worker.LLM", FakeLLM)
    store = TaskStore(tmp_path / "runtime")
    manager = GraphKnowledgeManager(
        config={
            "model": "chat-current",
            "model_provider": "Fallback",
            "api_key": "fallback-root-key",
            "base_url": "https://fallback.test/v1",
            "api_format": "openai",
            "providers": {
                "Fallback": {
                    "api_key": "fallback-provider-key",
                    "base_url": "https://fallback-provider.test/v1",
                    "api_format": "openai",
                },
                "OpenAI": {
                    "api_key": "graph-key",
                    "base_url": "https://graph.test/v1",
                    "api_format": "openai",
                },
            },
            "active_models": [
                {
                    "model": "graph-chat",
                    "provider": "OpenAI",
                    "config": {"temperature": 0.2, "max_tokens": 2048},
                }
            ],
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "extraction_model": "graph-chat",
                    "extraction_provider": "OpenAI",
                }
            },
        },
        graph_client=FakeGraphClient(),
        extractor=FakeExtractor(),
        task_store=store,
    )
    try:
        manager._create_llm()

        assert captured["model"] == "graph-chat"
        assert captured["api_key"] == "graph-key"
        assert captured["base_url"] == "https://graph.test/v1"
        assert captured["api_format"] == "openai"
        assert captured["temperature"] == 0.2
        assert captured["max_tokens"] == 2048
    finally:
        store.close()


@pytest.mark.asyncio
async def test_graph_manager_passes_retrieval_depth_to_graph_client(tmp_path):
    store = TaskStore(tmp_path / "runtime")
    graph = FakeGraphClient()
    manager = GraphKnowledgeManager(
        config={
            "knowledge": {
                "graph": {
                    "enabled": True,
                    "retrieval_enabled": True,
                    "retrieval_depth": 3,
                }
            }
        },
        graph_client=graph,
        extractor=FakeExtractor(),
        task_store=store,
    )
    try:
        context = await manager.retrieve_context(query="Alice", source_ids=[], max_facts=5)

        assert context == "[Graph context]\n- Alice -[works_at]-> Acme"
        assert graph.retrieve_calls == [
            {
                "query": "Alice",
                "source_ids": [],
                "max_facts": 5,
                "retrieval_depth": 3,
            }
        ]
    finally:
        store.close()
