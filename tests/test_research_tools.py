import asyncio
import concurrent.futures
import threading
from types import SimpleNamespace

import pytest

from core.agent.agent import Agent
from core.knowledge import GraphFactHit, GraphSearchResult
from core.research import ResearchPolicy, ResearchSession
from core.tools import create_tools
from core.tools.knowledge_search import GraphRagSearchTool, RagSearchTool


class _KnowledgeStore:
    def __init__(self):
        self.thread_ids: list[int] = []

    def chunks_by_ids(self, chunk_ids):
        self.thread_ids.append(threading.get_ident())
        assert chunk_ids == ["chunk-1"]
        return [
            {
                "chunk_id": "chunk-1",
                "kb_name": "Architecture",
                "doc_name": "cache.md",
                "chunk_index": 12,
            }
        ]


class _KnowledgeManager:
    def __init__(self):
        self.calls: list[dict] = []
        self.store = _KnowledgeStore()

    async def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "query": kwargs["query"],
            "results": [
                {
                    "chunk_id": "chunk-1",
                    "kb_id": "kb-active",
                    "kb_name": "Architecture",
                    "doc_id": "doc-1",
                    "doc_name": "cache.md",
                    "chunk_index": 12,
                    "content": "Cache entries are invalidated by version.",
                    "score": 0.91,
                }
            ],
            "total": 1,
            "context_text": "context",
        }


class _GraphManager:
    def __init__(self):
        self.calls: list[dict] = []

    async def search_facts(self, **kwargs):
        self.calls.append(kwargs)
        return GraphSearchResult(
            query=kwargs["query"],
            facts=[
                GraphFactHit(
                    fact_key="fact-1",
                    subject="cache",
                    predicate="invalidated_by",
                    object="version",
                    hop=1,
                    graph_score=0.88,
                    confidence=0.93,
                    evidence="Version changes invalidate cache entries.",
                    source_ids=["chunk-1"],
                    source_refs=[],
                )
            ],
            context_text="cache -[invalidated_by]-> version",
            diagnostics={"graph_cache_hit": False},
        )


@pytest.fixture
def loop_services():
    from core.research.services import ResearchServices

    loop = asyncio.new_event_loop()
    loop_thread_id: list[int] = []

    def run_loop():
        asyncio.set_event_loop(loop)
        loop_thread_id.append(threading.get_ident())
        loop.run_forever()

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    while not loop_thread_id:
        thread.join(0.001)

    knowledge = _KnowledgeManager()
    graph = _GraphManager()
    services = ResearchServices(
        loop=loop,
        knowledge_manager=knowledge,
        graph_manager=graph,
        knowledge_config_provider=lambda: {"active_bases": ["kb-active"]},
        graph_config_provider=lambda: {"retrieval_timeout_seconds": 7},
    )
    services.knowledge = knowledge
    services.graph = graph
    services.loop_thread_id = loop_thread_id[0]
    yield services
    services.cancel_pending()
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    loop.close()


@pytest.fixture
def session(tmp_path):
    return ResearchSession(
        policy=ResearchPolicy.from_config({}, tmp_path),
        turn_id="turn-1",
        session_id="session-1",
        original_user_request="research cache invalidation",
    )


def test_tool_factory_always_exposes_independent_knowledge_tools(tmp_path):
    tools = {tool.name: tool for tool in create_tools(str(tmp_path))}

    assert {"rag_search", "graphrag_search"} <= set(tools)
    assert tools["rag_search"].capabilities.read_only is True
    assert tools["rag_search"].capabilities.supports_parallel is True
    assert tools["graphrag_search"].capabilities.read_only is True
    assert tools["graphrag_search"].capabilities.supports_parallel is True


def test_rag_search_uses_active_bases_and_registers_evidence(loop_services, session):
    tool = RagSearchTool(
        ".",
        services=loop_services,
        research_session_provider=lambda: session,
        research_branch_provider=lambda: "branch-a",
    )

    text = tool.execute(query="cache", top_k=3)

    assert "[R1]" in text
    assert "cache.md#12" in text
    assert loop_services.knowledge.calls[0]["kb_ids"] == ["kb-active"]
    assert session.ledger.get("R1").branch_id == "branch-a"
    assert session.budget.snapshot()["research_tool_calls"] == 1


def test_graphrag_anchor_ids_resolve_and_sources_are_hydrated_on_loop(loop_services, session):
    session.ledger.add_rag(
        query="seed",
        chunk_id="chunk-1",
        title="Doc",
        locator="Doc#1",
        excerpt="seed",
        source_ids=["chunk-1"],
        score=0.8,
    )
    tool = GraphRagSearchTool(
        ".",
        services=loop_services,
        research_session_provider=lambda: session,
    )

    text = tool.execute(query="relation", anchor_ids=["R1"])

    call = loop_services.graph.calls[0]
    assert call["source_ids"] == ["chunk-1"]
    assert call["source_scores"] == {"chunk-1": 0.8}
    assert "[G1]" in text
    assert "Architecture/cache.md#12" in text
    assert session.ledger.get("G1").source_refs == ["Architecture/cache.md#12"]
    assert loop_services.knowledge.store.thread_ids == [loop_services.loop_thread_id]


def test_knowledge_tools_work_without_research_session(loop_services):
    rag = RagSearchTool(".", services=loop_services)
    graph = GraphRagSearchTool(".", services=loop_services)

    assert "[RAG1]" in rag.execute(query="cache")
    assert "[GRAPH1]" in graph.execute(query="relation")


def test_knowledge_tools_clamp_numeric_arguments(loop_services):
    RagSearchTool(".", services=loop_services).execute(query="q", top_k=999)
    GraphRagSearchTool(".", services=loop_services).execute(
        query="q",
        max_facts=999,
        retrieval_depth=99,
        expansion_candidate_limit=9999,
    )

    assert loop_services.knowledge.calls[-1]["top_k"] == 50
    assert loop_services.graph.calls[-1]["max_facts"] == 100
    assert loop_services.graph.calls[-1]["retrieval_depth"] == 7
    assert loop_services.graph.calls[-1]["expansion_candidate_limit"] == 1000


def test_missing_services_return_unavailable_instead_of_raising(tmp_path):
    assert "unavailable" in RagSearchTool(str(tmp_path)).execute(query="q").lower()
    assert "unavailable" in GraphRagSearchTool(str(tmp_path)).execute(query="q").lower()


def test_normal_mode_knowledge_tool_cancellation_uses_instance_scoped_owners():
    calls = []

    class _Services:
        knowledge_manager = object()

        def rag_search(self, **kwargs):
            calls.append(("execute", kwargs["owner_id"]))
            return {"results": []}

        def cancel_pending(self, *, owner_id=None):
            calls.append(("cancel", owner_id))

    services = _Services()
    first = RagSearchTool(".", services=services)
    second = RagSearchTool(".", services=services)

    first.execute(query="first")
    first.cancel()
    second.execute(query="second")
    second.cancel()

    first_execute, first_cancel, second_execute, second_cancel = calls
    assert first_execute[1] is not None
    assert first_execute[1] == first_cancel[1]
    assert second_execute[1] == second_cancel[1]
    assert first_execute[1] != second_execute[1]


def test_graphrag_timeout_is_reported_as_degraded_not_as_no_matching_facts():
    class _Services:
        graph_manager = object()

        def graph_search(self, **kwargs):
            return GraphSearchResult(
                query=kwargs["query"],
                facts=[],
                context_text="",
                diagnostics={"status": "timeout"},
            )

    result = GraphRagSearchTool(".", services=_Services()).execute(query="relation")

    assert "timed out" in result.lower()
    assert "no matching facts" not in result.lower()


def test_agent_reload_preserves_research_dependencies(tmp_path, loop_services, session):
    def provider():
        return session

    def branch_provider():
        return "main"

    agent = Agent(
        llm=SimpleNamespace(),
        workspace=str(tmp_path),
        research_services=loop_services,
        research_session_provider=provider,
        research_branch_provider=branch_provider,
    )

    agent.reload_tools()

    rag = next(tool for tool in agent.tools if tool.name == "rag_search")
    assert rag.services is loop_services
    assert rag.research_session_provider is provider
    assert rag.research_branch_provider is branch_provider


def test_research_service_cancellation_is_scoped_to_turn_owner():
    from core.research.services import ResearchServices

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    started = {"a": threading.Event(), "b": threading.Event()}

    async def make_gates():
        return {"a": loop.create_future(), "b": loop.create_future()}

    gates = asyncio.run_coroutine_threadsafe(make_gates(), loop).result(timeout=2)

    class _BlockingKnowledge:
        async def retrieve(self, **kwargs):
            query = kwargs["query"]
            started[query].set()
            return await gates[query]

    services = ResearchServices(loop=loop, knowledge_manager=_BlockingKnowledge())
    outcomes = {}

    def run(label):
        try:
            outcomes[label] = services.rag_search(
                query=label,
                kb_ids=["kb"],
                timeout=5,
                owner_id=f"turn-{label}",
            )
        except BaseException as exc:  # cancellation is the behavior under test
            outcomes[label] = exc

    workers = [threading.Thread(target=run, args=(label,)) for label in ("a", "b")]
    try:
        for worker in workers:
            worker.start()
        assert started["a"].wait(2)
        assert started["b"].wait(2)

        services.cancel_pending(owner_id="turn-a")
        loop.call_soon_threadsafe(
            gates["b"].set_result,
            {"query": "b", "results": [], "status": "ok"},
        )
        for worker in workers:
            worker.join(timeout=2)

        assert isinstance(outcomes["a"], concurrent.futures.CancelledError)
        assert outcomes["b"]["query"] == "b"
    finally:
        services.cancel_pending()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()
