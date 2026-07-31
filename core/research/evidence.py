"""Thread-safe evidence ledger with stable turn-local citations."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

EvidenceKind = Literal["rag", "graph", "web"]
EvidenceStrength = Literal["discovery", "derived", "full"]

_PREFIX_BY_KIND: dict[EvidenceKind, str] = {"rag": "R", "graph": "G", "web": "W"}
_STRENGTH_ORDER = {"discovery": 0, "derived": 1, "full": 2}


def _unique(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def canonicalize_url(url: str) -> str:
    """Normalize a URL for Web evidence de-duplication."""

    parsed = urlsplit(str(url or "").strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if not scheme or not host:
        raise ValueError("URL must include a scheme and host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL has an invalid port") from exc
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


@dataclass
class EvidenceItem:
    citation_id: str
    kind: EvidenceKind
    queries: list[str]
    title: str
    locator: str
    excerpt: str
    source_ids: list[str]
    source_refs: list[str]
    url: str
    score: float | None
    confidence: float | None
    strength: EvidenceStrength
    retrieved_at: str
    tool_call_id: str
    branch_id: str
    fingerprint: str
    fact_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def query(self) -> str:
        return self.queries[-1] if self.queries else ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["query"] = self.query
        return result


class EvidenceLedger:
    """Own every citable source independently of compressed chat messages."""

    def __init__(
        self,
        *,
        on_change: Callable[[EvidenceItem, bool], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._items_by_id: dict[str, EvidenceItem] = {}
        self._citation_by_fingerprint: dict[str, str] = {}
        self._counters = {"R": 0, "G": 0, "W": 0}
        self._on_change = on_change

    def __len__(self) -> int:
        with self._lock:
            return len(self._items_by_id)

    def set_on_change(self, callback: Callable[[EvidenceItem, bool], None] | None) -> None:
        with self._lock:
            self._on_change = callback

    def add_rag(
        self,
        *,
        query: str,
        chunk_id: str,
        title: str,
        locator: str,
        excerpt: str,
        source_ids: list[str] | None = None,
        source_refs: list[str] | None = None,
        score: float | None = None,
        confidence: float | None = None,
        tool_call_id: str = "",
        branch_id: str = "main",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        clean_chunk_id = str(chunk_id or "").strip()
        if not clean_chunk_id:
            raise ValueError("chunk_id is required")
        return self._add(
            kind="rag",
            fingerprint=f"rag:{clean_chunk_id}",
            query=query,
            title=title,
            locator=locator,
            excerpt=excerpt,
            source_ids=source_ids or [clean_chunk_id],
            source_refs=source_refs or [],
            score=score,
            confidence=confidence,
            strength="full",
            tool_call_id=tool_call_id,
            branch_id=branch_id,
            metadata={"chunk_id": clean_chunk_id, **dict(metadata or {})},
        )

    def add_graph(
        self,
        *,
        query: str,
        fact_key: str,
        subject: str,
        predicate: str,
        object_value: str,
        title: str,
        locator: str,
        excerpt: str,
        source_ids: list[str] | None = None,
        source_refs: list[str] | None = None,
        score: float | None = None,
        confidence: float | None = None,
        strength: EvidenceStrength = "full",
        tool_call_id: str = "",
        branch_id: str = "main",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        clean_fact_key = str(fact_key or "").strip()
        triple = "|".join(_normalized_text(value) for value in (subject, predicate, object_value))
        sources = _unique(source_ids or [])
        identity = clean_fact_key or f"{triple}|{'|'.join(sorted(sources))}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self._add(
            kind="graph",
            fingerprint=f"graph:{digest}",
            query=query,
            title=title,
            locator=locator or (f"graph:{clean_fact_key}" if clean_fact_key else "graph:fact"),
            excerpt=excerpt,
            source_ids=sources,
            source_refs=source_refs or [],
            score=score,
            confidence=confidence,
            strength=strength,
            tool_call_id=tool_call_id,
            branch_id=branch_id,
            fact_key=clean_fact_key,
            metadata={
                "subject": subject,
                "predicate": predicate,
                "object": object_value,
                **dict(metadata or {}),
            },
        )

    def add_web(
        self,
        *,
        query: str,
        url: str,
        title: str,
        excerpt: str,
        strength: EvidenceStrength,
        score: float | None = None,
        confidence: float | None = None,
        source_refs: list[str] | None = None,
        tool_call_id: str = "",
        branch_id: str = "main",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        canonical_url = canonicalize_url(url)
        return self._add(
            kind="web",
            fingerprint=f"web:{canonical_url}",
            query=query,
            title=title,
            locator=canonical_url,
            excerpt=excerpt,
            source_ids=[],
            source_refs=source_refs or [],
            url=canonical_url,
            score=score,
            confidence=confidence,
            strength=strength,
            tool_call_id=tool_call_id,
            branch_id=branch_id,
            metadata=metadata,
        )

    def _add(
        self,
        *,
        kind: EvidenceKind,
        fingerprint: str,
        query: str,
        title: str,
        locator: str,
        excerpt: str,
        source_ids: list[str],
        source_refs: list[str],
        score: float | None,
        confidence: float | None,
        strength: EvidenceStrength,
        tool_call_id: str,
        branch_id: str,
        url: str = "",
        fact_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        clean_query = str(query or "").strip()
        created = False
        with self._lock:
            citation_id = self._citation_by_fingerprint.get(fingerprint)
            if citation_id is None:
                prefix = _PREFIX_BY_KIND[kind]
                self._counters[prefix] += 1
                citation_id = f"{prefix}{self._counters[prefix]}"
                item = EvidenceItem(
                    citation_id=citation_id,
                    kind=kind,
                    queries=[clean_query] if clean_query else [],
                    title=str(title or "").strip() or "Untitled source",
                    locator=str(locator or "").strip(),
                    excerpt=str(excerpt or "").strip(),
                    source_ids=_unique(source_ids),
                    source_refs=_unique(source_refs),
                    url=str(url or "").strip(),
                    score=float(score) if score is not None else None,
                    confidence=float(confidence) if confidence is not None else None,
                    strength=strength,
                    retrieved_at=datetime.now(UTC).isoformat(),
                    tool_call_id=str(tool_call_id or "").strip(),
                    branch_id=str(branch_id or "main").strip() or "main",
                    fingerprint=fingerprint,
                    fact_key=str(fact_key or "").strip(),
                    metadata=dict(metadata or {}),
                )
                self._items_by_id[citation_id] = item
                self._citation_by_fingerprint[fingerprint] = citation_id
                created = True
            else:
                item = self._items_by_id[citation_id]
                if clean_query and clean_query not in item.queries:
                    item.queries.append(clean_query)
                item.source_ids = _unique([*item.source_ids, *source_ids])
                item.source_refs = _unique([*item.source_refs, *source_refs])
                if score is not None and (item.score is None or float(score) > item.score):
                    item.score = float(score)
                if confidence is not None and (
                    item.confidence is None or float(confidence) > item.confidence
                ):
                    item.confidence = float(confidence)
                if _STRENGTH_ORDER[strength] >= _STRENGTH_ORDER[item.strength]:
                    item.strength = strength
                    if str(excerpt or "").strip():
                        item.excerpt = str(excerpt).strip()
                    if str(title or "").strip():
                        item.title = str(title).strip()
                elif len(str(excerpt or "")) > len(item.excerpt):
                    item.excerpt = str(excerpt).strip()
                item.metadata.update(dict(metadata or {}))
            snapshot = copy.deepcopy(item)

        if self._on_change:
            self._on_change(snapshot, created)
        return snapshot

    def get(self, citation_id: str) -> EvidenceItem | None:
        with self._lock:
            item = self._items_by_id.get(str(citation_id or "").strip().upper())
            return copy.deepcopy(item) if item else None

    def items(self) -> list[EvidenceItem]:
        with self._lock:
            return [copy.deepcopy(item) for item in self._items_by_id.values()]

    def resolve_anchor_ids(self, citation_ids: list[str]) -> tuple[list[str], dict[str, float]]:
        source_ids: list[str] = []
        source_scores: dict[str, float] = {}
        for citation_id in citation_ids:
            item = self.get(citation_id)
            if item is None:
                raise ValueError(f"unknown evidence citation: {citation_id}")
            if item.kind != "rag":
                raise ValueError(f"anchor citation must refer to RAG evidence: {citation_id}")
            for source_id in item.source_ids:
                if source_id not in source_ids:
                    source_ids.append(source_id)
                if item.score is not None:
                    source_scores[source_id] = max(source_scores.get(source_id, 0.0), item.score)
        return source_ids, source_scores

    def cited_items(self, citation_ids: Iterable[str]) -> list[EvidenceItem]:
        result: list[EvidenceItem] = []
        seen: set[str] = set()
        for citation_id in citation_ids:
            normalized = str(citation_id or "").strip().upper()
            if normalized in seen:
                continue
            item = self.get(normalized)
            if item:
                seen.add(normalized)
                result.append(item)
        return result

    def source_catalog(self, citation_ids: Iterable[str] | None = None) -> str:
        items = self.items() if citation_ids is None else self.cited_items(citation_ids)
        lines = ["## Sources"]
        for item in items:
            if item.kind == "web" and item.url:
                lines.append(f"- [{item.citation_id}] [{item.title}]({item.url})")
                continue
            references = item.source_refs or item.source_ids
            provenance = ", ".join(references)
            suffix = f" — {provenance}" if provenance else ""
            lines.append(f"- [{item.citation_id}] {item.title} — {item.locator}{suffix}")
        if len(lines) == 1:
            lines.append("- No citable evidence was collected.")
        return "\n".join(lines)

    def compact(
        self,
        operation: str,
        *,
        query: str = "",
        citation_ids: list[str] | None = None,
        max_chars: int = 12_000,
    ) -> str:
        op = str(operation or "summary").strip().lower()
        items = self.items()
        if op == "summary":
            payload: Any = {
                "total": len(items),
                "by_kind": {
                    kind: sum(item.kind == kind for item in items)
                    for kind in ("rag", "graph", "web")
                },
                "citations": [item.citation_id for item in items],
                "by_branch": {
                    branch: [item.citation_id for item in items if item.branch_id == branch]
                    for branch in sorted({item.branch_id for item in items})
                },
            }
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        elif op == "search":
            needle = _normalized_text(query)
            matched = [
                item
                for item in items
                if needle
                and needle
                in _normalized_text(
                    f"{item.title} {item.locator} {item.excerpt} {' '.join(item.source_refs)}"
                )
            ]
            text = json.dumps(
                [self._compact_item(item) for item in matched],
                ensure_ascii=False,
                indent=2,
            )
        elif op == "get":
            selected = self.cited_items(citation_ids or [])
            text = json.dumps(
                [self._compact_item(item, include_excerpt=True) for item in selected],
                ensure_ascii=False,
                indent=2,
            )
        elif op == "sources":
            text = self.source_catalog(citation_ids)
        else:
            raise ValueError("operation must be one of: summary, search, get, sources")
        return self._truncate(text, max_chars)

    @staticmethod
    def _compact_item(item: EvidenceItem, *, include_excerpt: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "citation_id": item.citation_id,
            "kind": item.kind,
            "title": item.title,
            "locator": item.locator,
            "source_refs": item.source_refs,
            "strength": item.strength,
            "branch_id": item.branch_id,
        }
        if include_excerpt:
            result["excerpt"] = item.excerpt
        return result

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        limit = max(0, int(max_chars))
        if len(text) <= limit:
            return text
        if limit == 0:
            return ""
        if limit == 1:
            return "…"
        return text[: limit - 1] + "…"
