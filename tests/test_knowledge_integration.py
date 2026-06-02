import pytest

import core.pipeline.stages.process as process_stage_module
from core.config_schema import DEFAULT_CONFIG, normalize_config
from core.pipeline.stages.process import ProcessStage
from core.platform.message import MessageEvent


class FakeKnowledgeManager:
    def __init__(self):
        self.calls = []

    async def retrieve(self, *, query, kb_ids=None, kb_names=None, top_k=5):
        self.calls.append(
            {
                "query": query,
                "kb_ids": kb_ids,
                "kb_names": kb_names,
                "top_k": top_k,
            }
        )
        return {
            "context_text": "[Knowledge context]\n[1] Docs / notes.md#0\nSQLite stores chunks.",
            "results": [{"content": "SQLite stores chunks."}],
        }


class FakeGraphManager:
    def __init__(self, context_text="[Graph context]\n- Alice -[works_at]-> Acme"):
        self.context_text = context_text
        self.retrieve_calls = []
        self.enqueue_calls = []

    async def retrieve_context(
        self,
        *,
        query,
        source_ids=None,
        max_facts=8,
        retrieval_depth=1,
        ranking_policy="hybrid",
    ):
        self.retrieve_calls.append(
            {
                "query": query,
                "source_ids": source_ids,
                "max_facts": max_facts,
                "retrieval_depth": retrieval_depth,
                "ranking_policy": ranking_policy,
            }
        )
        return self.context_text

    def enqueue_chat_turn(self, **kwargs):
        self.enqueue_calls.append(kwargs)
        return "task_graph_chat"


def test_normalize_config_adds_knowledge_defaults():
    config, changed = normalize_config({})

    assert changed is True
    assert config["knowledge"] == DEFAULT_CONFIG["knowledge"]
    assert config["knowledge"] == {
        "enabled": False,
        "active_bases": [],
        "top_k": 5,
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
            "ranking_policy": "hybrid",
            "queue_max_size": 1000,
        },
    }


@pytest.mark.asyncio
async def test_process_stage_prepends_knowledge_context_to_current_turn():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {"enabled": True, "active_bases": ["kb-1"], "top_k": 3}
    stage.knowledge_manager = FakeKnowledgeManager()
    event = MessageEvent(message_str="How does sqlite retrieval work?")

    content = await stage._event_content_for_agent(event)

    assert content == (
        "[ATRI internal context]\n"
        "[Knowledge context]\n"
        "[1] Docs / notes.md#0\n"
        "SQLite stores chunks.\n\n"
        "[Current request]\n"
        "How does sqlite retrieval work?"
    )
    assert stage.knowledge_manager.calls == [
        {
            "query": "How does sqlite retrieval work?",
            "kb_ids": ["kb-1"],
            "kb_names": [],
            "top_k": 3,
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_appends_graph_context_without_replacing_vector_context():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 2,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = FakeKnowledgeManager()
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    content = await stage._event_content_for_agent(event)

    assert isinstance(content, str)
    assert content.startswith("[ATRI internal context]\n")
    assert "[Knowledge context]" in content
    assert "[Graph context]" in content
    assert content.endswith("[Current request]\nHow does Alice use sqlite?")
    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "How does Alice use sqlite?",
            "source_ids": [],
            "max_facts": 2,
            "retrieval_depth": 2,
            "ranking_policy": "hybrid",
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_injects_graph_context_when_vector_knowledge_is_disabled():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": False,
        "active_bases": [],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 2,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = FakeKnowledgeManager()
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    content = await stage._event_content_for_agent(event)

    assert content == (
        "[ATRI internal context]\n"
        "[Graph context]\n"
        "- Alice -[works_at]-> Acme\n\n"
        "[Current request]\n"
        "How does Alice use sqlite?"
    )
    assert stage.knowledge_manager.calls == []
    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "How does Alice use sqlite?",
            "source_ids": [],
            "max_facts": 2,
            "retrieval_depth": 2,
            "ranking_policy": "hybrid",
        }
    ]


def test_process_stage_chat_turn_enqueue_is_non_blocking():
    stage = ProcessStage()
    stage.knowledge = {
        "graph": {"enabled": True, "extraction_enabled": True, "extraction_sources": ["chat"]}
    }
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="Alice works at Acme.", platform_name="webchat")
    event.session_id = "session-1"

    stage._enqueue_graph_chat_turn(event, "Acme employs Alice.")

    assert stage.graph_manager.enqueue_calls == [
        {
            "user_text": "Alice works at Acme.",
            "assistant_text": "Acme employs Alice.",
            "session_id": "webchat:friend:session-1",
            "platform": "webchat",
            "metadata": {"message_type": "friend"},
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_passes_original_user_content_as_display_content(monkeypatch):
    class FakeLLM:
        model = "gpt-test"

    class FakeAgent:
        def __init__(self):
            self.llm = FakeLLM()
            self.messages = []
            self.high_privilege_tools_allowed = True
            self.chat_kwargs = None
            self.chat_user_input = None

        async def chat_async(self, user_input, **kwargs):
            self.chat_user_input = user_input
            self.chat_kwargs = kwargs
            self.messages = [
                {
                    "role": "user",
                    "content": user_input,
                    "_atri_display_content": kwargs.get("display_user_input"),
                },
                {"role": "assistant", "content": "ok"},
            ]
            return "ok"

    class FakeTurnRecorder:
        def __init__(self, *args, **kwargs):
            self.tool_events = []

        def record_turn_started(self):
            return None

        def on_token(self, *args, **kwargs):
            return None

        def on_tool(self, *args, **kwargs):
            return None

        def on_thinking(self, *args, **kwargs):
            return None

        def on_thinking_done(self, *args, **kwargs):
            return None

        def on_tool_start(self, *args, **kwargs):
            return None

        def on_tool_end(self, *args, **kwargs):
            return None

        def mark_thinking_done(self):
            return None

        async def drain_pending_broadcasts(self):
            return None

        async def finish_success(self, *args, **kwargs):
            return None

        def finish_error(self, *args, **kwargs):
            return None

    class FakeSessionStore:
        def __init__(self):
            self.saved_messages = None

        def save(self, messages, model, session_id):
            self.saved_messages = messages
            return session_id

    monkeypatch.setattr(process_stage_module, "_RuntimeTurnRecorder", FakeTurnRecorder)

    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": False,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 1,
            "max_facts": 2,
        },
    }
    stage.graph_manager = FakeGraphManager()
    stage._active_lock = process_stage_module.threading.Lock()
    stage._active_session_ids = set()
    stage.session_store = FakeSessionStore()
    fake_agent = FakeAgent()
    monkeypatch.setattr(stage, "_get_or_create_agent", lambda session_id: fake_agent)
    monkeypatch.setattr(stage, "_apply_event_llm_override", lambda agent, event: None)
    monkeypatch.setattr(stage, "_enqueue_graph_chat_turn", lambda event, response: None)

    event = MessageEvent(message_str="How does Alice use sqlite?")

    async for _ in stage._process_locked(event, "webchat:friend:test"):
        pass

    assert fake_agent.chat_user_input == (
        "[ATRI internal context]\n"
        "[Graph context]\n"
        "- Alice -[works_at]-> Acme\n\n"
        "[Current request]\n"
        "How does Alice use sqlite?"
    )
    assert fake_agent.chat_kwargs["display_user_input"] == "How does Alice use sqlite?"
    assert stage.session_store.saved_messages[0]["_atri_display_content"] == (
        "How does Alice use sqlite?"
    )


@pytest.mark.asyncio
async def test_process_stage_skips_knowledge_when_disabled():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {"enabled": False, "active_bases": ["kb-1"], "top_k": 3}
    stage.knowledge_manager = FakeKnowledgeManager()
    event = MessageEvent(message_str="plain request")

    content = await stage._event_content_for_agent(event)

    assert content == "plain request"
    assert stage.knowledge_manager.calls == []
