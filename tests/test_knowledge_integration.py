import pytest

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


def test_normalize_config_adds_knowledge_defaults():
    config, changed = normalize_config({})

    assert changed is True
    assert config["knowledge"] == DEFAULT_CONFIG["knowledge"]
    assert config["knowledge"] == {
        "enabled": False,
        "active_bases": [],
        "top_k": 5,
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
async def test_process_stage_skips_knowledge_when_disabled():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {"enabled": False, "active_bases": ["kb-1"], "top_k": 3}
    stage.knowledge_manager = FakeKnowledgeManager()
    event = MessageEvent(message_str="plain request")

    content = await stage._event_content_for_agent(event)

    assert content == "plain request"
    assert stage.knowledge_manager.calls == []
