"""Turn-scoped Deep Research control, evidence, and report export tools."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.utils import atomic_write_text

from .base import Tool, ToolCapabilities


class _SessionTool(Tool):
    def __init__(
        self,
        workspace: str = ".",
        *,
        research_session_provider: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(workspace)
        self.research_session_provider = research_session_provider

    def _session(self):
        if self.research_session_provider is None:
            return None
        return self.research_session_provider()


class ResearchCheckpointTool(_SessionTool):
    name = "research_checkpoint"
    description = (
        "Update the current Deep Research phase, coverage, conflicts, and open questions. "
        "This is a zero-budget control operation."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "phase": {
                "type": "string",
                "enum": ["planning", "gathering", "verifying", "synthesizing"],
                "description": "Next research phase.",
            },
            "completed_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Research questions already answered.",
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Material gaps still open.",
            },
            "conflicts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Conflicting evidence or interpretations.",
            },
            "note": {"type": "string", "description": "Short checkpoint note."},
        },
        "required": ["phase"],
    }
    capabilities = ToolCapabilities(
        capability="research.control",
        read_only=True,
        supports_parallel=False,
    )

    def execute(
        self,
        phase: str,
        completed_questions: list[str] | None = None,
        open_questions: list[str] | None = None,
        conflicts: list[str] | None = None,
        note: str = "",
    ) -> str:
        session = self._session()
        if session is None:
            return "Error: research_checkpoint requires an active Deep Research session."
        result = session.checkpoint(
            phase=phase,
            completed_questions=completed_questions,
            open_questions=open_questions,
            conflicts=conflicts,
            note=note,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)


class ResearchEvidenceTool(_SessionTool):
    name = "research_evidence"
    description = (
        "Read the current Deep Research evidence ledger by summary, keyword search, citation "
        "IDs, or canonical source catalog. This does not consume research-call budget."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["summary", "search", "get", "sources"],
                "description": "Ledger read operation.",
            },
            "query": {"type": "string", "description": "Keyword for search."},
            "citation_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Citation IDs for get or sources.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum returned characters (100-12000).",
                "default": 12000,
            },
        },
        "required": ["operation"],
    }
    capabilities = ToolCapabilities(
        capability="research.evidence.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(
        self,
        operation: str,
        query: str = "",
        citation_ids: list[str] | None = None,
        max_chars: int = 12_000,
    ) -> str:
        session = self._session()
        if session is None:
            return "Error: research_evidence requires an active Deep Research session."
        try:
            limit = max(100, min(12_000, int(max_chars)))
        except (TypeError, ValueError):
            limit = 12_000
        try:
            return session.ledger.compact(
                operation,
                query=str(query or ""),
                citation_ids=[str(value) for value in citation_ids or []],
                max_chars=limit,
            )
        except ValueError as exc:
            return f"Error: {exc}"


class ExportResearchReportTool(_SessionTool):
    name = "export_research_report"
    description = (
        "Export a citation-validated Deep Research report after synthesis. Requires explicit "
        "authorization in the current user request and writes only to the configured report "
        "directory."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Report path within the configured report directory.",
            },
            "content": {"type": "string", "description": "Complete report content."},
            "overwrite": {
                "type": "boolean",
                "description": "Replace an existing report. Defaults to false.",
                "default": False,
            },
        },
        "required": ["path", "content"],
    }
    capabilities = ToolCapabilities(
        capability="research.report.export",
        writes_files=True,
        requires_approval=True,
    )

    def execute(self, path: str, content: str, overwrite: bool = False) -> str:
        session = self._session()
        if session is None:
            return "Error: export requires an active Deep Research session."
        if not session.report_export_allowed:
            return "Error: explicit export authorization is required for this turn."
        if session.current_phase != "synthesizing":
            return "Error: report export is only allowed while synthesizing."
        try:
            target = self._target_path(session, path)
        except (PermissionError, ValueError) as exc:
            return f"Error: {exc}"
        if target.suffix.lower() not in {".md", ".txt", ".json"}:
            return "Error: report extension must be .md, .txt, or .json."
        if target.exists() and not overwrite:
            return f"Error: report already exists: {target.name}"
        try:
            validation = session.report_validator.validate_strict(
                content,
                require_source_section=False,
            )
            finalized = self._finalized_content(
                session,
                content,
                target.suffix.lower(),
                validation.known_citations,
            )
            if target.suffix.lower() != ".json":
                session.report_validator.validate_strict(finalized)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return f"Error: report validation failed: {exc}"

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_text(target, finalized, prefix=".research_report_")
        except OSError as exc:
            return f"Error: report export failed: {exc}"
        return f"Exported research report: {target}"

    @staticmethod
    def _target_path(session, path: str) -> Path:
        root = Path(session.policy.report_directory).resolve()
        raw_text = str(path or "").strip()
        if not raw_text:
            raise ValueError("report path is required")
        raw = Path(raw_text).expanduser()
        if not raw.is_absolute() and raw.parts and raw.parts[0].lower() == root.name.lower():
            raw = Path(*raw.parts[1:])
        target = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
        try:
            is_inside = os.path.commonpath([str(root), str(target)]) == str(root)
        except ValueError:
            is_inside = False
        if not is_inside or target == root:
            raise PermissionError("report path resolves outside the configured report directory")
        return target

    @staticmethod
    def _finalized_content(session, content: str, suffix: str, citation_ids: list[str]) -> str:
        if suffix != ".json":
            return session.report_validator.finalize_chat_report(content).content
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("JSON report content must be an object")
        payload["sources"] = [
            {
                "citation_id": item.citation_id,
                "kind": item.kind,
                "title": item.title,
                "locator": item.locator,
                "source_refs": item.source_refs,
                "url": item.url,
            }
            for item in session.ledger.cited_items(citation_ids)
        ]
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
