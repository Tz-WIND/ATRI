from concurrent.futures import ThreadPoolExecutor

import pytest

from core.research.budget import ResearchBudget
from core.research.evidence import EvidenceLedger, canonicalize_url
from core.research.policy import ResearchPolicy
from core.research.report import ResearchReportValidator
from core.research.session import ResearchSession


class FakeClock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def make_policy(tmp_path, **changes):
    config = {
        "max_gap_rounds": 2,
        "max_research_tool_calls": 3,
        "max_web_fetches": 2,
        "max_parallel_subagents": 2,
        "timeout_seconds": 120,
        "synthesis_reserve_seconds": 30,
        **changes,
    }
    return ResearchPolicy.from_config(config, tmp_path)


def test_budget_tool_reservations_are_atomic(tmp_path):
    budget = ResearchBudget(make_policy(tmp_path))

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda _: budget.reserve_tool_call("rag_search"), range(8)))

    assert sum(decision.allowed for decision in decisions) == 3
    assert budget.snapshot()["research_tool_calls"] == 3


def test_budget_reserves_synthesis_time(tmp_path):
    clock = FakeClock()
    budget = ResearchBudget(make_policy(tmp_path), clock=clock)

    clock.advance(91)
    decision = budget.reserve_tool_call("rag_search")

    assert decision.allowed is False
    assert decision.reason == "synthesis_reserved"
    assert budget.snapshot()["research_tool_calls"] == 0


def test_budget_web_urls_only_consume_one_page_slot(tmp_path):
    budget = ResearchBudget(make_policy(tmp_path))

    first = budget.reserve_web_fetch("https://example.com/a")
    repeated = budget.reserve_web_fetch("https://example.com/a")

    assert first.allowed and repeated.allowed
    assert repeated.reason == "repeat_url"
    assert budget.snapshot()["web_fetches"] == 1
    assert budget.snapshot()["research_tool_calls"] == 2


def test_budget_subagent_slots_are_shared_and_released(tmp_path):
    budget = ResearchBudget(make_policy(tmp_path))

    assert budget.reserve_subagents(2).allowed
    assert not budget.reserve_subagents(1).allowed
    budget.release_subagents(1)
    assert budget.reserve_subagents(1).allowed
    snapshot = budget.snapshot()
    assert snapshot["active_subagents"] == 2
    assert snapshot["total_subagents"] == 3


def test_budget_cancellation_blocks_new_work(tmp_path):
    budget = ResearchBudget(make_policy(tmp_path))
    budget.cancel()

    assert budget.reserve_tool_call("rag_search").reason == "cancelled"
    assert budget.reserve_subagents(1).reason == "cancelled"


def test_ledger_deduplicates_rag_chunks_with_stable_citations():
    ledger = EvidenceLedger()

    first = ledger.add_rag(
        query="first query",
        chunk_id="chunk-1",
        title="Architecture",
        locator="cache.md#12",
        excerpt="first excerpt",
        source_ids=["chunk-1"],
        score=0.8,
    )
    second = ledger.add_rag(
        query="second query",
        chunk_id="chunk-1",
        title="Architecture",
        locator="cache.md#12",
        excerpt="a longer second excerpt",
        source_ids=["chunk-1"],
        score=0.9,
    )

    assert first.citation_id == second.citation_id == "R1"
    assert second.queries == ["first query", "second query"]
    assert second.score == pytest.approx(0.9)
    assert len(ledger) == 1


def test_ledger_deduplicates_graph_fact_key_and_merges_sources():
    ledger = EvidenceLedger()
    first = ledger.add_graph(
        query="q1",
        fact_key="fact-1",
        subject="A",
        predicate="depends_on",
        object_value="B",
        title="A depends_on B",
        locator="graph:fact-1",
        excerpt="source one",
        source_ids=["chunk-1"],
        source_refs=["kb/doc#1"],
        score=0.7,
        confidence=0.8,
    )
    second = ledger.add_graph(
        query="q2",
        fact_key="fact-1",
        subject="A",
        predicate="depends_on",
        object_value="B",
        title="A depends_on B",
        locator="graph:fact-1",
        excerpt="source two",
        source_ids=["chunk-2"],
        source_refs=["kb/doc#2"],
        score=0.9,
        confidence=0.85,
    )

    assert first.citation_id == second.citation_id == "G1"
    assert second.source_ids == ["chunk-1", "chunk-2"]
    assert second.source_refs == ["kb/doc#1", "kb/doc#2"]


def test_web_discovery_is_upgraded_to_full_without_changing_citation():
    ledger = EvidenceLedger()
    discovery = ledger.add_web(
        query="q",
        url="HTTPS://Example.com:443/a#fragment",
        title="Example",
        excerpt="search snippet",
        strength="discovery",
    )
    full = ledger.add_web(
        query="q",
        url="https://example.com/a",
        title="Example page",
        excerpt="full page body",
        strength="full",
    )

    assert canonicalize_url("HTTPS://Example.com:443/a#fragment") == "https://example.com/a"
    assert discovery.citation_id == full.citation_id == "W1"
    assert full.strength == "full"
    assert full.excerpt == "full page body"


def test_ledger_resolves_rag_anchor_ids():
    ledger = EvidenceLedger()
    ledger.add_rag(
        query="q",
        chunk_id="chunk-1",
        title="Doc",
        locator="doc#1",
        excerpt="text",
        source_ids=["chunk-1"],
        score=0.81,
    )

    source_ids, source_scores = ledger.resolve_anchor_ids(["R1"])

    assert source_ids == ["chunk-1"]
    assert source_scores == {"chunk-1": pytest.approx(0.81)}
    with pytest.raises(ValueError, match="unknown evidence citation"):
        ledger.resolve_anchor_ids(["R99"])


def test_ledger_compact_operations_and_character_limit():
    ledger = EvidenceLedger()
    ledger.add_rag(
        query="alpha",
        chunk_id="chunk-1",
        title="Alpha document",
        locator="alpha.md#1",
        excerpt="alpha " * 100,
        source_ids=["chunk-1"],
        score=0.7,
    )

    assert "R1" in ledger.compact("summary", max_chars=500)
    assert "R1" in ledger.compact("search", query="Alpha", max_chars=500)
    assert "R1" in ledger.compact("get", citation_ids=["R1"], max_chars=500)
    assert "## Sources" in ledger.compact("sources", max_chars=500)
    assert len(ledger.compact("get", citation_ids=["R1"], max_chars=80)) <= 80


def test_report_validator_rebuilds_sources_and_removes_unknown_citations():
    ledger = EvidenceLedger()
    ledger.add_rag(
        query="q",
        chunk_id="chunk-1",
        title="Architecture",
        locator="cache.md#12",
        excerpt="supported",
        source_ids=["chunk-1"],
        score=0.9,
    )
    validator = ResearchReportValidator(ledger)

    validation = validator.validate("Finding [R1] and invented [W99].")
    finalized = validator.finalize_chat_report(
        "Finding [R1] and invented [W99].\n\n## Sources\n- fake"
    )

    assert validation.unknown_citations == ["W99"]
    assert "[W99]" not in finalized.content
    assert "citation validation removed unknown references" in finalized.content
    assert "## Sources" in finalized.content
    assert "cache.md#12" in finalized.content
    assert finalized.validation.valid is True


def test_report_validator_marks_discovery_only_reports():
    ledger = EvidenceLedger()
    ledger.add_web(
        query="q",
        url="https://example.com",
        title="Search hit",
        excerpt="snippet",
        strength="discovery",
    )

    validation = ResearchReportValidator(ledger).validate("Claim [W1]")

    assert validation.discovery_only is True
    assert validation.valid is False


def test_report_validator_removes_discovery_web_citations_from_mixed_report():
    ledger = EvidenceLedger()
    ledger.add_rag(
        query="q",
        chunk_id="chunk-1",
        title="Primary document",
        locator="primary.md#1",
        excerpt="verified",
        source_ids=["chunk-1"],
    )
    ledger.add_web(
        query="q",
        url="https://example.com/discovery",
        title="Search hit",
        excerpt="unfetched snippet",
        strength="discovery",
    )
    validator = ResearchReportValidator(ledger)

    validation = validator.validate("Verified [R1], snippet claim [W1].\n\n## Sources")
    finalized = validator.finalize_chat_report("Verified [R1], snippet claim [W1].")

    assert validation.valid is False
    assert validation.discovery_citations == ["W1"]
    assert "[W1]" not in finalized.content
    assert "discovery-only" in finalized.content
    assert finalized.validation.valid is True


def test_report_validator_requires_citation_and_canonical_sources_section():
    ledger = EvidenceLedger()
    ledger.add_rag(
        query="q",
        chunk_id="chunk-1",
        title="Primary document",
        locator="primary.md#1",
        excerpt="verified",
        source_ids=["chunk-1"],
    )
    validator = ResearchReportValidator(ledger)

    assert validator.validate("Uncited assertion.\n\n## Sources").valid is False
    assert validator.validate("Verified [R1]").valid is False
    with pytest.raises(ValueError, match="at least one citable source"):
        validator.validate_strict("Uncited assertion.\n\n## Sources")
    with pytest.raises(ValueError, match="canonical Sources section"):
        validator.validate_strict("Verified [R1]")

    finalized = validator.finalize_chat_report("Verified [R1]")
    assert finalized.validation.valid is True


def test_session_checkpoint_state_machine_counts_gap_rounds(tmp_path):
    events = []
    session = ResearchSession(
        policy=make_policy(tmp_path),
        turn_id="turn-1",
        session_id="session-1",
        original_user_request="research",
        event_callback=lambda event_type, payload: events.append((event_type, payload)),
    )

    session.start()
    for phase in ("planning", "gathering", "verifying", "gathering"):
        result = session.checkpoint(phase=phase)
        assert result["ok"] is True

    assert session.budget.snapshot()["gap_rounds"] == 1
    assert {event_type for event_type, _ in events} >= {
        "research_started",
        "research_phase",
        "research_budget",
    }


def test_session_rejects_illegal_phase_transition(tmp_path):
    session = ResearchSession(
        policy=make_policy(tmp_path),
        turn_id="turn-1",
        session_id="session-1",
        original_user_request="research",
    )

    result = session.checkpoint(phase="verifying")

    assert result["ok"] is False
    assert result["error"] == "invalid_phase_transition"


def test_session_cancelled_branch_cannot_be_overwritten_by_late_completion(tmp_path):
    session = ResearchSession(
        policy=make_policy(tmp_path),
        turn_id="turn-1",
        session_id="session-1",
        original_user_request="research",
    )
    session.register_branch("branch-1", "slow branch")

    session.finish_branch("branch-1", status="cancelled", error="synthesis deadline")
    session.finish_branch("branch-1", status="completed")

    assert session.branches["branch-1"] == {
        "task": "slow branch",
        "status": "cancelled",
        "error": "synthesis deadline",
    }


def test_session_emits_sanitized_evidence_preview(tmp_path):
    events = []
    session = ResearchSession(
        policy=make_policy(tmp_path),
        turn_id="turn-1",
        session_id="session-1",
        original_user_request="research",
        event_callback=lambda event_type, payload: events.append((event_type, payload)),
    )
    long_secret = "x" * 1000

    session.ledger.add_rag(
        query="q",
        chunk_id="chunk-1",
        title="Doc",
        locator="doc#1",
        excerpt=long_secret,
        source_ids=["chunk-1"],
        score=0.9,
    )

    _, payload = next(item for item in events if item[0] == "research_evidence")
    assert payload["citation_id"] == "R1"
    assert len(payload["preview"]) <= 240
    assert "excerpt" not in payload
