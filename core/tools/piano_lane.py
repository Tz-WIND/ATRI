"""Agent tools for Music Studio piano roll meter and harmony lanes."""

from __future__ import annotations

from typing import Any

from core.music_project import piano_lane_diff, piano_lane_write

from .base import Tool, ToolCapabilities
from .midi import _request_dashboard_sync


def _piano_lane_event_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "beat": {"type": "number", "minimum": 0},
            "start": {"type": "number", "minimum": 0},
            "numerator": {"type": "integer", "minimum": 1, "maximum": 255},
            "denominator": {"type": "integer", "enum": [2, 4, 8, 16, 32]},
            "text": {"type": "string"},
            "label": {"type": "string"},
            "name": {"type": "string"},
            "chord": {"type": "string"},
        },
    }


def _piano_lane_event_array_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": _piano_lane_event_schema(),
    }


class StudioPianoLaneWriteTool(Tool):
    name = "studio_piano_lane_write"
    description = (
        "Replace or append Music Studio piano roll meter or harmony lane events. "
        "Use this for time signature markers and chord/harmony labels instead of "
        "automation_write."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "lane": {
                "type": "string",
                "enum": ["meter", "harmony"],
                "description": "Piano lane to edit.",
            },
            "mode": {
                "type": "string",
                "enum": ["replace", "append"],
                "default": "replace",
            },
            "start": {
                "type": "number",
                "minimum": 0,
                "description": "Optional start beat of the replace range.",
            },
            "end": {
                "type": "number",
                "minimum": 0,
                "description": "Optional end beat of the replace range.",
            },
            "events": _piano_lane_event_array_schema(),
        },
        "required": ["lane", "events"],
    }
    capabilities = ToolCapabilities(
        capability="music.studio.piano_lane",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        lane: str,
        events: list[dict[str, Any]],
        mode: str = "replace",
        start: float | None = None,
        end: float | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            _project, summary = piano_lane_write(
                lane,
                events,
                mode=mode,
                start=start,
                end=end,
            )
        except (TypeError, ValueError) as e:
            return f"Error: {e}"
        return _format_piano_lane_result("Piano lane written", summary, _request_dashboard_sync())


class StudioPianoLaneDiffTool(Tool):
    name = "studio_piano_lane_diff"
    description = (
        "Apply atomic edits to Music Studio piano roll meter or harmony lane events. "
        "Supports add_event, update_event, delete_event, and replace_range by beat."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "lane": {
                "type": "string",
                "enum": ["meter", "harmony"],
                "description": "Piano lane to edit.",
            },
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": [
                                "add_event",
                                "update_event",
                                "delete_event",
                                "replace_range",
                            ],
                        },
                        "beat": {"type": "number", "minimum": 0},
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                        "numerator": {"type": "integer", "minimum": 1, "maximum": 255},
                        "denominator": {"type": "integer", "enum": [2, 4, 8, 16, 32]},
                        "text": {"type": "string"},
                        "event": _piano_lane_event_schema(),
                        "events": _piano_lane_event_array_schema(),
                    },
                    "required": ["op"],
                },
            },
        },
        "required": ["lane", "operations"],
    }
    capabilities = ToolCapabilities(
        capability="music.studio.piano_lane",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        lane: str,
        operations: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        try:
            _project, summary = piano_lane_diff(lane, operations)
        except (TypeError, ValueError) as e:
            return f"Error: {e}"
        return _format_piano_lane_result(
            "Piano lane diff applied",
            summary,
            _request_dashboard_sync(),
        )


def _format_piano_lane_result(
    title: str,
    operation_summary: dict[str, Any],
    sync_note: str,
) -> str:
    project = operation_summary.get("project") if isinstance(operation_summary, dict) else {}
    track_count = project.get("track_count", 0) if isinstance(project, dict) else 0
    meter_count = len(project.get("meter_events", [])) if isinstance(project, dict) else 0
    harmony_count = len(project.get("harmony_events", [])) if isinstance(project, dict) else 0
    return "\n".join(
        [
            title + ".",
            f"Operation: {operation_summary}",
            (
                f"Project: {track_count} track(s), {meter_count} meter event(s), "
                f"{harmony_count} harmony event(s)."
            ),
            sync_note,
        ]
    )
