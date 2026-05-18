"""Agent tools for ATRI's music workstation MIDI project."""

from __future__ import annotations

import json
import logging
from typing import Any

from core.music_project import (
    midi_batch_edit,
    midi_diff,
    midi_inspect,
    midi_query,
    midi_write,
    project_summary,
)

from .base import Tool, ToolCapabilities

logger = logging.getLogger("atri.music_tools")


class MidiWriteTool(Tool):
    name = "midi_write"
    description = (
        "Overwrite or append MIDI notes in the ATRI music workstation project. "
        "Use this for generating melodies, chords, basslines, drum patterns, or "
        "replacing a selected time range. Time values are in beats."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
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
        "Apply precise atomic edits to existing MIDI notes and MIDI events in "
        "the ATRI music workstation project. Use this for humanization, note "
        "fixes, velocity changes, CC automation, pitch bend curves, aftertouch "
        "curves, and small variations. Times are in beats."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
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
                            "enum": [
                                "add_note",
                                "delete_note",
                                "update_note",
                                "modify_note",
                                "add_event",
                                "add_midi_event",
                                "delete_event",
                                "delete_midi_event",
                                "update_event",
                                "modify_event",
                                "update_midi_event",
                                "modify_midi_event",
                                "draw_event_curve",
                                "set_event_curve",
                                "replace_event_curve",
                                "draw_controller_curve",
                                "set_controller_curve",
                                "cc_curve",
                                "pitch_bend_curve",
                                "aftertouch_curve",
                                "channel_pressure_curve",
                                "velocity_curve",
                                "draw_velocity_curve",
                                "set_velocity_curve",
                            ],
                        },
                        "id": {"type": "string", "description": "Existing note id."},
                        "note_id": {"type": "string", "description": "Existing note id alias."},
                        "event_id": {"type": "string", "description": "Existing MIDI event id."},
                        "clip_id": {
                            "type": "string",
                            "description": (
                                "Target MIDI clip id when adding or matching events/notes."
                            ),
                        },
                        "note": {
                            "type": "object",
                            "description": (
                                "Note payload for add_note. start is an absolute project beat; "
                                "use local_start only when intentionally editing clip-local time."
                            ),
                        },
                        "event": {
                            "type": "object",
                            "description": (
                                "MIDI event payload for add_event/update_event. "
                                "start is an absolute project beat. "
                                "Supported types: control_change, pitch_bend, "
                                "channel_pressure, polyphonic_key_pressure, "
                                "program_change, note_on, note_off, all_notes_off, sysex."
                            ),
                        },
                        "target": {
                            "type": "object",
                            "description": (
                                "Curve or event match target, for example "
                                '{"type":"control_change","controller":1,"channel":0}.'
                            ),
                        },
                        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
                        "start": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Absolute beat on the project timeline.",
                        },
                        "local_start": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Clip-local beat. Use only when explicitly needed.",
                        },
                        "end": {"type": "number", "minimum": 0},
                        "duration": {"type": "number", "minimum": 0},
                        "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
                        "event_type": {
                            "type": "string",
                            "description": "MIDI event type for event edits or curves.",
                        },
                        "channel": {"type": "integer", "minimum": 0, "maximum": 15},
                        "controller": {"type": "integer", "minimum": 0, "maximum": 127},
                        "cc": {"type": "integer", "minimum": 0, "maximum": 127},
                        "value": {
                            "type": "integer",
                            "description": (
                                "Event value. CC/pressure/program use 0-127; "
                                "pitch_bend uses -8192..8191."
                            ),
                        },
                        "pressure": {"type": "integer", "minimum": 0, "maximum": 127},
                        "program": {"type": "integer", "minimum": 0, "maximum": 127},
                        "points": {
                            "type": "array",
                            "description": (
                                "Curve points as objects {start,value}. "
                                "Point starts are absolute project beats. For velocity_curve, "
                                "value is note velocity. For aftertouch, value maps to pressure."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "start": {"type": "number", "minimum": 0},
                                    "value": {"type": "integer"},
                                },
                                "required": ["start", "value"],
                                "additionalProperties": False,
                            },
                        },
                        "start_value": {"type": "integer"},
                        "end_value": {"type": "integer"},
                        "resolution": {
                            "type": "number",
                            "minimum": 0,
                            "description": (
                                "Beat spacing for generated event curve points. "
                                "Curves are capped at 4096 generated points. "
                                "Use 0 to keep only explicit points."
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                            "description": (
                                "For event curves, replace matching events in range or append."
                            ),
                        },
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


class MidiBatchEditTool(Tool):
    name = "midi_batch_edit"
    description = (
        "Apply high-level batch MIDI edits from musical intent. Use this instead "
        "of low-level midi_diff when editing many note velocities or drawing CC, "
        "expression, modulation, pitch bend, or aftertouch curves. Supports "
        "selection by track, clip, beat range, pitch range, controller, note ids, "
        "and event ids. A write scope is required: pass track_id, selection.track_ids, "
        "or all_tracks=true."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_id": {
                "type": "integer",
                "description": (
                    "Target track id. Required unless selection.track_ids is set "
                    "or all_tracks is true."
                ),
            },
            "all_tracks": {
                "type": "boolean",
                "default": False,
                "description": "Explicitly allow the batch edit to affect every track.",
            },
            "selection": {
                "type": "object",
                "description": (
                    "Selection filter. Common keys: track_ids, clip_ids, range [start,end], "
                    "pitch_range [low,high], note_ids, event_ids, controllers, "
                    "event_types, channel."
                ),
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Preview the summary without saving or syncing.",
            },
            "operations": {
                "type": "array",
                "description": "High-level batch edit operations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": [
                                "velocity_set",
                                "velocity_scale",
                                "velocity_humanize",
                                "velocity_accent",
                                "velocity_shape",
                                "velocity_ramp",
                                "velocity_curve",
                                "cc_curve",
                                "controller_curve",
                                "expression_curve",
                                "modulation_curve",
                                "pitch_bend_curve",
                                "aftertouch_curve",
                                "channel_pressure_curve",
                                "cc_clear",
                                "controller_clear",
                                "event_clear",
                            ],
                        },
                        "selection": {
                            "type": "object",
                            "description": "Operation-local selection override.",
                        },
                        "range": {
                            "type": "array",
                            "description": "Beat range as [start,end].",
                            "items": {"type": "number", "minimum": 0},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                        "shape": {
                            "type": "string",
                            "enum": [
                                "linear",
                                "crescendo",
                                "decrescendo",
                                "swell",
                                "phrase_swell",
                                "fade_in",
                                "fade_out",
                                "ease_in",
                                "ease_out",
                                "ease_in_out",
                                "lfo",
                                "step",
                                "hold",
                            ],
                        },
                        "value": {"type": "integer"},
                        "velocity": {"type": "integer", "minimum": 1, "maximum": 127},
                        "from": {"type": "integer"},
                        "to": {"type": "integer"},
                        "start_value": {"type": "integer"},
                        "end_value": {"type": "integer"},
                        "min": {"type": "integer"},
                        "max": {"type": "integer"},
                        "amount": {
                            "type": "integer",
                            "description": "Humanize/accent amount in velocity units.",
                        },
                        "factor": {"type": "number"},
                        "offset": {"type": "number"},
                        "pattern": {
                            "type": "string",
                            "description": (
                                "For velocity_accent: downbeats, backbeat, offbeat, "
                                "or custom every/offset."
                            ),
                        },
                        "every": {"type": "number"},
                        "controller": {"type": "integer", "minimum": 0, "maximum": 127},
                        "cc": {"type": "integer", "minimum": 0, "maximum": 127},
                        "channel": {"type": "integer", "minimum": 0, "maximum": 15},
                        "points": {
                            "type": "array",
                            "description": ("Explicit curve points as {start,value} objects."),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "start": {"type": "number", "minimum": 0},
                                    "value": {"type": "integer"},
                                },
                                "required": ["start", "value"],
                                "additionalProperties": False,
                            },
                        },
                        "resolution": {
                            "type": "number",
                            "description": (
                                "Beat spacing for generated controller events. Default is 0.25. "
                                "Curves are capped at 4096 generated points."
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                            "description": (
                                "For curves, replace matching events in range or append."
                            ),
                        },
                    },
                    "required": ["op"],
                },
            },
        },
        "required": ["operations"],
    }
    capabilities = ToolCapabilities(capability="music.midi.write")

    def execute(
        self,
        operations: list[dict[str, Any]],
        track_id: int | None = None,
        selection: dict[str, Any] | None = None,
        all_tracks: bool = False,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> str:
        project, summary = midi_batch_edit(
            operations,
            track_id=track_id,
            selection=selection,
            all_tracks=all_tracks,
            dry_run=dry_run,
        )
        sync_note = "" if dry_run else _request_dashboard_sync()
        return _format_result(
            "MIDI batch edit applied", summary, project_summary(project), sync_note
        )


class MidiQueryTool(Tool):
    name = "midi_query"
    description = (
        "Read a compact summary of the MIDI project or a selected region before "
        "editing. Use this to inspect track/clip counts, velocity stats, note "
        "ranges, and existing CC/event lanes without dumping every note."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_id": {"type": "integer"},
            "selection": {
                "type": "object",
                "description": (
                    "Selection filter: track_ids, clip_ids, range, pitch_range, "
                    "controllers, event_types."
                ),
            },
            "include": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["tracks", "clips", "notes", "velocity", "events", "controllers"],
                },
            },
        },
    }
    capabilities = ToolCapabilities(
        capability="music.midi.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(
        self,
        track_id: int | None = None,
        selection: dict[str, Any] | None = None,
        include: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        return json.dumps(
            midi_query(track_id=track_id, selection=selection, include=include),
            ensure_ascii=False,
            indent=2,
        )


class MidiInspectTool(Tool):
    name = "midi_inspect"
    description = (
        "Read detailed MIDI notes and events for a selected region with bounded "
        "pagination. Use this when you need exact note ids, velocities, timings, "
        "or CC/event values before making precise edits."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_id": {"type": "integer"},
            "selection": {
                "type": "object",
                "description": (
                    "Selection filter: track_ids, clip_ids, range, pitch_range, "
                    "note_ids, event_ids, controllers, event_types."
                ),
            },
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": ["notes", "events", "midi_events"]},
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "default": 120,
            },
            "offset": {"type": "integer", "minimum": 0, "default": 0},
        },
    }
    capabilities = ToolCapabilities(
        capability="music.midi.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(
        self,
        track_id: int | None = None,
        selection: dict[str, Any] | None = None,
        include: list[str] | None = None,
        limit: int = 120,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        return json.dumps(
            midi_inspect(
                track_id=track_id,
                selection=selection,
                include=include,
                limit=limit,
                offset=offset,
            ),
            ensure_ascii=False,
            indent=2,
        )


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
        f"{session_summary.get('midi_event_count', 0)} MIDI event(s), "
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
