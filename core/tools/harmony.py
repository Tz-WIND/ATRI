"""Agent tools for symbolic harmony analysis."""

from __future__ import annotations

import json
from typing import Any

from core.music_theory.music21_harmony import analyze_harmony
from core.music_theory.music21_transform import transpose_music

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
            "key_window_beats": {
                "type": "number",
                "minimum": 0,
                "description": "Key analysis window in beats for modulation detection.",
            },
            "detect_modulations": {
                "type": "boolean",
                "default": True,
                "description": "When true, report key events and modulation points.",
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
        key_window_beats: float | None = None,
        detect_modulations: bool = True,
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
                key_window_beats=key_window_beats,
                detect_modulations=detect_modulations,
                min_confidence=min_confidence,
                apply=apply,
                mode=mode,
            )
        except (RuntimeError, TypeError, ValueError) as e:
            return f"Error: {e}"
        if apply:
            result["sync"] = _request_dashboard_sync()
        return json.dumps(result, ensure_ascii=False, indent=2)


class MusicTransposeTool(Tool):
    name = "music_transpose"
    description = (
        "Preview or apply MIDI note transposition in Music Studio, with optional harmony "
        "lane chord-label transposition. Provide semitones, or from_key and to_key. "
        "from_key and to_key accept root names only, not major/minor modes. "
        "Defaults to preview only; set apply=true to write changes."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Project track ids to transpose. Defaults to all MIDI tracks.",
            },
            "range": {
                "type": "array",
                "items": {"type": "number", "minimum": 0},
                "minItems": 2,
                "maxItems": 2,
                "description": "Optional beat range as [start,end].",
            },
            "semitones": {
                "type": "integer",
                "description": "Chromatic transposition amount. Positive moves up.",
            },
            "from_key": {
                "type": "string",
                "description": (
                    "Source root name when semitones is omitted, for example C or Bb. "
                    "Do not include major/minor mode."
                ),
            },
            "to_key": {
                "type": "string",
                "description": (
                    "Target root name when semitones is omitted, for example D or G. "
                    "Do not include major/minor mode."
                ),
            },
            "transpose_harmony": {
                "type": "boolean",
                "default": True,
                "description": "Transpose harmony lane chord labels in the selected range.",
            },
            "apply": {
                "type": "boolean",
                "default": False,
                "description": "When true, write transposed MIDI notes and harmony labels.",
            },
        },
    }
    capabilities = ToolCapabilities(
        capability="music.transpose.write",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        track_ids: list[int] | None = None,
        semitones: int | None = None,
        from_key: str = "",
        to_key: str = "",
        transpose_harmony: bool = True,
        apply: bool = False,
        **kwargs: Any,
    ) -> str:
        beat_range = kwargs.get("range") or kwargs.get("beat_range")
        try:
            result = transpose_music(
                track_ids=track_ids,
                beat_range=beat_range,
                semitones=semitones,
                from_key=from_key,
                to_key=to_key,
                transpose_harmony=transpose_harmony,
                apply=apply,
            )
        except (TypeError, ValueError) as e:
            return f"Error: {e}"
        if apply:
            result["sync"] = _request_dashboard_sync()
        return json.dumps(result, ensure_ascii=False, indent=2)
