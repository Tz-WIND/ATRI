"""Deterministic citation validation and canonical source-list generation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .evidence import EvidenceLedger

_CITATION_RE = re.compile(r"\[([RGW]\d+)\]", re.I)
_SOURCES_HEADING_RE = re.compile(r"(?im)^##\s+(?:sources|来源|参考来源)\s*$")
_KIND_PREFIX = {"rag": "R", "graph": "G", "web": "W"}


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.upper()
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


@dataclass(frozen=True)
class ReportValidation:
    valid: bool
    citations: list[str] = field(default_factory=list)
    known_citations: list[str] = field(default_factory=list)
    unknown_citations: list[str] = field(default_factory=list)
    type_mismatches: list[str] = field(default_factory=list)
    discovery_citations: list[str] = field(default_factory=list)
    discovery_only: bool = False
    missing_source_section: bool = False


@dataclass(frozen=True)
class FinalizedReport:
    content: str
    validation: ReportValidation


class ResearchReportValidator:
    def __init__(self, ledger: EvidenceLedger):
        self.ledger = ledger

    def validate(self, content: str) -> ReportValidation:
        text = str(content or "")
        citations = _ordered_unique(_CITATION_RE.findall(text))
        known: list[str] = []
        unknown: list[str] = []
        mismatches: list[str] = []
        known_items = []
        discovery_citations: list[str] = []
        for citation_id in citations:
            item = self.ledger.get(citation_id)
            if item is None:
                unknown.append(citation_id)
                continue
            known.append(citation_id)
            known_items.append(item)
            if _KIND_PREFIX[item.kind] != citation_id[0]:
                mismatches.append(citation_id)
            if item.kind == "web" and item.strength == "discovery":
                discovery_citations.append(citation_id)
        discovery_only = bool(known_items) and all(
            item.kind == "web" and item.strength == "discovery" for item in known_items
        )
        missing_sources = _SOURCES_HEADING_RE.search(text) is None
        return ReportValidation(
            valid=bool(known)
            and not unknown
            and not mismatches
            and not discovery_citations
            and not missing_sources,
            citations=citations,
            known_citations=known,
            unknown_citations=unknown,
            type_mismatches=mismatches,
            discovery_citations=discovery_citations,
            discovery_only=discovery_only,
            missing_source_section=missing_sources,
        )

    def validate_strict(
        self,
        content: str,
        *,
        require_source_section: bool = True,
    ) -> ReportValidation:
        validation = self.validate(content)
        errors = []
        if not validation.known_citations:
            errors.append("report requires at least one citable source")
        if validation.unknown_citations:
            errors.append(f"unknown citations: {', '.join(validation.unknown_citations)}")
        if validation.type_mismatches:
            errors.append(f"citation type mismatch: {', '.join(validation.type_mismatches)}")
        if validation.discovery_citations:
            errors.append(
                "discovery-only web evidence cannot support the report: "
                + ", ".join(validation.discovery_citations)
            )
        if require_source_section and validation.missing_source_section:
            errors.append("report requires a canonical Sources section")
        if errors:
            raise ValueError("; ".join(errors))
        return validation

    def finalize_chat_report(self, content: str) -> FinalizedReport:
        text = str(content or "").strip()
        initial = self.validate(text)
        notes: list[str] = []
        invalid = set(
            initial.unknown_citations + initial.type_mismatches + initial.discovery_citations
        )
        if invalid:
            text = _CITATION_RE.sub(
                lambda match: "" if match.group(1).upper() in invalid else match.group(0),
                text,
            )
            text = re.sub(r"[ \t]{2,}", " ", text)
        if initial.unknown_citations or initial.type_mismatches:
            notes.append(
                "citation validation removed unknown references; no replacement was invented"
            )
        if initial.discovery_citations:
            notes.append("available Web evidence is discovery-only and does not verify key claims")

        body = self._without_source_catalog(text).rstrip()
        if notes:
            body += "\n\n## Conflicts, unknowns, and limitations\n"
            body += "\n".join(f"- {note}." for note in notes)

        cleaned = self.validate(body)
        sources = self.ledger.source_catalog(cleaned.known_citations)
        finalized_content = f"{body}\n\n{sources}".strip()
        final_validation = self.validate(finalized_content)
        return FinalizedReport(finalized_content, final_validation)

    @staticmethod
    def _without_source_catalog(content: str) -> str:
        match = _SOURCES_HEADING_RE.search(content)
        return content[: match.start()] if match else content
