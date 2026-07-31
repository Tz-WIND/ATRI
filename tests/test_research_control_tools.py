import json

import pytest

from core.research import ResearchPolicy, ResearchSession
from core.tools import create_tools
from core.tools.research import (
    ExportResearchReportTool,
    ResearchCheckpointTool,
    ResearchEvidenceTool,
)


@pytest.fixture
def session(tmp_path):
    current = ResearchSession(
        policy=ResearchPolicy.from_config({}, tmp_path),
        turn_id="turn-1",
        session_id="session-1",
        original_user_request="export the research report",
        report_export_allowed=True,
    )
    current.ledger.add_rag(
        query="alpha",
        chunk_id="chunk-1",
        title="Alpha document",
        locator="Docs/alpha.md#0",
        excerpt="alpha is supported",
        source_ids=["chunk-1"],
        source_refs=["Docs/alpha.md#0"],
        score=0.9,
    )
    return current


def test_factory_registers_research_control_tools_with_session_provider(tmp_path, session):
    provider = lambda: session  # noqa: E731
    tools = {
        tool.name: tool for tool in create_tools(str(tmp_path), research_session_provider=provider)
    }

    assert {"research_checkpoint", "research_evidence", "export_research_report"} <= set(tools)
    assert tools["research_checkpoint"].research_session_provider is provider
    assert tools["research_evidence"].capabilities.read_only is True
    assert tools["export_research_report"].capabilities.writes_files is True


def test_research_evidence_supports_all_operations(session):
    tool = ResearchEvidenceTool(".", research_session_provider=lambda: session)

    assert "R1" in tool.execute(operation="summary")
    assert "R1" in tool.execute(operation="search", query="alpha")
    assert "R1" in tool.execute(operation="get", citation_ids=["R1"])
    assert "Sources" in tool.execute(operation="sources")
    assert session.budget.snapshot()["research_tool_calls"] == 0


def test_checkpoint_updates_phase_without_consuming_research_budget(session):
    tool = ResearchCheckpointTool(".", research_session_provider=lambda: session)

    result = tool.execute(
        phase="planning",
        completed_questions=["scope"],
        open_questions=["freshness"],
        conflicts=[],
        note="plan ready",
    )

    assert '"ok": true' in result
    assert session.current_phase == "planning"
    assert session.completed_questions == ["scope"]
    assert session.budget.snapshot()["research_tool_calls"] == 0


def test_control_tools_require_active_session(tmp_path):
    assert "active Deep Research session" in ResearchEvidenceTool(str(tmp_path)).execute(
        operation="summary"
    )
    assert "active Deep Research session" in ResearchCheckpointTool(str(tmp_path)).execute(
        phase="planning"
    )


def test_export_requires_intent_synthesizing_and_scoped_path(tmp_path, session):
    session.report_export_allowed = False
    tool = ExportResearchReportTool(str(tmp_path), research_session_provider=lambda: session)

    assert "explicit export authorization" in tool.execute(
        path="research/x.md", content="Finding [R1]"
    )
    session.report_export_allowed = True
    assert "synthesizing" in tool.execute(path="x.md", content="Finding [R1]")
    session.current_phase = "synthesizing"
    assert "outside" in tool.execute(path="../x.md", content="Finding [R1]")

    result = tool.execute(path="x.md", content="Finding [R1]")

    assert "Exported" in result
    exported = (tmp_path / "research" / "x.md").read_text(encoding="utf-8")
    assert "Finding [R1]" in exported
    assert exported.count("## Sources") == 1
    assert "Docs/alpha.md#0" in exported


def test_export_rejects_unknown_citations_extensions_and_overwrite(tmp_path, session):
    session.current_phase = "synthesizing"
    tool = ExportResearchReportTool(str(tmp_path), research_session_provider=lambda: session)

    assert "unknown citations" in tool.execute(path="bad.md", content="Finding [R99]")
    assert "extension" in tool.execute(path="bad.html", content="Finding [R1]")
    assert "Exported" in tool.execute(path="report.txt", content="Finding [R1]")
    assert "already exists" in tool.execute(path="report.txt", content="Finding [R1]")
    assert "Exported" in tool.execute(path="report.txt", content="Updated [R1]", overwrite=True)


def test_json_export_remains_valid_json_and_has_canonical_sources(tmp_path, session):
    session.current_phase = "synthesizing"
    tool = ExportResearchReportTool(str(tmp_path), research_session_provider=lambda: session)

    result = tool.execute(
        path="report.json",
        content=json.dumps({"finding": "Alpha [R1]", "sources": ["invented"]}),
    )

    assert "Exported" in result
    payload = json.loads((tmp_path / "research" / "report.json").read_text("utf-8"))
    assert payload["finding"] == "Alpha [R1]"
    assert payload["sources"] == [
        {
            "citation_id": "R1",
            "kind": "rag",
            "title": "Alpha document",
            "locator": "Docs/alpha.md#0",
            "source_refs": ["Docs/alpha.md#0"],
            "url": "",
        }
    ]
