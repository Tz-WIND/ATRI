"""Helpers for separating internal context from user-visible content."""

from __future__ import annotations

from typing import Any

from core.agent.context import content_to_text

INTERNAL_CONTEXT_HEADER = "[ATRI internal context]"
CURRENT_REQUEST_MARKER = "[Current request]\n"


def prepend_internal_context(context_text: str, content: str | list[dict]) -> str | list[dict]:
    context = context_text.strip()
    if not context:
        return content

    prefix = f"{INTERNAL_CONTEXT_HEADER}\n{context}\n\n{CURRENT_REQUEST_MARKER}"
    if isinstance(content, list):
        return [{"type": "text", "text": prefix}, *content]
    return prefix + content


def strip_internal_context_text(text: str) -> str:
    stripped = text.lstrip()
    marker_index = stripped.rfind(CURRENT_REQUEST_MARKER)
    if marker_index < 0:
        return text

    context_prefix = stripped[:marker_index]
    if stripped.startswith(INTERNAL_CONTEXT_HEADER) or _looks_like_legacy_context(context_prefix):
        return stripped[marker_index + len(CURRENT_REQUEST_MARKER) :]
    return text


def content_to_display_text(content: Any) -> str:
    return strip_internal_context_text(content_to_text(content))


def _looks_like_legacy_context(prefix: str) -> bool:
    first_line = prefix.lstrip().splitlines()[0].strip().lower() if prefix.strip() else ""
    return (
        first_line.startswith("[")
        and first_line.endswith("]")
        and ("context" in first_line or "before this request" in first_line)
    )
