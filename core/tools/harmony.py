"""Agent tools for symbolic harmony analysis."""

from __future__ import annotations

import json
from typing import Any

from core.music_theory.music21_harmony import analyze_harmony

from .base import Tool, ToolCapabilities
from .midi import _request_dashboard_sync


class MusicHarmonyAnalyzeTool(Tool):
    name = "music_harmony_analyze"
    description = (
        "Analyze existing Music Studio MIDI notes with music21 and propose harmony lane "
        "chord labels. Defaults to preview only; set apply=true to write inferred events "
        "to the harmony lane. Use this before generating or revising MIDI from an "
        "existing arrangement."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Project track ids to analyze. Defaults to all MIDI tracks.",
            },
            "range": {
                "type": "array",
                "items": {"type": "number", "minimum": 0},
                "minItems": 2,
                "maxItems": 2,
                "description": "Optional beat range as [start,end].",
            },
            "window_beats": {
                "type": "number",
                "minimum": 0,
                "description": "Harmony analysis window in beats. Defaults to one bar.",
            },
            "min_confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": 0.55,
                "description": "Minimum confidence required for returned and applied events.",
            },
            "apply": {
                "type": "boolean",
                "default": False,
                "description": "When true, write the inferred events to the harmony lane.",
            },
            "mode": {
                "type": "string",
                "enum": ["replace", "append"],
                "default": "replace",
                "description": "Harmony lane write mode when apply=true.",
            },
        },
    }
    capabilities = ToolCapabilities(
        capability="music.harmony.analyze",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        track_ids: list[int] | None = None,
        window_beats: float | None = None,
        min_confidence: float = 0.55,
        apply: bool = False,
        mode: str = "replace",
        **kwargs: Any,
    ) -> str:
        beat_range = kwargs.get("range") or kwargs.get("beat_range")
        try:
            result = analyze_harmony(
                track_ids=track_ids,
                beat_range=beat_range,
                window_beats=window_beats,
                min_confidence=min_confidence,
                apply=apply,
                mode=mode,
            )
        except (RuntimeError, TypeError, ValueError) as e:
            return f"Error: {e}"
        if apply:
            result["sync"] = _request_dashboard_sync()
        return json.dumps(result, ensure_ascii=False, indent=2)
