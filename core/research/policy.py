"""Immutable DeepResearch policy and per-turn export authorization helpers."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

DEFAULT_DEEP_RESEARCH_CONFIG: dict[str, Any] = {
    "max_gap_rounds": 8,
    "max_research_tool_calls": 100,
    "max_web_fetches": 40,
    "max_parallel_subagents": 3,
    "timeout_seconds": 900.0,
    "synthesis_reserve_seconds": 60.0,
    "allow_report_export": True,
    "report_directory": "research",
}

_EXPORT_PERSIST_ACTION_RE = re.compile(
    r"(?:\u4fdd\u5b58|\u53e6\u5b58|\u5bfc\u51fa|\u5199\u5165|\u843d\u76d8|"
    r"\b(?:save|export|download)\b)",
    re.I,
)
_EXPORT_WRITE_ACTION_RE = re.compile(r"(?:\u521b\u5efa|\b(?:write|create)\b)", re.I)
_EXPORT_FILE_DESTINATION_RE = re.compile(
    r"(?:\b(?:file|path|directory|folder|disk)\b|"
    r"\.(?:md|txt|json|csv|html?|pdf|docx?)\b|"
    r"(?:^|\s)(?:[a-z]:[\\/]|/|[a-z0-9_.-]+[\\/])[^\s]+)",
    re.I,
)
_EXPORT_TARGET_RE = re.compile(
    r"(?:研究报告|报告|文件|路径|research\s+report|report|file|path|\.md\b|\.txt\b|\.json\b)",
    re.I,
)
_EXPORT_NEGATION_RE = re.compile(
    r"(?:不要|不用|无需|不必|不可|不能|别|请勿|禁止|拒绝|"
    r"\bdo\s+not\b|\bdon['\u2019]?t\b|\bnever\b|\bno\s+need\s+to\b|\bwithout\b)",
    re.I,
)
_EXPORT_META_RE = re.compile(
    r"(?:如何|怎么|怎样|解释|说明|教程|能否|是否|可不可以|"
    r"\bhow\s+to\b|\bexplain\b|\bdescribe\b|\btell\s+me\s+how\b|"
    r"\bdocumentation\b|\bguide\b)",
    re.I,
)
_EXPORT_QUOTED_CONTEXT_RE = re.compile(
    r"(?:附件|文件|文本).{0,24}(?:写着|说|提到|包含)|"
    r"\b(?:attachment|file|text)\b.{0,40}\b(?:says?|mentions?|contains?)\b",
    re.I | re.S,
)


def _positive_int(value: object, field: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < 1:
        raise ValueError(f"{field} must be >= 1")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return parsed


def _seconds(value: object, field: str, *, minimum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum:g}")
    return parsed


@dataclass(frozen=True)
class ResearchPolicy:
    """Validated limits and permissions shared by one research turn."""

    max_gap_rounds: int
    max_research_tool_calls: int
    max_web_fetches: int
    max_parallel_subagents: int
    timeout_seconds: float
    synthesis_reserve_seconds: float
    allow_report_export: bool
    report_directory: Path

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any] | None,
        workspace: str | Path,
    ) -> ResearchPolicy:
        values = {**DEFAULT_DEEP_RESEARCH_CONFIG, **dict(config or {})}
        timeout = _seconds(values["timeout_seconds"], "timeout_seconds", minimum=60.0)
        synthesis_reserve = _seconds(
            values["synthesis_reserve_seconds"],
            "synthesis_reserve_seconds",
            minimum=15.0,
        )
        if synthesis_reserve >= timeout:
            raise ValueError("synthesis_reserve_seconds must be less than timeout_seconds")

        workspace_root = Path(workspace).expanduser().resolve()
        report_value = str(values.get("report_directory") or "").strip()
        if not report_value:
            raise ValueError("report_directory must not be empty")
        configured_path = Path(report_value).expanduser()
        report_directory = (
            configured_path.resolve()
            if configured_path.is_absolute()
            else (workspace_root / configured_path).resolve()
        )
        try:
            within_workspace = os.path.commonpath(
                [str(workspace_root), str(report_directory)]
            ) == str(workspace_root)
        except ValueError:
            within_workspace = False
        if not within_workspace:
            raise ValueError("report_directory must resolve inside workspace")

        return cls(
            max_gap_rounds=_positive_int(values["max_gap_rounds"], "max_gap_rounds"),
            max_research_tool_calls=_positive_int(
                values["max_research_tool_calls"],
                "max_research_tool_calls",
            ),
            max_web_fetches=_positive_int(values["max_web_fetches"], "max_web_fetches"),
            max_parallel_subagents=_positive_int(
                values["max_parallel_subagents"],
                "max_parallel_subagents",
                maximum=3,
            ),
            timeout_seconds=timeout,
            synthesis_reserve_seconds=synthesis_reserve,
            allow_report_export=bool(values["allow_report_export"]),
            report_directory=report_directory,
        )


def detect_report_export_intent(request: str) -> bool:
    """Conservatively grant report export only for an explicit action and target."""

    text = str(request or "").strip()
    has_persistent_action = bool(_EXPORT_PERSIST_ACTION_RE.search(text))
    has_write_to_file_action = bool(
        _EXPORT_WRITE_ACTION_RE.search(text) and _EXPORT_FILE_DESTINATION_RE.search(text)
    )
    if (
        not text
        or not _EXPORT_TARGET_RE.search(text)
        or not (has_persistent_action or has_write_to_file_action)
    ):
        return False
    if (
        _EXPORT_NEGATION_RE.search(text)
        or _EXPORT_META_RE.search(text)
        or _EXPORT_QUOTED_CONTEXT_RE.search(text)
    ):
        return False
    return True
