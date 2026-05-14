"""Agent tools for ATRI's music workstation MIDI project."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from core.music_project import midi_diff, midi_write, project_summary

from .base import Tool, ToolCapabilities

logger = logging.getLogger("atri.music_tools")


class MidiWriteTool(Tool):
    name = "midi_write"
    description = (
        "Overwrite or append MIDI notes in the ATRI music workstation project. "
        "Use this for generating melodies, chords, basslines, drum patterns, or "
        "replacing a selected time range. Time values are in beats."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "track_id": {
                "type": "integer",
                "description": (
                    "Target track id. Prefer the project track id; Rust host track ids "
                    "such as 0 are also accepted when the project has host_track_id set."
                ),
            },
            "start": {
                "type": "number",
                "description": "Start beat of the overwrite range.",
            },
            "end": {
                "type": "number",
                "description": "End beat of the overwrite range.",
            },
            "mode": {
                "type": "string",
                "enum": ["replace", "append"],
                "default": "replace",
                "description": "replace removes overlapping notes; append keeps existing notes.",
            },
            "notes": {
                "type": "array",
                "description": "Notes to write. start and duration are in beats.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
                        "start": {"type": "number", "minimum": 0},
                        "duration": {"type": "number", "minimum": 0},
                        "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
                    },
                    "required": ["pitch", "start", "duration", "velocity"],
                },
            },
        },
        "required": ["track_id", "notes"],
    }
    capabilities = ToolCapabilities(capability="music.midi.write")

    def execute(
        self,
        track_id: int,
        notes: list[dict[str, Any]],
        start: float | None = None,
        end: float | None = None,
        mode: str = "replace",
        **kwargs: Any,
    ) -> str:
        project, summary = midi_write(track_id, notes, start=start, end=end, mode=mode)
        sync_note = _request_dashboard_sync()
        return _format_result("MIDI written", summary, project_summary(project), sync_note)


class MidiDiffTool(Tool):
    name = "midi_diff"
    description = (
        "Apply precise atomic edits to existing MIDI notes in the ATRI music "
        "workstation project. Use this for humanization, note fixes, velocity "
        "changes, transposition by explicit updates, and small variations."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "track_id": {
                "type": "integer",
                "description": (
                    "Target track id. Prefer the project track id; Rust host track ids "
                    "such as 0 are also accepted when the project has host_track_id set."
                ),
            },
            "operations": {
                "type": "array",
                "description": "Atomic MIDI edit operations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["add_note", "delete_note", "update_note", "modify_note"],
                        },
                        "id": {"type": "string", "description": "Existing note id."},
                        "note_id": {"type": "string", "description": "Existing note id alias."},
                        "note": {
                            "type": "object",
                            "description": "Note payload for add_note.",
                        },
                        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
                        "start": {"type": "number", "minimum": 0},
                        "duration": {"type": "number", "minimum": 0},
                        "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
                    },
                    "required": ["op"],
                },
            },
        },
        "required": ["track_id", "operations"],
    }
    capabilities = ToolCapabilities(capability="music.midi.write")

    def execute(
        self,
        track_id: int,
        operations: list[dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        project, summary = midi_diff(track_id, operations)
        sync_note = _request_dashboard_sync()
        return _format_result("MIDI diff applied", summary, project_summary(project), sync_note)


def _format_result(
    title: str,
    operation_summary: dict[str, Any],
    session_summary: dict[str, Any],
    sync_note: str,
) -> str:
    lines = [title + "."]
    lines.append(f"Operation: {operation_summary}")
    lines.append(
        "Project: "
        f"{session_summary['track_count']} track(s), "
        f"{session_summary['note_count']} note(s), "
        f"{session_summary['tempo']} BPM."
    )
    if sync_note:
        lines.append(sync_note)
    return "\n".join(lines)


def _request_dashboard_sync() -> str:
    try:
        import httpx
    except ImportError:
        return "Project saved; dashboard sync was unavailable."

    try:
        response = httpx.post(
            "http://127.0.0.1:6185/api/music/studio/sync",
            json={"broadcast": True},
            timeout=2,
        )
        if response.status_code < 400:
            return "Dashboard sync requested."
    except httpx.HTTPError as e:
        logger.debug("Music workstation dashboard sync failed: %s", e)
    return "Project saved; dashboard sync was unavailable."
