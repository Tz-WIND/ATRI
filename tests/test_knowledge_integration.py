import asyncio
import logging
from datetime import datetime

import pytest

import core.pipeline.stages.process as process_stage_module
from core.config_schema import DEFAULT_CONFIG, normalize_config
from core.pipeline.stages.process import ProcessStage
from core.platform.message import MessageEvent

EXPECTED_GRAPH_RETRIEVAL_DEFAULT_DEPTH = 3


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
        source_scores=None,
        max_facts=8,
        retrieval_depth=1,
        ranking_policy="hybrid",
        expansion_candidate_limit=40,
    ):
        call = {
            "query": query,
            "source_ids": source_ids,
            "max_facts": max_facts,
            "retrieval_depth": retrieval_depth,
            "ranking_policy": ranking_policy,
            "expansion_candidate_limit": expansion_candidate_limit,
        }
        if source_scores:
            call["source_scores"] = source_scores
        self.retrieve_calls.append(call)
        return self.context_text

    def enqueue_chat_turn(self, **kwargs):
        self.enqueue_calls.append(kwargs)
        return "task_graph_chat"


async def _event_set_within(event: asyncio.Event, seconds: float) -> bool:
    try:
        async with asyncio.timeout(seconds):
            await event.wait()
    except TimeoutError:
        return False
    return True


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
            "retrieval_depth": EXPECTED_GRAPH_RETRIEVAL_DEFAULT_DEPTH,
            "max_facts": 8,
            "expansion_candidate_limit": 40,
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
            "expansion_candidate_limit": 64,
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
            "expansion_candidate_limit": 64,
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_uses_default_graph_retrieval_depth():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": False,
        "active_bases": [],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "max_facts": 2,
        },
    }
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="Trace the alert chain.")

    await stage._event_content_for_agent(event)

    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "Trace the alert chain.",
            "source_ids": [],
            "max_facts": 2,
            "retrieval_depth": EXPECTED_GRAPH_RETRIEVAL_DEFAULT_DEPTH,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 40,
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_raises_graph_depth_for_count_questions():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": False,
        "active_bases": [],
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 1,
            "max_facts": 2,
            "expansion_candidate_limit": 40,
        },
    }
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="Alice 有多少项目?")

    await stage._knowledge_context_for_event(event)

    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "Alice 有多少项目?",
            "source_ids": [],
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 120,
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_raises_graph_candidate_limit_for_each_item_questions():
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": False,
        "active_bases": [],
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 2,
            "max_facts": 2,
            "expansion_candidate_limit": 64,
        },
    }
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="分别列出每个项目的负责人")

    await stage._knowledge_context_for_event(event)

    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "分别列出每个项目的负责人",
            "source_ids": [],
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 120,
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_starts_graph_retrieval_while_vector_retrieval_is_pending():
    class BlockingKnowledgeManager:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
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
            self.started.set()
            await self.release.wait()
            return {
                "context_text": "[Knowledge context]\n[1] Docs / notes.md#0\nSQLite stores chunks.",
                "results": [{"chunk_id": "chunk-1", "content": "SQLite stores chunks."}],
            }

    class ObservingGraphManager(FakeGraphManager):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def retrieve_context(self, **kwargs):
            self.started.set()
            return await super().retrieve_context(**kwargs)

    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = BlockingKnowledgeManager()
    stage.graph_manager = ObservingGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    context_task = asyncio.create_task(stage._knowledge_context_for_event(event))
    await asyncio.wait_for(stage.knowledge_manager.started.wait(), timeout=1)
    graph_started_while_vector_pending = await _event_set_within(
        stage.graph_manager.started,
        seconds=0.1,
    )
    stage.knowledge_manager.release.set()
    context = await asyncio.wait_for(context_task, timeout=1)

    assert graph_started_while_vector_pending is True
    assert "[Knowledge context]" in context
    assert "[Graph context]" in context


@pytest.mark.asyncio
async def test_process_stage_uses_fast_vector_source_ids_for_first_graph_retrieval():
    class FastKnowledgeManager(FakeKnowledgeManager):
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
                "results": [{"chunk_id": "chunk-1", "content": "SQLite stores chunks."}],
            }

    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = FastKnowledgeManager()
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    context = await stage._knowledge_context_for_event(event)

    assert "[Knowledge context]" in context
    assert "[Graph context]" in context
    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "How does Alice use sqlite?",
            "source_ids": ["chunk-1"],
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 40,
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_passes_vector_scores_to_graph_retrieval():
    class ScoredKnowledgeManager(FakeKnowledgeManager):
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
                "results": [
                    {"chunk_id": "chunk-1", "content": "SQLite stores chunks.", "score": 0.82},
                    {"chunk_id": "chunk-2", "content": "Graph stores facts.", "score": 0.41},
                ],
            }

    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = ScoredKnowledgeManager()
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    await stage._knowledge_context_for_event(event)

    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "How does Alice use sqlite?",
            "source_ids": ["chunk-1", "chunk-2"],
            "source_scores": {"chunk-1": 0.82, "chunk-2": 0.41},
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 40,
        }
    ]


def test_retrieval_source_scores_keep_highest_valid_score_per_chunk():
    result = {
        "results": [
            {"chunk_id": "chunk-1", "score": 0.25},
            {"chunk_id": "chunk-2", "score": "bad"},
            {"chunk_id": "chunk-1", "score": 0.75},
            {"chunk_id": "chunk-3", "score": -1.0},
        ]
    }

    assert process_stage_module._retrieval_source_scores(result) == {"chunk-1": 0.75}


@pytest.mark.asyncio
async def test_process_stage_adapts_anchor_wait_after_observed_vector_latency():
    class SlowKnowledgeManager(FakeKnowledgeManager):
        async def retrieve(self, *, query, kb_ids=None, kb_names=None, top_k=5):
            self.calls.append(
                {
                    "query": query,
                    "kb_ids": kb_ids,
                    "kb_names": kb_names,
                    "top_k": top_k,
                }
            )
            await asyncio.sleep(process_stage_module._GRAPH_SOURCE_ANCHOR_WAIT_SECONDS + 0.03)
            return {
                "context_text": "[Knowledge context]\n[1] Docs / notes.md#0\nSQLite stores chunks.",
                "results": [{"chunk_id": "chunk-1", "content": "SQLite stores chunks."}],
            }

    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = SlowKnowledgeManager()
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    await stage._knowledge_context_for_event(event)
    stage.graph_manager.retrieve_calls.clear()

    await stage._knowledge_context_for_event(event)

    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "How does Alice use sqlite?",
            "source_ids": ["chunk-1"],
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 40,
        }
    ]


@pytest.mark.asyncio
async def test_process_stage_retries_empty_graph_retrieval_with_late_vector_source_ids(caplog):
    class SlowKnowledgeManager(FakeKnowledgeManager):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def retrieve(self, *, query, kb_ids=None, kb_names=None, top_k=5):
            self.calls.append(
                {
                    "query": query,
                    "kb_ids": kb_ids,
                    "kb_names": kb_names,
                    "top_k": top_k,
                }
            )
            self.started.set()
            await self.release.wait()
            return {
                "context_text": "[Knowledge context]\n[1] Docs / notes.md#0\nSQLite stores chunks.",
                "results": [{"chunk_id": "chunk-1", "content": "SQLite stores chunks."}],
            }

    class RetryGraphManager(FakeGraphManager):
        def __init__(self):
            super().__init__()
            self.initial_unanchored_retrieval = asyncio.Event()

        async def retrieve_context(self, **kwargs):
            await super().retrieve_context(**kwargs)
            if not kwargs.get("source_ids"):
                self.initial_unanchored_retrieval.set()
                return ""
            return self.context_text

    caplog.set_level(logging.INFO, logger="atri")
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = SlowKnowledgeManager()
    stage.graph_manager = RetryGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    context_task = asyncio.create_task(stage._knowledge_context_for_event(event))
    await asyncio.wait_for(stage.knowledge_manager.started.wait(), timeout=1)
    await asyncio.sleep(process_stage_module._GRAPH_SOURCE_ANCHOR_WAIT_SECONDS + 0.02)
    await asyncio.wait_for(
        stage.graph_manager.initial_unanchored_retrieval.wait(),
        timeout=1,
    )
    stage.knowledge_manager.release.set()
    context = await asyncio.wait_for(context_task, timeout=1)

    assert "[Knowledge context]" in context
    assert "[Graph context]" in context
    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "How does Alice use sqlite?",
            "source_ids": [],
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 40,
        },
        {
            "query": "How does Alice use sqlite?",
            "source_ids": ["chunk-1"],
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 40,
        },
    ]
    assert "source_ids_count=1" in caplog.text
    assert "graph_anchored=False" in caplog.text
    assert "graph_retry=True" in caplog.text


@pytest.mark.asyncio
async def test_process_stage_skips_late_anchored_retry_when_unanchored_graph_has_context(caplog):
    class SlowKnowledgeManager(FakeKnowledgeManager):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def retrieve(self, *, query, kb_ids=None, kb_names=None, top_k=5):
            self.calls.append(
                {
                    "query": query,
                    "kb_ids": kb_ids,
                    "kb_names": kb_names,
                    "top_k": top_k,
                }
            )
            self.started.set()
            await self.release.wait()
            return {
                "context_text": "[Knowledge context]\n[1] Docs / notes.md#0\nSQLite stores chunks.",
                "results": [{"chunk_id": "chunk-1", "content": "SQLite stores chunks."}],
            }

    class AnchoredGraphManager(FakeGraphManager):
        def __init__(self):
            super().__init__()
            self.initial_unanchored_retrieval = asyncio.Event()

        async def retrieve_context(self, **kwargs):
            await super().retrieve_context(**kwargs)
            if not kwargs.get("source_ids"):
                self.initial_unanchored_retrieval.set()
                return "[Graph context]\n- generic graph result"
            return "[Graph context]\n- anchored graph result"

    caplog.set_level(logging.INFO, logger="atri")
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = SlowKnowledgeManager()
    stage.graph_manager = AnchoredGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    context_task = asyncio.create_task(stage._knowledge_context_for_event(event))
    await asyncio.wait_for(stage.knowledge_manager.started.wait(), timeout=1)
    await asyncio.sleep(process_stage_module._GRAPH_SOURCE_ANCHOR_WAIT_SECONDS + 0.02)
    await asyncio.wait_for(
        stage.graph_manager.initial_unanchored_retrieval.wait(),
        timeout=1,
    )
    stage.knowledge_manager.release.set()
    context = await asyncio.wait_for(context_task, timeout=1)

    assert "[Knowledge context]" in context
    assert "- generic graph result" in context
    assert "- anchored graph result" not in context
    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "How does Alice use sqlite?",
            "source_ids": [],
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 40,
        },
    ]
    assert "graph_retry=False" in caplog.text


@pytest.mark.asyncio
async def test_process_stage_keeps_generic_graph_context_when_late_anchored_retry_is_empty(
    caplog,
):
    class SlowKnowledgeManager(FakeKnowledgeManager):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def retrieve(self, *, query, kb_ids=None, kb_names=None, top_k=5):
            self.calls.append(
                {
                    "query": query,
                    "kb_ids": kb_ids,
                    "kb_names": kb_names,
                    "top_k": top_k,
                }
            )
            self.started.set()
            await self.release.wait()
            return {
                "context_text": "[Knowledge context]\n[1] Docs / notes.md#0\nSQLite stores chunks.",
                "results": [{"chunk_id": "chunk-1", "content": "SQLite stores chunks."}],
            }

    class EmptyAnchoredRetryGraphManager(FakeGraphManager):
        def __init__(self):
            super().__init__()
            self.initial_unanchored_retrieval = asyncio.Event()

        async def retrieve_context(self, **kwargs):
            await super().retrieve_context(**kwargs)
            if not kwargs.get("source_ids"):
                self.initial_unanchored_retrieval.set()
                return "[Graph context]\n- generic graph result"
            return ""

    caplog.set_level(logging.INFO, logger="atri")
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = SlowKnowledgeManager()
    stage.graph_manager = EmptyAnchoredRetryGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    context_task = asyncio.create_task(stage._knowledge_context_for_event(event))
    await asyncio.wait_for(stage.knowledge_manager.started.wait(), timeout=1)
    await asyncio.sleep(process_stage_module._GRAPH_SOURCE_ANCHOR_WAIT_SECONDS + 0.02)
    await asyncio.wait_for(
        stage.graph_manager.initial_unanchored_retrieval.wait(),
        timeout=1,
    )
    stage.knowledge_manager.release.set()
    context = await asyncio.wait_for(context_task, timeout=1)

    assert "[Knowledge context]" in context
    assert "- generic graph result" in context
    assert stage.graph_manager.retrieve_calls == [
        {
            "query": "How does Alice use sqlite?",
            "source_ids": [],
            "max_facts": 2,
            "retrieval_depth": 3,
            "ranking_policy": "hybrid",
            "expansion_candidate_limit": 40,
        },
    ]
    assert "graph_retry=False" in caplog.text


@pytest.mark.asyncio
async def test_process_stage_logs_knowledge_context_retrieval_metrics(caplog):
    class FastKnowledgeManager(FakeKnowledgeManager):
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
                "results": [{"chunk_id": "chunk-1", "content": "SQLite stores chunks."}],
            }

    caplog.set_level(logging.INFO, logger="atri")
    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = FastKnowledgeManager()
    stage.graph_manager = FakeGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    await stage._knowledge_context_for_event(event)

    assert "Knowledge context retrieval done" in caplog.text
    assert "source_ids_count=1" in caplog.text
    assert "graph_retry=False" in caplog.text


@pytest.mark.asyncio
async def test_process_stage_cancels_pending_retrieval_tasks_when_context_is_cancelled():
    class BlockingKnowledgeManager:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def retrieve(self, *, query, kb_ids=None, kb_names=None, top_k=5):
            self.started.set()
            await self.release.wait()
            return {"context_text": "", "results": []}

    class CancellableGraphManager(FakeGraphManager):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()

        async def retrieve_context(self, **kwargs):
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return await super().retrieve_context(**kwargs)

    stage = ProcessStage()
    stage.image_transcription = {"enabled": False}
    stage.knowledge = {
        "enabled": True,
        "active_bases": ["kb-1"],
        "top_k": 3,
        "graph": {
            "enabled": True,
            "retrieval_enabled": True,
            "retrieval_depth": 3,
            "max_facts": 2,
        },
    }
    stage.knowledge_manager = BlockingKnowledgeManager()
    stage.graph_manager = CancellableGraphManager()
    event = MessageEvent(message_str="How does Alice use sqlite?")

    context_task = asyncio.create_task(stage._knowledge_context_for_event(event))
    try:
        await asyncio.wait_for(stage.knowledge_manager.started.wait(), timeout=1)
        await asyncio.wait_for(stage.graph_manager.started.wait(), timeout=1)

        context_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await context_task

        graph_was_cancelled = await _event_set_within(stage.graph_manager.cancelled, seconds=0.1)
    finally:
        stage.knowledge_manager.release.set()
        stage.graph_manager.release.set()
        await asyncio.sleep(0)

    assert graph_was_cancelled is True


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
            "expansion_candidate_limit": 40,
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
            "metadata": {
                "message_type": "friend",
                "reference_date": datetime.now().strftime("%Y-%m-%d"),
            },
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
    fake_session_store = FakeSessionStore()
    stage.session_store = fake_session_store  # type: ignore[assignment]
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
    assert fake_agent.chat_kwargs is not None
    assert fake_agent.chat_kwargs["display_user_input"] == "How does Alice use sqlite?"
    assert fake_session_store.saved_messages is not None
    assert fake_session_store.saved_messages[0]["_atri_display_content"] == (
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
