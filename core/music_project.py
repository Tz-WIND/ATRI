"""Persistent AI music workstation project state.

The Rust host is intentionally a realtime renderer, not the source of truth for
session data. This module keeps the editable DAW project in JSON so the
dashboard, Agent tools, and host sync path all operate on the same data model.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from copy import deepcopy
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from core.utils import atomic_write_text

PROJECT_PATH = Path("data/music_workstation/project.json")

DEFAULT_TRACK_COLORS = ["#4e79ff", "#d95b55", "#5f916b", "#d7b66f", "#b489d6", "#58a7b8"]

MIDI_EVENT_OPERATION_NAMES = {
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
}

MIDI_CURVE_EVENT_TYPES = {
    "control_change",
    "pitch_bend",
    "channel_pressure",
    "polyphonic_key_pressure",
}

TRACK_AUTOMATION_TARGET_KINDS = {"plugin_parameter", "track_volume", "track_pan"}
GLOBAL_AUTOMATION_TARGET_KINDS = {"tempo_bpm", "time_signature_numerator"}

MIDI_CURVE_MAX_POINTS = 4096
METER_DENOMINATORS = {2, 4, 8, 16, 32}
MAX_METER_NUMERATOR = 255


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_project() -> dict[str, Any]:
    """Return a small playable starter project."""
    return {
        "version": 1,
        "title": "ATRI Session",
        "tempo": 120.0,
        "time_signature": [4, 4],
        "length_beats": 16.0,
        "updated_at": _now_iso(),
        "automation_learned_parameters": [],
        "tracks": [
            {
                "id": 1,
                "host_track_id": None,
                "name": "Impact Lead",
                "color": DEFAULT_TRACK_COLORS[0],
                "volume": 0.82,
                "pan": 0.0,
                "mute": False,
                "solo": False,
                "instrument": "ATRI Basic Synth",
                "notes": [
                    {"id": "demo_1", "pitch": 60, "start": 0.0, "duration": 0.75, "velocity": 92},
                    {"id": "demo_2", "pitch": 64, "start": 1.0, "duration": 0.75, "velocity": 86},
                    {"id": "demo_3", "pitch": 67, "start": 2.0, "duration": 1.0, "velocity": 94},
                    {"id": "demo_4", "pitch": 72, "start": 3.5, "duration": 0.5, "velocity": 88},
                    {"id": "demo_5", "pitch": 71, "start": 4.0, "duration": 0.75, "velocity": 82},
                    {"id": "demo_6", "pitch": 67, "start": 5.0, "duration": 0.75, "velocity": 88},
                    {"id": "demo_7", "pitch": 64, "start": 6.0, "duration": 1.0, "velocity": 84},
                    {"id": "demo_8", "pitch": 60, "start": 7.5, "duration": 0.5, "velocity": 90},
                ],
            },
            {
                "id": 2,
                "host_track_id": None,
                "name": "Sub Pulse",
                "color": DEFAULT_TRACK_COLORS[2],
                "volume": 0.7,
                "pan": -0.08,
                "mute": False,
                "solo": False,
                "instrument": "ATRI Basic Synth",
                "notes": [
                    {"id": "demo_b1", "pitch": 36, "start": 0.0, "duration": 1.5, "velocity": 78},
                    {"id": "demo_b2", "pitch": 36, "start": 2.0, "duration": 1.0, "velocity": 74},
                    {"id": "demo_b3", "pitch": 43, "start": 4.0, "duration": 1.5, "velocity": 82},
                    {"id": "demo_b4", "pitch": 41, "start": 6.0, "duration": 1.25, "velocity": 76},
                ],
            },
        ],
    }


def load_project(path: Path | str = PROJECT_PATH) -> dict[str, Any]:
    project_path = Path(path)
    if not project_path.exists():
        project = default_project()
        save_project(project, project_path)
        return project

    try:
        loaded = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        loaded = default_project()
    return normalize_project(loaded)


def save_project(project: dict[str, Any], path: Path | str = PROJECT_PATH) -> dict[str, Any]:
    normalized = normalize_project(project)
    normalized["updated_at"] = _now_iso()
    project_path = Path(path)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        project_path,
        json.dumps(normalized, ensure_ascii=False, indent=2),
        prefix=".music_project_",
    )
    return normalized


def normalize_project(project: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(project, dict):
        project = {}

    base = default_project()
    normalized: dict[str, Any] = {
        "version": 1,
        "title": str(project.get("title") or base["title"]),
        "tempo": _positive_float(project.get("tempo"), base["tempo"]),
        "time_signature": _normalize_meter(project.get("time_signature")),
        "length_beats": _positive_float(project.get("length_beats"), base["length_beats"]),
        "updated_at": str(project.get("updated_at") or _now_iso()),
        "automation_learned_parameters": _normalize_learned_parameters(
            project.get("automation_learned_parameters")
        ),
        "tracks": [],
    }

    raw_tracks = project.get("tracks")
    if not isinstance(raw_tracks, list):
        raw_tracks = deepcopy(base["tracks"])

    used_ids: set[int] = set()
    next_id = 1
    for index, raw_track in enumerate(raw_tracks):
        if not isinstance(raw_track, dict):
            continue
        track_id = _positive_int(raw_track.get("id"), next_id)
        while track_id in used_ids:
            track_id += 1
        used_ids.add(track_id)
        next_id = max(next_id, track_id + 1)

        track_color = _track_color(raw_track.get("color"), index)
        declared_type = (
            str(raw_track.get("type", raw_track.get("track_type", "")) or "").strip().lower()
        )
        if declared_type == "automation":
            clips: list[dict[str, Any]] = []
            notes: list[dict[str, Any]] = []
            midi_events: list[dict[str, Any]] = []
            track_type = "automation"
        else:
            legacy_notes = [
                _normalize_note(note)
                for note in raw_track.get("notes", [])
                if isinstance(note, dict)
            ]
            legacy_notes.sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
            clips = _normalize_clips(raw_track, legacy_notes=legacy_notes, track_color=track_color)
            notes = _flatten_clip_notes(clips)
            midi_events = _flatten_clip_midi_events(clips)
            track_type = _normalize_track_type(raw_track, clips=clips)

        normalized_track: dict[str, Any] = {
            "id": track_id,
            "host_track_id": None
            if track_type == "automation"
            else _nullable_non_negative_int(raw_track.get("host_track_id")),
            "type": track_type,
            "channel_type": _normalize_track_channel_type(
                raw_track.get("channel_type"),
                track_type=track_type,
            ),
            "name": str(raw_track.get("name") or f"Track {track_id}"),
            "color": track_color,
            "volume": _bounded_float(raw_track.get("volume"), 0.8, 0.0, 2.0),
            "pan": _bounded_float(raw_track.get("pan"), 0.0, -1.0, 1.0),
            "mute": bool(raw_track.get("mute", False)),
            "solo": bool(raw_track.get("solo", False)),
            "instrument": str(
                raw_track.get("instrument")
                or (
                    "Automation"
                    if track_type == "automation"
                    else "Bus"
                    if track_type == "bus"
                    else "Audio Track"
                    if track_type == "audio"
                    else "ATRI Basic Synth"
                )
            ),
            "plugin_slots": _normalize_plugin_slots(raw_track, track_type=track_type),
            "output_bus_id": _nullable_non_negative_int(raw_track.get("output_bus_id")),
            "sends": []
            if track_type == "automation"
            else _normalize_track_sends(raw_track),
            "clips": clips,
            "notes": notes,
            "midi_events": midi_events,
        }
        if track_type == "automation":
            normalized_track["target"] = _normalize_automation_target(raw_track.get("target"))
            normalized_track["automation"] = _normalize_automation_payload(
                raw_track.get("automation"),
                target=normalized_track["target"],
            )
        normalized["tracks"].append(normalized_track)

    if not normalized["tracks"]:
        normalized["tracks"] = deepcopy(base["tracks"])

    _repair_output_bus_routing(normalized["tracks"])

    max_clip_end = max(
        (
            clip["start"] + clip["duration"]
            for track in normalized["tracks"]
            for clip in track["clips"]
        ),
        default=0.0,
    )
    max_automation_end = max(
        (
            point["beat"]
            for track in normalized["tracks"]
            if track.get("type") == "automation"
            for point in track.get("automation", {}).get("points", [])
            if isinstance(point, dict)
        ),
        default=0.0,
    )
    max_end = max(max_clip_end, max_automation_end)
    normalized["length_beats"] = max(normalized["length_beats"], _ceil_to_bar(max_end))
    return normalized


def create_track(
    name: str = "Instrument",
    *,
    color: str | None = None,
    track_type: str = "instrument",
    channel_type: str = "multichannel",
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_project()
    existing = [int(track["id"]) for track in project["tracks"]]
    track_id = max(existing, default=0) + 1
    normalized_type = _normalize_track_type({"type": track_type}, clips=[])
    normalized_channel_type = _normalize_track_channel_type(
        channel_type,
        track_type=normalized_type,
    )
    track: dict[str, Any] = {
        "id": track_id,
        "host_track_id": None,
        "type": normalized_type,
        "channel_type": normalized_channel_type,
        "name": name.strip() or f"Track {track_id}",
        "color": _track_color(color, track_id - 1),
        "volume": 0.8,
        "pan": 0.0,
        "mute": False,
        "solo": False,
        "instrument": "Bus"
        if normalized_type == "bus"
        else "Audio Track"
        if normalized_type == "audio"
        else "ATRI Basic Synth",
        "plugin_slots": _normalize_plugin_slots(
            {
                "plugin_slots": [] if normalized_type == "bus" else None,
                "instrument": "ATRI Basic Synth",
            },
            track_type=normalized_type,
        ),
        "output_bus_id": None,
        "sends": [],
        "clips": [],
        "notes": [],
        "midi_events": [],
    }
    project["tracks"].append(track)
    project = save_project(project)
    return project, find_track(project, track_id)


def import_audio_clip(
    path: str | Path,
    *,
    name: str = "",
    start: float = 0.0,
    duration_seconds: float = 0.0,
    waveform: list[Any] | None = None,
    source: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create a new audio track containing a single imported audio clip."""
    project = load_project()
    existing = [int(track["id"]) for track in project["tracks"]]
    track_id = max(existing, default=0) + 1
    track_color = _track_color(None, track_id - 1)
    source_path = Path(path)
    clip_name = name.strip() or source_path.stem or "Audio Clip"
    tempo = _positive_float(project.get("tempo"), 120.0)
    seconds = _non_negative_float(duration_seconds, 0.0)
    duration_beats = max(0.25, seconds * tempo / 60.0) if seconds > 0 else 4.0
    clip: dict[str, Any] = {
        "id": f"clip_{uuid4().hex[:10]}",
        "type": "audio",
        "name": clip_name,
        "start": _non_negative_float(start, 0.0),
        "duration": duration_beats,
        "duration_seconds": seconds,
        "color": track_color,
        "source": Path(source).as_posix() if source is not None else source_path.as_posix(),
        "path": source_path.as_posix(),
        "source_offset": 0.0,
        "gain": 1.0,
        "waveform": _normalize_waveform(waveform),
        "notes": [],
        "events": [],
    }
    track: dict[str, Any] = {
        "id": track_id,
        "host_track_id": None,
        "type": "audio",
        "channel_type": "multichannel",
        "name": clip_name,
        "color": track_color,
        "volume": 0.8,
        "pan": 0.0,
        "mute": False,
        "solo": False,
        "instrument": "Audio Track",
        "plugin_slots": [],
        "clips": [clip],
        "notes": [],
        "midi_events": [],
    }
    project["tracks"].append(track)
    project = save_project(project)
    synced_track = find_track(project, track_id)
    clip_id = clip["id"]
    synced_clip = next(item for item in synced_track.get("clips", []) if item.get("id") == clip_id)
    return project, synced_track, synced_clip


def delete_track(track_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove a track from the project while keeping at least one track."""
    project = load_project()
    track = find_track(project, track_id)
    if len(project["tracks"]) <= 1:
        raise ValueError("cannot delete the last track")

    deleted_id = int(track["id"])
    project["tracks"] = [
        item for item in project["tracks"] if int(item.get("id", -1)) != deleted_id
    ]
    for item in project["tracks"]:
        if item.get("output_bus_id") == deleted_id:
            item["output_bus_id"] = None
        item["sends"] = [
            send
            for send in item.get("sends", [])
            if isinstance(send, dict) and send.get("target_bus_id") != deleted_id
        ]
    project = save_project(project)
    return project, track


def update_track(track_id: int, updates: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_project()
    track = find_track(project, track_id)
    if "name" in updates:
        track["name"] = str(updates["name"]).strip() or track["name"]
    if "color" in updates:
        track["color"] = _track_color(updates["color"], track_id - 1)
    if "volume" in updates:
        track["volume"] = _bounded_float(updates["volume"], track["volume"], 0.0, 2.0)
    if "pan" in updates:
        track["pan"] = _bounded_float(updates["pan"], track["pan"], -1.0, 1.0)
    if "mute" in updates:
        track["mute"] = bool(updates["mute"])
    if "solo" in updates:
        track["solo"] = bool(updates["solo"])
    if "output_bus_id" in updates:
        track["output_bus_id"] = _nullable_non_negative_int(updates.get("output_bus_id"))
    if "sends" in updates and isinstance(updates["sends"], list):
        track["sends"] = _normalize_track_sends({"sends": updates["sends"]})
    if "type" in updates or "track_type" in updates:
        track["type"] = _normalize_track_type(
            {"type": updates.get("type", updates.get("track_type"))},
            clips=track.get("clips", []),
        )
        if track["type"] == "audio":
            track["instrument"] = "Audio Track"
            track["plugin_slots"] = []
        elif track["type"] == "bus":
            track["instrument"] = "Bus"
            track["plugin_slots"] = _normalize_plugin_slots(track, track_type="bus")
        else:
            track["plugin_slots"] = _normalize_plugin_slots(track, track_type="instrument")
    if "channel_type" in updates:
        track["channel_type"] = _normalize_track_channel_type(
            updates["channel_type"],
            track_type=str(track.get("type") or "instrument"),
        )
    if "instrument" in updates:
        track["instrument"] = str(updates["instrument"] or "ATRI Basic Synth")
    if "clips" in updates and isinstance(updates["clips"], list):
        track["clips"] = updates["clips"]
    if "plugin_slots" in updates and isinstance(updates["plugin_slots"], list):
        track["plugin_slots"] = _normalize_plugin_slots(
            {"plugin_slots": updates["plugin_slots"]},
            track_type=str(track.get("type") or "instrument"),
        )
    project = save_project(project)
    return project, find_track(project, track_id)


def set_track_plugin(
    track_id: int,
    plugin: dict[str, Any] | None,
    *,
    slot_id: str = "instrument",
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_project()
    track = find_track(project, track_id)
    if track.get("type") != "instrument":
        raise ValueError("only instrument tracks support instrument plugins")
    slot = _normalize_plugin_slot(plugin, slot_id=slot_id)
    slots = [s for s in track.get("plugin_slots", []) if s.get("id") != slot["id"]]
    track["plugin_slots"] = _sort_plugin_slots([slot, *slots])
    if slot["id"] == "instrument":
        track["instrument"] = slot["name"]
    project = save_project(project)
    return project, find_track(project, track_id)


def automation_write(
    target: dict[str, Any],
    *,
    points: list[dict[str, Any]] | None = None,
    name: str = "",
    track_id: int | None = None,
    color: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create or replace a first-class project automation track."""
    project = load_project()
    normalized_target = _normalize_automation_target(target)
    automation = _normalize_automation_payload({"points": points or []}, target=normalized_target)
    created = track_id is None

    if track_id is None:
        existing = [int(track["id"]) for track in project["tracks"]]
        track_id = max(existing, default=0) + 1
        track = _new_automation_track(
            track_id,
            target=normalized_target,
            automation=automation,
            name=name,
            color=color,
        )
        project["tracks"].append(track)
    else:
        track = find_track(project, track_id)
        if track.get("type") != "automation":
            raise ValueError(f"track {track_id} is not an automation track")
        track["target"] = normalized_target
        track["automation"] = automation
        if name:
            track["name"] = str(name).strip() or track["name"]
        if color is not None:
            track["color"] = _track_color(color, int(track["id"]) - 1)

    project = save_project(project)
    saved_track = find_track(project, track_id)
    summary = {
        "track_id": int(saved_track["id"]),
        "created": created,
        "target": saved_track["target"],
        "points": len(saved_track["automation"]["points"]),
        "target_status": _automation_target_status(project, saved_track["target"]),
    }
    return project, summary


def automation_diff(
    track_id: int,
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply atomic edits to an existing automation track."""
    project = load_project()
    track = find_track(project, track_id)
    if track.get("type") != "automation":
        raise ValueError(f"track {track_id} is not an automation track")

    target = _normalize_automation_target(track.get("target"))
    automation = _normalize_automation_payload(track.get("automation"), target=target)
    points = list(automation["points"])
    changed = {"added": 0, "updated": 0, "deleted": 0}

    for raw_op in operations:
        if not isinstance(raw_op, dict):
            continue
        op = dict(raw_op)
        op_type = str(op.get("op") or op.get("type") or "").strip().lower()
        if op_type in {"add_point", "add"}:
            point = _normalize_automation_point(op, target=target)
            points = _upsert_automation_point(points, point)
            changed["added"] += 1
        elif op_type in {"update_point", "update"}:
            point = _normalize_automation_point(op, target=target)
            points, updated = _update_automation_point(points, point)
            changed["updated"] += updated
        elif op_type in {"delete_point", "delete"}:
            points, deleted = _delete_automation_point(points, op)
            changed["deleted"] += deleted
        elif op_type in {"replace_range", "replace"}:
            start = _non_negative_float(op.get("start"), 0.0)
            end = _non_negative_float(op.get("end"), start)
            lo, hi = min(start, end), max(start, end)
            kept = [point for point in points if not lo - 1e-6 <= point["beat"] <= hi + 1e-6]
            changed["deleted"] += len(points) - len(kept)
            points = kept
            for raw_point in op.get("points") or []:
                if not isinstance(raw_point, dict):
                    continue
                point = _normalize_automation_point(raw_point, target=target)
                points = _upsert_automation_point(points, point)
                changed["added"] += 1
        else:
            raise ValueError(f"unsupported automation diff operation: {op_type}")

    automation["points"] = _normalize_automation_points(points, target=target)
    track["target"] = target
    track["automation"] = automation
    project = save_project(project)
    summary = {
        "track_id": int(track["id"]),
        "operations": len(operations),
        **changed,
    }
    return project, summary


def automation_retarget(
    track_id: int,
    target: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_project()
    track = find_track(project, track_id)
    if track.get("type") != "automation":
        raise ValueError(f"track {track_id} is not an automation track")
    normalized_target = _normalize_automation_target(target)
    track["target"] = normalized_target
    track["automation"] = _normalize_automation_payload(
        track.get("automation"),
        target=normalized_target,
    )
    project = save_project(project)
    saved_track = find_track(project, track_id)
    summary = {
        "track_id": int(saved_track["id"]),
        "target": saved_track["target"],
        "target_status": _automation_target_status(project, saved_track["target"]),
    }
    return project, summary


def automation_query(
    *,
    track_id: int | None = None,
    include_points: bool = False,
) -> dict[str, Any]:
    project = load_project()
    automation_tracks = [
        track
        for track in project.get("tracks", [])
        if isinstance(track, dict)
        and track.get("type") == "automation"
        and (track_id is None or int(track.get("id", -1)) == int(track_id))
    ]
    rows = [
        _automation_track_summary(project, track, include_points=include_points)
        for track in automation_tracks
    ]
    return {
        "automation_track_count": len(rows),
        "tracks": rows,
    }


def automation_learned_parameters_query() -> dict[str, Any]:
    project = load_project()
    return {
        "learned_parameter_count": len(project.get("automation_learned_parameters", [])),
        "items": deepcopy(project.get("automation_learned_parameters", [])),
    }


def automation_learned_parameter_upsert(
    parameter: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_project()
    learned = _normalize_learned_parameter(parameter)
    items = list(project.get("automation_learned_parameters", []))
    existing_index = next(
        (index for index, item in enumerate(items) if item.get("id") == learned["id"]),
        None,
    )
    if existing_index is None:
        items.append(learned)
        saved_item = learned
        created = True
    else:
        previous = items[existing_index]
        saved_item = {
            **learned,
            "name": str(previous.get("name") or learned["name"]),
            "created_at": str(previous.get("created_at") or learned["created_at"]),
        }
        items[existing_index] = saved_item
        created = False
    project["automation_learned_parameters"] = items
    project = save_project(project)
    saved = next(
        item
        for item in project.get("automation_learned_parameters", [])
        if item["id"] == saved_item["id"]
    )
    return project, {**deepcopy(saved), "created": created}


def automation_learned_parameter_rename(
    parameter_id: str,
    name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_project()
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("learned parameter name is required")
    items = list(project.get("automation_learned_parameters", []))
    for item in items:
        if item.get("id") == parameter_id:
            item["name"] = clean_name
            project["automation_learned_parameters"] = items
            project = save_project(project)
            saved = next(
                saved_item
                for saved_item in project.get("automation_learned_parameters", [])
                if saved_item["id"] == parameter_id
            )
            return project, deepcopy(saved)
    raise ValueError(f"learned parameter {parameter_id} not found")


def midi_write(
    track_id: int,
    notes: list[dict[str, Any]],
    *,
    start: float | None = None,
    end: float | None = None,
    mode: str = "replace",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Overwrite or append MIDI notes on a track.

    Times are stored in beats to match the Rust sequencer.
    """
    if mode not in {"replace", "append"}:
        raise ValueError("mode must be 'replace' or 'append'")

    project = load_project()
    track = find_track(project, track_id)
    clip = _ensure_midi_clip(track)
    normalized_notes = [_normalize_note(note) for note in notes]

    if start is None:
        start = min((note["start"] for note in normalized_notes), default=0.0)
    if end is None:
        end = max((note["start"] + note["duration"] for note in normalized_notes), default=start)
    start = max(0.0, float(start))
    end = max(start, float(end))

    removed = 0
    if mode == "replace":
        kept = []
        for note in clip["notes"]:
            overlaps = note["start"] < end and (note["start"] + note["duration"]) > start
            if overlaps:
                removed += 1
            else:
                kept.append(note)
        clip["notes"] = kept

    clip["notes"].extend(normalized_notes)
    clip["notes"].sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
    _update_midi_clip_duration(clip)
    project = save_project(project)
    synced_track = find_track(project, track_id)
    summary = {
        "track_id": track["id"],
        "requested_track_id": track_id,
        "host_track_id": track.get("host_track_id"),
        "mode": mode,
        "range": [start, end],
        "notes_added": len(normalized_notes),
        "notes_removed": removed,
        "track_note_count": len(synced_track["notes"]),
    }
    return project, summary


def midi_diff(
    track_id: int,
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_project()
    track = find_track(project, track_id)
    changed: dict[str, Any] = {
        "added": 0,
        "deleted": 0,
        "updated": 0,
        "events_added": 0,
        "events_deleted": 0,
        "events_updated": 0,
        "curves_written": 0,
    }

    for op in operations:
        op_type = str(op.get("op") or op.get("type") or "").strip().lower()
        if op_type == "add_note":
            raw_note = op.get("note")
            note_data = cast(dict[str, Any], raw_note) if isinstance(raw_note, dict) else op
            if isinstance(raw_note, dict) and "clip_id" in op and "clip_id" not in note_data:
                note_data = {**note_data, "clip_id": op["clip_id"]}
            clip = _target_clip_for_timeline_write(track, note_data, create=True)
            clip["notes"].append(_normalize_note(_note_payload_to_clip_local(note_data, clip)))
            changed["added"] += 1
        elif op_type == "delete_note":
            changed["deleted"] += _delete_timeline_notes(track, op)
        elif op_type in {"update_note", "modify_note"}:
            note_ref = _find_timeline_note(track, op)
            if note_ref is None:
                continue
            clip = note_ref["clip"]
            note = note_ref["note"]
            payload = dict(note)
            for key in ("pitch", "duration", "velocity"):
                if key in op:
                    payload[key] = op[key]
            if _payload_has_start(op):
                payload["start"] = _payload_start_to_clip_local(op, clip)
            note.clear()
            note.update(_normalize_note(payload))
            changed["updated"] += 1
        elif op_type in {"add_event", "add_midi_event"}:
            payload = _event_payload_from_op(op)
            clip = _target_clip_for_timeline_write(track, payload, create=True)
            clip["events"].append(
                _normalize_midi_event(_event_payload_to_clip_local(payload, clip))
            )
            changed["events_added"] += 1
        elif op_type in {"delete_event", "delete_midi_event"}:
            changed["events_deleted"] += _delete_timeline_events(track, op)
        elif op_type in {"update_event", "modify_event", "update_midi_event", "modify_midi_event"}:
            event_ref = _find_timeline_event(track, op)
            if event_ref is None:
                continue
            clip = event_ref["clip"]
            event = event_ref["event"]
            payload = _event_payload_from_op(op, include_identity=False)
            if "new_id" in op:
                payload["id"] = op["new_id"]
            if _payload_has_start(payload):
                payload = _event_payload_to_clip_local(payload, clip)
            updated_event = _normalize_midi_event({**event, **payload})
            event.clear()
            event.update(updated_event)
            changed["events_updated"] += 1
        elif op_type in {
            "draw_event_curve",
            "set_event_curve",
            "replace_event_curve",
            "draw_controller_curve",
            "set_controller_curve",
            "cc_curve",
            "pitch_bend_curve",
            "aftertouch_curve",
            "channel_pressure_curve",
        }:
            clip = _target_clip_for_timeline_write(track, op, create=True)
            added, deleted = _apply_midi_event_curve(
                clip, _curve_op_to_clip_local(op, clip), op_type
            )
            changed["events_added"] += added
            changed["events_deleted"] += deleted
            changed["curves_written"] += 1
        elif op_type in {"velocity_curve", "draw_velocity_curve", "set_velocity_curve"}:
            clip = _target_clip_for_timeline_write(track, op, create=True)
            changed["updated"] += _apply_velocity_curve(clip, _curve_op_to_clip_local(op, clip))
            changed["curves_written"] += 1
        else:
            raise ValueError(f"unsupported MIDI diff operation: {op_type}")

    for clip in _track_midi_clips(track):
        clip["notes"].sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
        clip["events"].sort(key=_midi_event_sort_key)
        _update_midi_clip_duration(clip)
    project = save_project(project)
    synced_track = find_track(project, track_id)
    summary = {
        "track_id": track["id"],
        "requested_track_id": track_id,
        "host_track_id": track.get("host_track_id"),
        "operations": len(operations),
        **changed,
        "track_note_count": len(synced_track["notes"]),
        "track_midi_event_count": len(synced_track["midi_events"]),
    }
    return project, summary


def midi_batch_edit(
    operations: list[dict[str, Any]],
    *,
    track_id: int | None = None,
    selection: dict[str, Any] | None = None,
    all_tracks: bool = False,
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply AI-friendly batch edits to notes and controller lanes.

    This is intentionally higher-level than midi_diff: operations can describe
    musical intent such as velocity shapes, accents, humanization, and CC swells.
    """
    project = load_project()
    _validate_midi_batch_write_scope(selection, track_id=track_id, all_tracks=all_tracks)
    base_selection = _normalize_selection(project, selection, track_id=track_id)
    if all_tracks or bool((selection or {}).get("all_tracks")):
        base_selection["all_tracks"] = True
    if not base_selection.get("all_tracks") and not base_selection.get("track_ids"):
        raise ValueError("midi_batch_edit write scope did not match any project tracks")
    changed: dict[str, Any] = {
        "operations": len(operations),
        "notes_updated": 0,
        "events_added": 0,
        "events_deleted": 0,
        "curves_written": 0,
        "dry_run": bool(dry_run),
        "details": [],
    }

    for raw_op in operations:
        if not isinstance(raw_op, dict):
            continue
        op = dict(raw_op)
        op_type = str(op.get("op") or op.get("type") or "").strip().lower()
        op_selection = _normalize_selection(
            project, op.get("selection"), base=base_selection, op=op
        )

        if op_type in {
            "velocity_set",
            "velocity_scale",
            "velocity_humanize",
            "velocity_accent",
            "velocity_shape",
            "velocity_ramp",
            "velocity_curve",
        }:
            updated = _apply_batch_velocity_operation(project, op_selection, op, op_type)
            changed["notes_updated"] += updated
            changed["details"].append({"op": op_type, "notes_updated": updated})
        elif op_type in {
            "cc_curve",
            "controller_curve",
            "draw_controller_curve",
            "expression_curve",
            "modulation_curve",
            "pitch_bend_curve",
            "aftertouch_curve",
            "channel_pressure_curve",
        }:
            added, deleted = _apply_batch_event_curve_operation(project, op_selection, op, op_type)
            changed["events_added"] += added
            changed["events_deleted"] += deleted
            changed["curves_written"] += 1
            changed["details"].append(
                {
                    "op": op_type,
                    "events_added": added,
                    "events_deleted": deleted,
                }
            )
        elif op_type in {"cc_clear", "controller_clear", "event_clear"}:
            deleted = _apply_batch_event_clear(project, op_selection, op)
            changed["events_deleted"] += deleted
            changed["details"].append({"op": op_type, "events_deleted": deleted})
        else:
            raise ValueError(f"unsupported MIDI batch operation: {op_type}")

    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or clip.get("type") != "midi":
                continue
            clip["notes"].sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
            clip["events"].sort(key=_midi_event_sort_key)
            _update_midi_clip_duration(clip)

    project = normalize_project(project) if dry_run else save_project(project)
    summary = {
        **changed,
        "selection": _selection_summary(base_selection),
        "project": project_summary(project),
    }
    return project, summary


def midi_query(
    *,
    track_id: int | None = None,
    selection: dict[str, Any] | None = None,
    include: list[str] | None = None,
) -> dict[str, Any]:
    """Return a compact project/selection summary for planning MIDI edits."""
    project = load_project()
    normalized_selection = _normalize_selection(project, selection, track_id=track_id)
    include_set = {str(item).lower() for item in (include or [])}
    if not include_set:
        include_set = {"tracks", "clips", "notes", "velocity", "events", "controllers"}

    notes = _selected_note_refs(project, normalized_selection)
    events = _selected_event_refs(project, normalized_selection)
    tracks = _selected_tracks(project, normalized_selection)
    response: dict[str, Any] = {
        "project": project_summary(project),
        "selection": _selection_summary(normalized_selection),
        "selected": {
            "track_count": len(tracks),
            "note_count": len(notes),
            "midi_event_count": len(events),
        },
    }

    if "tracks" in include_set:
        response["tracks"] = [_track_query_summary(track) for track in tracks]
    if "clips" in include_set:
        response["clips"] = [
            _clip_query_summary(track, clip)
            for track, clip in _selected_midi_clips(project, normalized_selection)
        ]
    if "notes" in include_set or "velocity" in include_set:
        response["notes"] = {
            "count": len(notes),
            "pitch": _numeric_stats([ref["note"]["pitch"] for ref in notes]),
            "duration": _numeric_stats([ref["note"]["duration"] for ref in notes]),
            "velocity": _numeric_stats([ref["note"]["velocity"] for ref in notes]),
            "beat_range": _beat_stats([ref["absolute_start"] for ref in notes]),
        }
    if "events" in include_set or "controllers" in include_set:
        response["events"] = {
            "count": len(events),
            "beat_range": _beat_stats([ref["absolute_start"] for ref in events]),
            "lanes": _event_lane_summaries(events),
        }
    return response


def midi_inspect(
    *,
    track_id: int | None = None,
    selection: dict[str, Any] | None = None,
    include: list[str] | None = None,
    limit: int = 120,
    offset: int = 0,
) -> dict[str, Any]:
    """Return detailed selected MIDI notes/events with bounded pagination."""
    project = load_project()
    normalized_selection = _normalize_selection(project, selection, track_id=track_id)
    include_set = {str(item).lower() for item in (include or ["notes", "events"])}
    safe_limit = _bounded_int(limit, 120, 1, 500)
    safe_offset = max(0, int(offset or 0))

    rows: list[dict[str, Any]] = []
    if "notes" in include_set:
        rows.extend(_note_detail(ref) for ref in _selected_note_refs(project, normalized_selection))
    if "events" in include_set or "midi_events" in include_set:
        rows.extend(
            _event_detail(ref) for ref in _selected_event_refs(project, normalized_selection)
        )
    rows.sort(
        key=lambda row: (float(row.get("start", 0.0)), row.get("kind", ""), row.get("id", ""))
    )

    return {
        "selection": _selection_summary(normalized_selection),
        "pagination": {
            "offset": safe_offset,
            "limit": safe_limit,
            "total": len(rows),
            "returned": len(rows[safe_offset : safe_offset + safe_limit]),
        },
        "items": rows[safe_offset : safe_offset + safe_limit],
    }


def find_track(project: dict[str, Any], track_id: int) -> dict[str, Any]:
    requested_id = int(track_id)
    raw_tracks = project.get("tracks", [])
    tracks = raw_tracks if isinstance(raw_tracks, list) else []
    for raw_track in tracks:
        if not isinstance(raw_track, dict):
            continue
        track = cast(dict[str, Any], raw_track)
        if int(track.get("id", -1)) == requested_id:
            return track
    for raw_track in tracks:
        if not isinstance(raw_track, dict):
            continue
        track = cast(dict[str, Any], raw_track)
        host_track_id = track.get("host_track_id")
        if host_track_id is not None and int(host_track_id) == requested_id:
            return track
    raise ValueError(f"track {track_id} not found")


def project_summary(project: dict[str, Any]) -> dict[str, Any]:
    project = normalize_project(project)
    note_count = sum(len(track["notes"]) for track in project["tracks"])
    midi_event_count = sum(len(track["midi_events"]) for track in project["tracks"])
    audio_clip_count = sum(
        1
        for track in project["tracks"]
        for clip in track.get("clips", [])
        if isinstance(clip, dict) and clip.get("type") == "audio"
    )
    return {
        "title": project["title"],
        "tempo": project["tempo"],
        "time_signature": project["time_signature"],
        "length_beats": project["length_beats"],
        "track_count": len(project["tracks"]),
        "note_count": note_count,
        "midi_event_count": midi_event_count,
        "audio_clip_count": audio_clip_count,
        "tracks": [
            {
                "id": track["id"],
                "name": track["name"],
                "type": track["type"],
                "channel_type": track["channel_type"],
                "notes": len(track["notes"]),
                "midi_events": len(track["midi_events"]),
                "clips": len(track.get("clips", [])),
                "audio_clips": sum(
                    1
                    for clip in track.get("clips", [])
                    if isinstance(clip, dict) and clip.get("type") == "audio"
                ),
                "instrument": track["instrument"],
                "plugin_slots": track.get("plugin_slots", []),
            }
            for track in project["tracks"]
        ],
    }


def _ensure_midi_clip(track: dict[str, Any]) -> dict[str, Any]:
    raw_clips = track.get("clips")
    if not isinstance(raw_clips, list):
        raw_clips = []
        track["clips"] = raw_clips
    clips = cast(list[Any], raw_clips)
    for raw_clip in clips:
        if not isinstance(raw_clip, dict):
            continue
        existing_clip = cast(dict[str, Any], raw_clip)
        if existing_clip.get("type") == "midi":
            return existing_clip
    new_clip: dict[str, Any] = {
        "id": f"clip_{uuid4().hex[:10]}",
        "type": "midi",
        "name": "MIDI Clip",
        "start": 0.0,
        "duration": 4.0,
        "color": track.get("color") or DEFAULT_TRACK_COLORS[0],
        "notes": [],
        "events": [],
    }
    clips.append(new_clip)
    return new_clip


def _normalize_clips(
    track: dict[str, Any],
    *,
    legacy_notes: list[dict[str, Any]],
    track_color: str,
) -> list[dict[str, Any]]:
    raw_clips = track.get("clips")
    if isinstance(raw_clips, list):
        clips = [
            _normalize_clip(clip, track_color=track_color)
            for clip in raw_clips
            if isinstance(clip, dict)
        ]
        clips.sort(key=lambda clip: (clip["start"], clip["type"], clip["name"]))
        return clips

    if not legacy_notes:
        return []

    clip_start = min(note["start"] for note in legacy_notes)
    clip_end = max(note["start"] + note["duration"] for note in legacy_notes)
    local_notes = [
        {
            **note,
            "start": max(0.0, note["start"] - clip_start),
        }
        for note in legacy_notes
    ]
    return [
        _normalize_clip(
            {
                "type": "midi",
                "name": str(track.get("name") or "MIDI Clip"),
                "start": clip_start,
                "duration": max(0.25, clip_end - clip_start),
                "color": track_color,
                "notes": local_notes,
            },
            track_color=track_color,
        )
    ]


def _normalize_clip(clip: dict[str, Any], *, track_color: str) -> dict[str, Any]:
    clip_type = str(clip.get("type") or "midi").lower()
    if clip_type not in {"midi", "audio"}:
        clip_type = "midi"

    notes = [
        _normalize_note(note)
        for note in clip.get("notes", [])
        if clip_type == "midi" and isinstance(note, dict)
    ]
    notes.sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
    events = [
        _normalize_midi_event(event)
        for event in clip.get("events", [])
        if clip_type == "midi" and isinstance(event, dict)
    ]
    events.sort(key=_midi_event_sort_key)
    note_end = max((note["start"] + note["duration"] for note in notes), default=0.0)
    event_end = max((event["start"] for event in events), default=0.0)
    duration = max(_positive_float(clip.get("duration"), 4.0), note_end, event_end, 0.25)
    default_name = "MIDI Clip" if clip_type == "midi" else "Audio Clip"

    return {
        "id": str(clip.get("id") or f"clip_{uuid4().hex[:10]}"),
        "type": clip_type,
        "name": str(clip.get("name") or default_name),
        "start": _non_negative_float(clip.get("start"), 0.0),
        "duration": duration,
        "duration_seconds": _non_negative_float(clip.get("duration_seconds"), 0.0),
        "color": _track_color(clip.get("color") or track_color, 0),
        "source": str(clip.get("source") or ""),
        "path": str(clip.get("path") or ""),
        "source_offset": _non_negative_float(
            _first_present(clip, ("source_offset", "offset"), default=0.0),
            0.0,
        ),
        "gain": _bounded_float(clip.get("gain"), 1.0, 0.0, 4.0),
        "waveform": _normalize_waveform(clip.get("waveform")) if clip_type == "audio" else [],
        "notes": notes,
        "events": events,
    }


def _flatten_clip_notes(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notes = []
    for clip in clips:
        if clip.get("type") != "midi":
            continue
        clip_start = float(clip.get("start", 0.0) or 0.0)
        for note in clip.get("notes", []):
            notes.append(
                {
                    **note,
                    "start": clip_start + float(note.get("start", 0.0) or 0.0),
                }
            )
    notes.sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
    return notes


def _flatten_clip_midi_events(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for clip in clips:
        if clip.get("type") != "midi":
            continue
        clip_start = float(clip.get("start", 0.0) or 0.0)
        for event in clip.get("events", []):
            events.append(
                {
                    **event,
                    "start": clip_start + float(event.get("start", 0.0) or 0.0),
                }
            )
    events.sort(key=_midi_event_sort_key)
    return events


def _new_automation_track(
    track_id: int,
    *,
    target: dict[str, Any],
    automation: dict[str, Any],
    name: str = "",
    color: str | None = None,
) -> dict[str, Any]:
    return {
        "id": track_id,
        "host_track_id": None,
        "type": "automation",
        "channel_type": "multichannel",
        "name": str(name or target.get("label") or f"Automation {track_id}").strip()
        or f"Automation {track_id}",
        "color": _track_color(color, track_id - 1),
        "volume": 0.8,
        "pan": 0.0,
        "mute": False,
        "solo": False,
        "instrument": "Automation",
        "plugin_slots": [],
        "target": target,
        "automation": automation,
        "clips": [],
        "notes": [],
        "midi_events": [],
    }


def _normalize_automation_target(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    kind = str(raw.get("kind") or raw.get("type") or "track_volume").strip().lower()
    if kind not in {
        *TRACK_AUTOMATION_TARGET_KINDS,
        *GLOBAL_AUTOMATION_TARGET_KINDS,
        "unassigned",
    }:
        kind = "track_volume"
    target: dict[str, Any] = {"kind": kind}
    if kind in TRACK_AUTOMATION_TARGET_KINDS:
        target["track_id"] = _positive_int(raw.get("track_id"), 1)
    if kind == "plugin_parameter":
        target["slot_id"] = str(raw.get("slot_id") or "instrument").strip() or "instrument"
        target["param_index"] = _bounded_int(raw.get("param_index"), 0, 0, 2**31 - 1)
        if raw.get("param_id") not in (None, ""):
            target["param_id"] = _bounded_int(raw.get("param_id"), 0, 0, 2**31 - 1)
    label = str(raw.get("label") or raw.get("name") or _automation_target_default_label(target))
    if label:
        target["label"] = label
    return target


def _automation_target_default_label(target: dict[str, Any]) -> str:
    kind = target.get("kind")
    if kind == "unassigned":
        return "Unassigned"
    if kind == "tempo_bpm":
        return "Tempo BPM"
    if kind == "time_signature_numerator":
        return "Time Signature Numerator"
    if kind == "track_pan":
        return "Pan"
    if kind == "plugin_parameter":
        return f"Parameter {target.get('param_index', 0)}"
    return "Volume"


def _automation_bounds_for_target(target: dict[str, Any]) -> tuple[float, float, float]:
    kind = target.get("kind")
    if kind == "tempo_bpm":
        return (1.0, 999.0, 120.0)
    if kind == "time_signature_numerator":
        return (1.0, float(MAX_METER_NUMERATOR), 4.0)
    if kind == "track_pan":
        return (-1.0, 1.0, 0.0)
    if kind == "track_volume":
        return (0.0, 2.0, 0.8)
    return (0.0, 1.0, 0.0)


def _normalize_automation_payload(value: Any, *, target: dict[str, Any]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    minimum, maximum, default = _automation_bounds_for_target(target)
    value_min = _bounded_float(raw.get("value_min"), minimum, minimum, maximum)
    value_max = _bounded_float(raw.get("value_max"), maximum, minimum, maximum)
    if value_max < value_min:
        value_min, value_max = value_max, value_min
    default_value = _bounded_float(raw.get("default_value"), default, value_min, value_max)
    payload = {
        "value_min": value_min,
        "value_max": value_max,
        "default_value": default_value,
        "points": _normalize_automation_points(raw.get("points") or [], target=target),
    }
    return payload


def _normalize_automation_points(value: Any, *, target: dict[str, Any]) -> list[dict[str, Any]]:
    raw_points = value if isinstance(value, list) else []
    points: list[dict[str, Any]] = []
    for raw_point in raw_points:
        if isinstance(raw_point, dict):
            points.append(_normalize_automation_point(raw_point, target=target))
    by_beat: dict[float, dict[str, Any]] = {}
    for point in points:
        by_beat[round(float(point["beat"]), 6)] = point
    return [by_beat[beat] for beat in sorted(by_beat)]


def _normalize_automation_point(value: dict[str, Any], *, target: dict[str, Any]) -> dict[str, Any]:
    minimum, maximum, default = _automation_bounds_for_target(target)
    beat = _non_negative_float(_first_present(value, ("beat", "start"), default=0.0), 0.0)
    point_id = str(value.get("id") or f"pt_{uuid4().hex[:10]}")
    curve = str(value.get("curve") or "linear").strip().lower()
    if curve not in {"linear", "hold"}:
        curve = "linear"
    point_value = _bounded_float(value.get("value"), default, minimum, maximum)
    if target.get("kind") == "time_signature_numerator":
        point_value = float(round(point_value))
    return {
        "id": point_id,
        "beat": round(beat, 6),
        "value": point_value,
        "curve": curve,
    }


def _upsert_automation_point(
    points: list[dict[str, Any]],
    point: dict[str, Any],
) -> list[dict[str, Any]]:
    kept = [item for item in points if abs(float(item["beat"]) - float(point["beat"])) > 1e-6]
    return sorted([*kept, point], key=lambda item: float(item["beat"]))


def _update_automation_point(
    points: list[dict[str, Any]],
    point: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    updated = 0
    next_points = []
    for existing in points:
        same_id = point.get("id") and str(existing.get("id")) == str(point.get("id"))
        same_beat = abs(float(existing["beat"]) - float(point["beat"])) <= 1e-6
        if same_id or same_beat:
            next_points.append({**existing, **point, "id": existing.get("id") or point["id"]})
            updated += 1
        else:
            next_points.append(existing)
    if not updated:
        next_points.append(point)
    return (
        sorted(next_points, key=lambda item: float(item["beat"])),
        updated if updated else 1,
    )


def _delete_automation_point(
    points: list[dict[str, Any]],
    op: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    before = len(points)
    point_id = str(op.get("id") or op.get("point_id") or "")
    has_beat = "beat" in op or "start" in op
    beat = _non_negative_float(_first_present(op, ("beat", "start"), default=0.0), 0.0)
    kept = []
    for point in points:
        if point_id and str(point.get("id")) == point_id:
            continue
        if has_beat and abs(float(point["beat"]) - beat) <= 1e-6:
            continue
        kept.append(point)
    return kept, before - len(kept)


def _automation_target_status(project: dict[str, Any], target: dict[str, Any]) -> str:
    if target.get("kind") == "unassigned":
        return "unassigned"
    if target.get("kind") in GLOBAL_AUTOMATION_TARGET_KINDS:
        return "valid"
    try:
        target_track = find_track(project, int(target.get("track_id", -1)))
    except (TypeError, ValueError):
        return "missing"
    kind = target.get("kind")
    if kind in {"track_volume", "track_pan"}:
        return "valid"
    if kind == "plugin_parameter":
        slot_id = str(target.get("slot_id") or "instrument")
        slots = target_track.get("plugin_slots") if isinstance(target_track, dict) else []
        if not isinstance(slots, list):
            slots = []
        if slot_id == "instrument" and not slots:
            return "unvalidated"
        slot = next(
            (item for item in slots if isinstance(item, dict) and item.get("id") == slot_id),
            None,
        )
        if not slot or slot.get("type") == "empty":
            return "missing"
        return "unvalidated"
    return "missing"


def _automation_track_summary(
    project: dict[str, Any],
    track: dict[str, Any],
    *,
    include_points: bool,
) -> dict[str, Any]:
    points = track.get("automation", {}).get("points", [])
    beats = [float(point.get("beat", 0.0)) for point in points if isinstance(point, dict)]
    values = [float(point.get("value", 0.0)) for point in points if isinstance(point, dict)]
    row = {
        "id": track["id"],
        "name": track["name"],
        "color": track["color"],
        "mute": track["mute"],
        "target": track.get("target"),
        "target_status": _automation_target_status(project, track.get("target") or {}),
        "point_count": len(points),
        "beat_range": [min(beats), max(beats)] if beats else None,
        "value_range": [min(values), max(values)] if values else None,
    }
    if include_points:
        row["points"] = points
    return row


def _normalize_learned_parameters(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    learned: dict[str, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        try:
            item = _normalize_learned_parameter(raw)
        except ValueError:
            continue
        learned[item["id"]] = item
    return [learned[key] for key in sorted(learned)]


def _normalize_learned_parameter(value: dict[str, Any]) -> dict[str, Any]:
    target = _normalize_automation_target(value.get("target"))
    if target.get("kind") != "plugin_parameter":
        raise ValueError("learned automation parameter target must be a plugin parameter")
    raw_source = value.get("source")
    source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
    now = _now_iso()
    item_id = str(value.get("id") or _learned_parameter_id(target)).strip()
    name = str(value.get("name") or _learned_parameter_default_name(target, source)).strip()
    return {
        "id": item_id,
        "name": name or _learned_parameter_default_name(target, source),
        "target": target,
        "source": {
            "track_name": str(source.get("track_name") or ""),
            "slot_id": str(source.get("slot_id") or target.get("slot_id") or "instrument"),
            "slot_label": str(source.get("slot_label") or _slot_label(target.get("slot_id"))),
            "plugin_name": str(source.get("plugin_name") or ""),
            "param_name": str(source.get("param_name") or target.get("label") or ""),
            "units": str(source.get("units") or ""),
        },
        "last_value": _bounded_float(value.get("value", value.get("last_value")), 0.0, 0.0, 1.0),
        "created_at": str(value.get("created_at") or now),
        "last_captured_at": str(value.get("last_captured_at") or now),
    }


def _learned_parameter_id(target: dict[str, Any]) -> str:
    slot_id = str(target.get("slot_id") or "instrument")
    param_key = target.get("param_id", target.get("param_index", 0))
    raw = f"{target.get('track_id')}:{slot_id}:{param_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"learned_plugin_parameter_{digest}"


def _learned_parameter_default_name(target: dict[str, Any], source: dict[str, Any]) -> str:
    parts = [
        str(source.get("track_name") or f"Track {target.get('track_id')}").strip(),
        str(source.get("slot_label") or _slot_label(target.get("slot_id"))).strip(),
        str(source.get("plugin_name") or "Plugin").strip(),
        str(source.get("param_name") or target.get("label") or "Parameter").strip(),
    ]
    return " / ".join(part for part in parts if part)


def _slot_label(slot_id: Any) -> str:
    slot = str(slot_id or "instrument")
    if slot == "instrument":
        return "Instrument"
    if slot.startswith("insert_"):
        suffix = slot.removeprefix("insert_")
        return f"Insert {suffix}"
    return slot


def _normalize_selection(
    project: dict[str, Any],
    selection: Any = None,
    *,
    track_id: int | None = None,
    base: dict[str, Any] | None = None,
    op: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = dict(base or {})
    if isinstance(selection, dict):
        raw.update({key: value for key, value in selection.items() if value is not None})
    if track_id is not None:
        raw["track_ids"] = [track_id]
    if op:
        for key in (
            "track_id",
            "track_ids",
            "clip_id",
            "clip_ids",
            "note_ids",
            "event_ids",
            "pitch_range",
            "controllers",
            "event_types",
            "channel",
        ):
            if key in op:
                raw[key] = op[key]
        if "range" in op:
            raw["range"] = op["range"]
        elif "start" in op or "end" in op:
            start, end = _selection_range(raw) or (0.0, project.get("length_beats", 0.0))
            raw["range"] = [
                _non_negative_float(op.get("start"), start),
                _non_negative_float(op.get("end"), end),
            ]

    has_track_filter = any(key in raw for key in ("track_id", "track_ids", "tracks"))
    track_ids = _as_int_list(raw.get("track_ids", raw.get("tracks")))
    if "track_id" in raw:
        track_ids.append(_bounded_int(raw.get("track_id"), 1, 0, 2**31 - 1))
    if has_track_filter:
        raw["track_ids"] = _resolve_selection_track_ids(project, track_ids)

    clip_ids = _as_str_list(raw.get("clip_ids", raw.get("clips")))
    if "clip_id" in raw:
        clip_ids.append(str(raw["clip_id"]))
    if clip_ids:
        raw["clip_ids"] = sorted(set(clip_ids))

    note_ids = _as_str_list(raw.get("note_ids", raw.get("notes")))
    if "note_id" in raw:
        note_ids.append(str(raw["note_id"]))
    if note_ids:
        raw["note_ids"] = sorted(set(note_ids))

    event_ids = _as_str_list(raw.get("event_ids", raw.get("events")))
    if "event_id" in raw:
        event_ids.append(str(raw["event_id"]))
    if event_ids:
        raw["event_ids"] = sorted(set(event_ids))

    controllers = _as_int_list(raw.get("controllers"))
    raw = _normalize_event_aliases(raw)
    if "controller" in raw:
        controllers.append(_bounded_int(raw["controller"], 0, 0, 127))
    if controllers:
        raw["controllers"] = sorted(set(_bounded_int(value, 0, 0, 127) for value in controllers))

    event_types = [
        _normalize_midi_event_type(value) for value in _as_str_list(raw.get("event_types"))
    ]
    if "event_type" in raw:
        event_types.append(_normalize_midi_event_type(raw["event_type"]))
    if event_types:
        raw["event_types"] = sorted(set(event_types))

    if "range" in raw:
        start, end = _selection_range(raw) or (0.0, 0.0)
        raw["range"] = [start, max(start, end)]
    if "pitch_range" in raw:
        raw["pitch_range"] = _int_range(raw["pitch_range"], 0, 127)
    if "channel" in raw:
        raw["channel"] = _bounded_int(raw["channel"], 0, 0, 15)
    if bool(raw.get("all_tracks")):
        raw["all_tracks"] = True
    return raw


def _validate_midi_batch_write_scope(
    selection: dict[str, Any] | None,
    *,
    track_id: int | None,
    all_tracks: bool,
) -> None:
    """Require explicit write scope so batch edits cannot silently hit every track."""
    raw_selection = selection if isinstance(selection, dict) else {}
    has_track_scope = track_id is not None or bool(
        raw_selection.get("track_ids") or raw_selection.get("track_id")
    )
    has_all_tracks_scope = bool(all_tracks or raw_selection.get("all_tracks"))
    if not has_track_scope and not has_all_tracks_scope:
        raise ValueError(
            "midi_batch_edit requires an explicit write scope: provide track_id, "
            "selection.track_ids, or all_tracks=true"
        )


def _selection_summary(selection: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "all_tracks",
        "track_ids",
        "clip_ids",
        "range",
        "pitch_range",
        "note_ids",
        "event_ids",
        "controllers",
        "event_types",
        "channel",
    ):
        if key in selection:
            summary[key] = selection[key]
    return summary


def _resolve_selection_track_ids(project: dict[str, Any], track_ids: list[int]) -> list[int]:
    resolved = []
    for requested_track_id in track_ids:
        try:
            resolved.append(int(find_track(project, requested_track_id)["id"]))
        except ValueError:
            continue
    return sorted(set(resolved))


def _selected_tracks(project: dict[str, Any], selection: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tracks = project.get("tracks", [])
    tracks = [track for track in raw_tracks if isinstance(track, dict)]
    if "track_ids" not in selection:
        return tracks
    track_ids = set(_as_int_list(selection.get("track_ids")))
    return [track for track in tracks if int(track.get("id", -1)) in track_ids]


def _selected_midi_clips(
    project: dict[str, Any],
    selection: dict[str, Any],
    *,
    create: bool = False,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    selected = []
    clip_ids = set(_as_str_list(selection.get("clip_ids")))
    beat_range = _selection_range(selection)
    for track in _selected_tracks(project, selection):
        clips = [
            clip
            for clip in track.get("clips", [])
            if isinstance(clip, dict) and clip.get("type") == "midi"
        ]
        if create and not clips and not clip_ids:
            clips = [_ensure_midi_clip(track)]
        for clip in clips:
            if clip_ids and str(clip.get("id")) not in clip_ids:
                continue
            if beat_range and not _clip_overlaps_range(clip, beat_range):
                continue
            selected.append((track, clip))

    if create and not selected and not clip_ids:
        for track in _selected_tracks(project, selection):
            selected.append((track, _ensure_midi_clip(track)))
    return selected


def _selected_note_refs(project: dict[str, Any], selection: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    beat_range = _selection_range(selection)
    note_ids = set(_as_str_list(selection.get("note_ids")))
    pitch_range = selection.get("pitch_range")
    for track, clip in _selected_midi_clips(project, selection):
        clip_start = float(clip.get("start", 0.0) or 0.0)
        for note in clip.get("notes", []):
            if not isinstance(note, dict):
                continue
            absolute_start = clip_start + float(note.get("start", 0.0) or 0.0)
            absolute_end = absolute_start + float(note.get("duration", 0.0) or 0.0)
            if note_ids and str(note.get("id")) not in note_ids:
                continue
            if pitch_range and not (
                int(pitch_range[0]) <= int(note["pitch"]) <= int(pitch_range[1])
            ):
                continue
            if beat_range and not (beat_range[0] - 1e-6 <= absolute_start <= beat_range[1] + 1e-6):
                continue
            refs.append(
                {
                    "track": track,
                    "clip": clip,
                    "note": note,
                    "absolute_start": absolute_start,
                    "absolute_end": absolute_end,
                }
            )
    return sorted(
        refs, key=lambda ref: (ref["absolute_start"], ref["note"]["pitch"], ref["note"]["id"])
    )


def _selected_event_refs(
    project: dict[str, Any], selection: dict[str, Any]
) -> list[dict[str, Any]]:
    refs = []
    beat_range = _selection_range(selection)
    event_ids = set(_as_str_list(selection.get("event_ids")))
    event_types = set(_as_str_list(selection.get("event_types")))
    controllers = set(_as_int_list(selection.get("controllers")))
    channel = selection.get("channel")
    for track, clip in _selected_midi_clips(project, selection):
        clip_start = float(clip.get("start", 0.0) or 0.0)
        for event in clip.get("events", []):
            if not isinstance(event, dict):
                continue
            absolute_start = clip_start + float(event.get("start", 0.0) or 0.0)
            event_type = str(event.get("type") or "")
            if event_ids and str(event.get("id")) not in event_ids:
                continue
            if event_types and event_type not in event_types:
                continue
            if controllers and int(event.get("controller", -1)) not in controllers:
                continue
            if channel is not None and int(event.get("channel", -1)) != int(channel):
                continue
            if beat_range and not (beat_range[0] - 1e-6 <= absolute_start <= beat_range[1] + 1e-6):
                continue
            refs.append(
                {
                    "track": track,
                    "clip": clip,
                    "event": event,
                    "absolute_start": absolute_start,
                }
            )
    return sorted(
        refs, key=lambda ref: _midi_event_sort_key({**ref["event"], "start": ref["absolute_start"]})
    )


def _track_query_summary(track: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": track["id"],
        "host_track_id": track.get("host_track_id"),
        "name": track["name"],
        "instrument": track["instrument"],
        "clips": len(track.get("clips", [])),
        "notes": len(track.get("notes", [])),
        "midi_events": len(track.get("midi_events", [])),
        "volume": track.get("volume"),
        "pan": track.get("pan"),
        "mute": track.get("mute"),
        "solo": track.get("solo"),
    }


def _clip_query_summary(track: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    notes = [note for note in clip.get("notes", []) if isinstance(note, dict)]
    events = [event for event in clip.get("events", []) if isinstance(event, dict)]
    return {
        "track_id": track["id"],
        "track_name": track["name"],
        "id": clip["id"],
        "name": clip["name"],
        "start": clip["start"],
        "duration": clip["duration"],
        "notes": len(notes),
        "midi_events": len(events),
        "velocity": _numeric_stats([note["velocity"] for note in notes]),
        "lanes": _event_lane_summaries(
            [{"event": event, "absolute_start": clip["start"] + event["start"]} for event in events]
        ),
    }


def _note_detail(ref: dict[str, Any]) -> dict[str, Any]:
    note = ref["note"]
    track = ref["track"]
    clip = ref["clip"]
    return {
        "kind": "note",
        "track_id": track["id"],
        "track_name": track["name"],
        "clip_id": clip["id"],
        "clip_name": clip["name"],
        "id": note["id"],
        "pitch": note["pitch"],
        "start": round(float(ref["absolute_start"]), 6),
        "local_start": note["start"],
        "duration": note["duration"],
        "end": round(float(ref["absolute_end"]), 6),
        "velocity": note["velocity"],
    }


def _event_detail(ref: dict[str, Any]) -> dict[str, Any]:
    event = ref["event"]
    track = ref["track"]
    clip = ref["clip"]
    payload = {
        key: event[key]
        for key in (
            "channel",
            "pitch",
            "velocity",
            "controller",
            "value",
            "program",
            "pressure",
            "data_b64",
        )
        if key in event
    }
    return {
        "kind": "event",
        "track_id": track["id"],
        "track_name": track["name"],
        "clip_id": clip["id"],
        "clip_name": clip["name"],
        "id": event["id"],
        "type": event["type"],
        "start": round(float(ref["absolute_start"]), 6),
        "local_start": event["start"],
        **payload,
    }


def _apply_batch_velocity_operation(
    project: dict[str, Any],
    selection: dict[str, Any],
    op: dict[str, Any],
    op_type: str,
) -> int:
    refs = _selected_note_refs(project, selection)
    if not refs:
        return 0

    updated = 0
    if op_type == "velocity_set":
        value = _bounded_int(_first_present(op, ("velocity", "value"), default=96), 96, 1, 127)
        for ref in refs:
            updated += _set_note_velocity(ref["note"], value)
        return updated

    if op_type == "velocity_scale":
        factor = _float_value(_first_present(op, ("factor", "scale"), default=1.0), 1.0)
        offset = _float_value(_first_present(op, ("offset", "add"), default=0.0), 0.0)
        for ref in refs:
            value = round(float(ref["note"]["velocity"]) * factor + offset)
            updated += _set_note_velocity(ref["note"], _bounded_int(value, 96, 1, 127))
        return updated

    if op_type == "velocity_humanize":
        amount = _bounded_int(op.get("amount"), 6, 0, 64)
        seed = str(op.get("seed") or "atri")
        for ref in refs:
            delta = _stable_signed_amount(
                f"{ref['note']['id']}:{ref['absolute_start']}:{ref['note']['pitch']}:{seed}",
                amount,
            )
            value = int(ref["note"]["velocity"]) + delta
            updated += _set_note_velocity(ref["note"], _bounded_int(value, 96, 1, 127))
        return updated

    if op_type == "velocity_accent":
        amount = _bounded_int(op.get("amount"), 12, -64, 64)
        for ref in refs:
            if _accent_matches(float(ref["absolute_start"]), op):
                value = int(ref["note"]["velocity"]) + amount
                updated += _set_note_velocity(ref["note"], _bounded_int(value, 96, 1, 127))
        return updated

    value_range = _operation_beat_range(selection, refs)
    if op.get("points") or op.get("curve"):
        points = _curve_points(op, "velocity", 1, 127, 96)
        for ref in refs:
            beat = float(ref["absolute_start"])
            if value_range and not (value_range[0] - 1e-6 <= beat <= value_range[1] + 1e-6):
                continue
            updated += _set_note_velocity(ref["note"], _interpolate_curve_value(points, beat))
        return updated

    start, end = value_range
    for ref in refs:
        beat = float(ref["absolute_start"])
        unit = _range_unit(beat, start, end)
        value = _shape_value(op, unit, 1, 127, default_min=55, default_max=105)
        updated += _set_note_velocity(ref["note"], value)
    return updated


def _apply_batch_event_curve_operation(
    project: dict[str, Any],
    selection: dict[str, Any],
    op: dict[str, Any],
    op_type: str,
) -> tuple[int, int]:
    local_op_type, event_op = _batch_event_curve_op(op, op_type)
    target = _curve_event_target(event_op, local_op_type)
    value_field = _curve_value_field(str(target["type"]))
    minimum, maximum, default = _curve_value_bounds(str(target["type"]))
    clips = _selected_midi_clips(project, selection, create=True)
    if not clips:
        return (0, 0)

    absolute_range = _selection_range(selection)
    explicit_points = _batch_explicit_curve_points(
        event_op,
        value_field,
        minimum,
        maximum,
        default,
    )
    explicit_range = _explicit_points_range(explicit_points)
    split_across_arrangement_clips = len(clips) > 1 and not selection.get("clip_ids")
    added = 0
    deleted = 0
    for _track, clip in clips:
        clip_start = float(clip.get("start", 0.0) or 0.0)
        clip_end = clip_start + float(clip.get("duration", 0.0) or 0.0)
        if absolute_range:
            abs_start = max(absolute_range[0], clip_start)
            if split_across_arrangement_clips:
                abs_end = min(absolute_range[1], clip_end)
            else:
                abs_end = absolute_range[1]
        elif explicit_range:
            abs_start = max(explicit_range[0], clip_start)
            if split_across_arrangement_clips:
                abs_end = min(explicit_range[1], clip_end)
            else:
                abs_end = explicit_range[1]
        else:
            abs_start = clip_start
            abs_end = clip_end
        if abs_end < abs_start:
            continue

        points = _batch_curve_points_for_range(
            event_op,
            value_field,
            minimum,
            maximum,
            default,
            explicit_points=explicit_points,
            source_start=absolute_range[0] if absolute_range else abs_start,
            source_end=absolute_range[1] if absolute_range else abs_end,
            target_start=abs_start,
            target_end=abs_end,
        )
        if not points:
            continue
        local_op = {
            **event_op,
            "points": [[round(beat - clip_start, 6), value] for beat, value in points],
            "start": round(abs_start - clip_start, 6),
            "end": round(abs_end - clip_start, 6),
            "resolution": 0,
        }
        local_added, local_deleted = _apply_midi_event_curve(clip, local_op, local_op_type)
        added += local_added
        deleted += local_deleted
    return (added, deleted)


def _apply_batch_event_clear(
    project: dict[str, Any],
    selection: dict[str, Any],
    op: dict[str, Any],
) -> int:
    refs = _selected_event_refs(project, selection)
    if not refs:
        return 0
    target = None
    if any(key in op for key in ("event_type", "type", "controller", "cc", "channel", "pitch")):
        target_op = dict(op)
        if (
            "type" not in target_op
            and "event_type" not in target_op
            and ("controller" in target_op or "cc" in target_op)
        ):
            target_op["type"] = "control_change"
        target = _curve_event_target(target_op, "draw_event_curve")

    ids_by_clip: dict[str, set[str]] = {}
    deleted = 0
    for ref in refs:
        event = ref["event"]
        if target and not _event_matches_curve_target(event, target):
            continue
        clip = ref["clip"]
        clip_id = str(clip["id"])
        ids_by_clip.setdefault(clip_id, set()).add(str(event["id"]))
    for _track, clip in _selected_midi_clips(project, selection):
        event_ids = ids_by_clip.get(str(clip["id"]), set())
        if not event_ids:
            continue
        before = len(clip.get("events", []))
        clip["events"] = [
            event for event in clip["events"] if str(event.get("id")) not in event_ids
        ]
        deleted += before - len(clip["events"])
    return deleted


def _batch_event_curve_op(op: dict[str, Any], op_type: str) -> tuple[str, dict[str, Any]]:
    event_op = dict(op)
    if op_type in {"expression_curve"}:
        event_op.setdefault("controller", 11)
        event_op.setdefault("type", "control_change")
        return "cc_curve", event_op
    if op_type in {"modulation_curve"}:
        event_op.setdefault("controller", 1)
        event_op.setdefault("type", "control_change")
        return "cc_curve", event_op
    if op_type in {"cc_curve", "controller_curve", "draw_controller_curve"}:
        event_op.setdefault("type", "control_change")
        return "cc_curve", event_op
    if op_type == "pitch_bend_curve":
        event_op.setdefault("type", "pitch_bend")
        return "pitch_bend_curve", event_op
    if op_type in {"aftertouch_curve", "channel_pressure_curve"}:
        event_op.setdefault("type", "channel_pressure")
        return "aftertouch_curve", event_op
    return "draw_event_curve", event_op


def _batch_curve_points_for_range(
    op: dict[str, Any],
    value_field: str,
    minimum: int,
    maximum: int,
    default: int,
    *,
    explicit_points: list[tuple[float, int]] | None = None,
    source_start: float,
    source_end: float,
    target_start: float,
    target_end: float,
) -> list[tuple[float, int]]:
    resolution = _curve_resolution(op)
    if explicit_points is not None:
        in_range_points = [
            (beat, value)
            for beat, value in explicit_points
            if target_start - 1e-6 <= beat <= target_end + 1e-6
        ]
        if resolution is None:
            return in_range_points
        beats = _curve_sample_beats(target_start, target_end, resolution)
        return [(beat, _interpolate_curve_value(explicit_points, beat)) for beat in beats]
    beats = _curve_sample_beats(target_start, target_end, resolution)
    return [
        (
            beat,
            _shape_value(
                op,
                _range_unit(beat, source_start, source_end),
                minimum,
                maximum,
                default_min=minimum,
                default_max=maximum,
            ),
        )
        for beat in beats
    ]


def _batch_explicit_curve_points(
    op: dict[str, Any],
    value_field: str,
    minimum: int,
    maximum: int,
    default: int,
) -> list[tuple[float, int]] | None:
    if not (op.get("points") or op.get("curve")):
        return None
    return _curve_points(op, value_field, minimum, maximum, default)


def _explicit_points_range(points: list[tuple[float, int]] | None) -> tuple[float, float] | None:
    if not points:
        return None
    return (points[0][0], points[-1][0])


def _curve_sample_beats(start: float, end: float, resolution: float | None) -> list[float]:
    if abs(end - start) <= 1e-9:
        return [round(start, 6)]
    if resolution is None:
        return [round(start, 6), round(end, 6)]
    return _sample_beats_with_limit(start, end, resolution)


def _sample_beats_with_limit(start: float, end: float, resolution: float) -> list[float]:
    if resolution <= 0:
        raise ValueError("MIDI curve resolution must be positive when sampling generated points")
    estimated_points = math.floor((end - start) / resolution) + 2
    if estimated_points > MIDI_CURVE_MAX_POINTS:
        raise ValueError(
            "MIDI curve would generate too many points "
            f"({estimated_points} > {MIDI_CURVE_MAX_POINTS}); "
            "increase resolution or use explicit points"
        )
    beats: list[float] = []
    beat = start
    while beat < end - 1e-6:
        beats.append(round(beat, 6))
        beat += resolution
    beats.append(round(end, 6))
    return list(dict.fromkeys(beats))


def _shape_value(
    op: dict[str, Any],
    unit: float,
    minimum: int,
    maximum: int,
    *,
    default_min: int,
    default_max: int,
) -> int:
    shape = str(op.get("shape") or "linear").strip().lower()
    low = _bounded_int(
        _first_present(op, ("min", "minimum", "low"), default=default_min),
        default_min,
        minimum,
        maximum,
    )
    high = _bounded_int(
        _first_present(op, ("max", "maximum", "high"), default=default_max),
        default_max,
        minimum,
        maximum,
    )
    start_value = _first_present(op, ("from", "start_value"), default=None)
    end_value = _first_present(op, ("to", "end_value"), default=None)

    if shape in {"decrescendo", "fade_out"} and start_value is None and end_value is None:
        start_value, end_value = high, low
    elif (
        shape in {"crescendo", "fade_in", "linear", "ramp"}
        and start_value is None
        and end_value is None
    ):
        start_value, end_value = low, high

    if shape in {"swell", "phrase_swell"}:
        peak_at = _bounded_float(op.get("peak_at"), 0.5, 0.05, 0.95)
        shaped = unit / peak_at if unit <= peak_at else (1.0 - unit) / (1.0 - peak_at)
        value = low + (high - low) * max(0.0, min(1.0, shaped))
    elif shape == "ease_in":
        value = _interpolate_scalar(
            start_value if start_value is not None else low,
            end_value if end_value is not None else high,
            unit * unit,
        )
    elif shape == "ease_out":
        value = _interpolate_scalar(
            start_value if start_value is not None else low,
            end_value if end_value is not None else high,
            1 - (1 - unit) * (1 - unit),
        )
    elif shape == "ease_in_out":
        eased = 0.5 - 0.5 * math.cos(math.pi * unit)
        value = _interpolate_scalar(
            start_value if start_value is not None else low,
            end_value if end_value is not None else high,
            eased,
        )
    elif shape == "lfo":
        cycles = _float_value(op.get("cycles"), 1.0)
        phase = _float_value(op.get("phase"), 0.0)
        value = low + (high - low) * (0.5 + 0.5 * math.sin((unit * cycles + phase) * math.tau))
    elif shape == "step":
        switch_at = _bounded_float(op.get("switch_at"), 0.5, 0.0, 1.0)
        value = end_value if unit >= switch_at and end_value is not None else start_value
        if value is None:
            value = high if unit >= switch_at else low
    elif shape == "hold":
        value = _first_present(op, ("value", "velocity", "pressure"), default=start_value)
        if value is None:
            value = low
    else:
        value = _interpolate_scalar(
            start_value if start_value is not None else low,
            end_value if end_value is not None else high,
            unit,
        )
    return _bounded_int(round(float(value)), default_min, minimum, maximum)


def _operation_beat_range(
    selection: dict[str, Any],
    refs: list[dict[str, Any]],
) -> tuple[float, float]:
    selected_range = _selection_range(selection)
    if selected_range:
        return selected_range
    starts = [float(ref["absolute_start"]) for ref in refs]
    if not starts:
        return (0.0, 0.0)
    return (min(starts), max(starts))


def _set_note_velocity(note: dict[str, Any], value: int) -> int:
    bounded = _bounded_int(value, 96, 1, 127)
    changed = int(int(note.get("velocity", 0)) != bounded)
    note["velocity"] = bounded
    return changed


def _accent_matches(beat: float, op: dict[str, Any]) -> bool:
    pattern = str(op.get("pattern") or "downbeats").strip().lower()
    tolerance = _bounded_float(op.get("tolerance"), 1e-4, 0.0, 0.5)
    if pattern == "backbeat":
        return _beat_mod_matches(beat, 4.0, 1.0, tolerance) or _beat_mod_matches(
            beat, 4.0, 3.0, tolerance
        )
    if pattern in {"offbeat", "upbeats"}:
        return _beat_mod_matches(beat, 1.0, 0.5, tolerance)
    every = _float_value(_first_present(op, ("every", "grid"), default=4.0), 4.0)
    offset = _float_value(op.get("offset"), 0.0)
    return _beat_mod_matches(beat, max(every, 1e-6), offset, tolerance)


def _beat_mod_matches(beat: float, every: float, offset: float, tolerance: float) -> bool:
    delta = (beat - offset) % every
    return delta <= tolerance or every - delta <= tolerance


def _stable_signed_amount(seed: str, amount: int) -> int:
    if amount <= 0:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return digest[0] % (amount * 2 + 1) - amount


def _range_unit(value: float, start: float, end: float) -> float:
    if end <= start:
        return 1.0
    return max(0.0, min(1.0, (value - start) / (end - start)))


def _interpolate_scalar(start: Any, end: Any, unit: float) -> float:
    return float(start) + (float(end) - float(start)) * max(0.0, min(1.0, unit))


def _numeric_stats(values: list[Any]) -> dict[str, Any]:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {
        "count": len(numeric),
        "min": min(numeric),
        "max": max(numeric),
        "avg": round(sum(numeric) / len(numeric), 3),
    }


def _beat_stats(values: list[Any]) -> dict[str, Any]:
    stats = _numeric_stats(values)
    if stats["count"] == 0:
        return stats
    return {**stats, "min": round(stats["min"], 6), "max": round(stats["max"], 6)}


def _event_lane_summaries(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lanes: dict[str, dict[str, Any]] = {}
    for ref in refs:
        event = ref["event"]
        key = _event_lane_key(event)
        lane = lanes.setdefault(
            key,
            {
                "id": key,
                "type": event["type"],
                "channel": event.get("channel"),
                "controller": event.get("controller"),
                "pitch": event.get("pitch"),
                "count": 0,
                "starts": [],
                "values": [],
            },
        )
        lane["count"] += 1
        lane["starts"].append(ref["absolute_start"])
        event_value = _event_numeric_value(event)
        if event_value is not None:
            lane["values"].append(event_value)
    summaries = []
    for lane in lanes.values():
        summaries.append(
            {
                "id": lane["id"],
                "type": lane["type"],
                "channel": lane["channel"],
                "controller": lane["controller"],
                "pitch": lane["pitch"],
                "count": lane["count"],
                "beat_range": _beat_stats(lane["starts"]),
                "value": _numeric_stats(lane["values"]),
            }
        )
    return sorted(summaries, key=lambda lane: lane["id"])


def _event_lane_key(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "")
    channel = event.get("channel", "")
    if event_type == "control_change":
        return f"cc:{event.get('controller', 0)}:ch{channel}"
    if event_type == "polyphonic_key_pressure":
        return f"poly_pressure:{event.get('pitch', 0)}:ch{channel}"
    return f"{event_type}:ch{channel}"


def _event_numeric_value(event: dict[str, Any]) -> int | None:
    for key in ("value", "pressure", "program", "velocity"):
        if key in event:
            return int(event[key])
    return None


def _selection_range(selection: dict[str, Any]) -> tuple[float, float] | None:
    raw_range = selection.get("range")
    if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 2:
        start = _non_negative_float(raw_range[0], 0.0)
        end = _non_negative_float(raw_range[1], start)
        return (start, max(start, end))
    if "start" in selection or "end" in selection:
        start = _non_negative_float(selection.get("start"), 0.0)
        end = _non_negative_float(selection.get("end"), start)
        return (start, max(start, end))
    return None


def _clip_overlaps_range(clip: dict[str, Any], beat_range: tuple[float, float]) -> bool:
    clip_start = float(clip.get("start", 0.0) or 0.0)
    clip_end = clip_start + float(clip.get("duration", 0.0) or 0.0)
    return clip_start <= beat_range[1] + 1e-6 and clip_end >= beat_range[0] - 1e-6


def _as_int_list(value: Any) -> list[int]:
    raw_items = value if isinstance(value, list) else [] if value in (None, "") else [value]
    items = []
    for item in raw_items:
        try:
            items.append(int(item))
        except (TypeError, ValueError):
            continue
    return items


def _as_str_list(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else [] if value in (None, "") else [value]
    return [str(item) for item in raw_items if str(item)]


def _int_range(value: Any, minimum: int, maximum: int) -> list[int]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        start = _bounded_int(value[0], minimum, minimum, maximum)
        end = _bounded_int(value[1], maximum, minimum, maximum)
        return [min(start, end), max(start, end)]
    return [minimum, maximum]


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _track_midi_clips(track: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        clip
        for clip in track.get("clips", [])
        if isinstance(clip, dict) and clip.get("type") == "midi"
    ]


def _target_clip_for_timeline_write(
    track: dict[str, Any],
    payload: dict[str, Any],
    *,
    create: bool = False,
) -> dict[str, Any]:
    clip_id = payload.get("clip_id")
    if clip_id:
        for clip in _track_midi_clips(track):
            if str(clip.get("id")) == str(clip_id):
                return clip
        raise ValueError(f"MIDI clip {clip_id} not found on track {track.get('id')}")

    absolute_start = _payload_absolute_start(payload)
    if absolute_start is not None:
        for clip in _track_midi_clips(track):
            if _clip_contains_beat(clip, absolute_start):
                return clip

    clips = _track_midi_clips(track)
    if clips:
        return clips[0]
    if create:
        return _ensure_midi_clip(track)
    raise ValueError(f"track {track.get('id')} has no MIDI clip")


def _note_payload_to_clip_local(note: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    payload = dict(note)
    if _payload_has_start(payload):
        payload["start"] = _payload_start_to_clip_local(payload, clip)
    return payload


def _event_payload_to_clip_local(event: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event)
    if _payload_has_start(payload):
        payload["start"] = _payload_start_to_clip_local(payload, clip)
        payload.pop("beat", None)
        payload.pop("local_start", None)
    return payload


def _curve_op_to_clip_local(op: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    local_op = dict(op)
    if "range" in local_op and isinstance(local_op["range"], (list, tuple)):
        raw_range = local_op["range"]
        if len(raw_range) >= 2:
            local_op["start"] = raw_range[0]
            local_op["end"] = raw_range[1]

    if "start" in local_op or "beat" in local_op or "local_start" in local_op:
        local_op["start"] = _payload_start_to_clip_local(local_op, clip)
        local_op.pop("beat", None)
        local_op.pop("local_start", None)
    if "end" in local_op:
        local_op["end"] = _absolute_to_clip_local(float(local_op["end"]), clip)

    for key in ("points", "curve"):
        if isinstance(local_op.get(key), list):
            local_op[key] = [_curve_point_to_clip_local(point, clip) for point in local_op[key]]
    return local_op


def _curve_point_to_clip_local(point: Any, clip: dict[str, Any]) -> Any:
    if isinstance(point, dict):
        local_point = dict(point)
        if "local_start" in local_point:
            local_point["start"] = _non_negative_float(local_point["local_start"], 0.0)
            local_point.pop("local_start", None)
        elif "start" in local_point or "beat" in local_point:
            local_point["start"] = _payload_start_to_clip_local(local_point, clip)
            local_point.pop("beat", None)
        return local_point
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        return [_absolute_to_clip_local(float(point[0]), clip), *list(point[1:])]
    return point


def _find_timeline_note(
    track: dict[str, Any],
    op: dict[str, Any],
) -> dict[str, Any] | None:
    for clip in _track_midi_clips(track):
        for note in clip.get("notes", []):
            if isinstance(note, dict) and _timeline_note_matches(note, op, clip):
                return {"clip": clip, "note": note}
    return None


def _delete_timeline_notes(track: dict[str, Any], op: dict[str, Any]) -> int:
    deleted = 0
    for clip in _track_midi_clips(track):
        before = len(clip.get("notes", []))
        clip["notes"] = [
            note
            for note in clip.get("notes", [])
            if not (isinstance(note, dict) and _timeline_note_matches(note, op, clip))
        ]
        deleted += before - len(clip["notes"])
    return deleted


def _timeline_note_matches(note: dict[str, Any], op: dict[str, Any], clip: dict[str, Any]) -> bool:
    if not _op_matches_clip(op, clip):
        return False
    note_id = op.get("id") or op.get("note_id")
    if note_id:
        return bool(note.get("id") == note_id)

    criteria_seen = False
    if "pitch" in op:
        criteria_seen = True
        if int(note["pitch"]) != int(op["pitch"]):
            return False
    if "local_start" in op:
        criteria_seen = True
        if abs(float(note["start"]) - float(op["local_start"])) > 1e-6:
            return False
    elif "start" in op or "beat" in op:
        criteria_seen = True
        absolute_start = float(_first_present(op, ("start", "beat")))
        if abs(_note_absolute_start(note, clip) - absolute_start) > 1e-6:
            return False
    return criteria_seen


def _find_timeline_event(
    track: dict[str, Any],
    op: dict[str, Any],
) -> dict[str, Any] | None:
    for clip in _track_midi_clips(track):
        for event in clip.get("events", []):
            if isinstance(event, dict) and _timeline_event_matches(event, op, clip):
                return {"clip": clip, "event": event}
    return None


def _delete_timeline_events(track: dict[str, Any], op: dict[str, Any]) -> int:
    deleted = 0
    for clip in _track_midi_clips(track):
        before = len(clip.get("events", []))
        clip["events"] = [
            event
            for event in clip.get("events", [])
            if not (isinstance(event, dict) and _timeline_event_matches(event, op, clip))
        ]
        deleted += before - len(clip["events"])
    return deleted


def _timeline_event_matches(
    event: dict[str, Any],
    op: dict[str, Any],
    clip: dict[str, Any],
) -> bool:
    if not _op_matches_clip(op, clip):
        return False
    event_id = op.get("event_id") or op.get("id")
    if event_id:
        return bool(event.get("id") == event_id)

    criteria = _event_match_criteria(op)
    criteria_seen = False

    event_type = _event_type_from_payload(criteria)
    if event_type:
        criteria_seen = True
        if str(event.get("type") or "") != event_type:
            return False

    if "local_start" in criteria:
        criteria_seen = True
        if abs(float(event.get("start", 0.0) or 0.0) - float(criteria["local_start"])) > 1e-6:
            return False
    else:
        beat = _first_present(criteria, ("start", "beat"))
        if beat is not None:
            criteria_seen = True
            if abs(_event_absolute_start(event, clip) - float(beat)) > 1e-6:
                return False

    for key in ("channel", "pitch", "controller"):
        if key in criteria:
            criteria_seen = True
            if int(event.get(key, -1)) != int(criteria[key]):
                return False

    return criteria_seen


def _op_matches_clip(op: dict[str, Any], clip: dict[str, Any]) -> bool:
    clip_id = op.get("clip_id")
    return not clip_id or str(clip.get("id")) == str(clip_id)


def _payload_has_start(payload: dict[str, Any]) -> bool:
    return "local_start" in payload or "start" in payload or "beat" in payload


def _payload_absolute_start(payload: dict[str, Any]) -> float | None:
    if "local_start" in payload:
        return None
    raw_start = _first_present(payload, ("start", "beat"))
    if raw_start is None:
        return None
    return _non_negative_float(raw_start, 0.0)


def _payload_start_to_clip_local(payload: dict[str, Any], clip: dict[str, Any]) -> float:
    if "local_start" in payload:
        return _non_negative_float(payload["local_start"], 0.0)
    raw_start = _first_present(payload, ("start", "beat"), default=0.0)
    return _absolute_to_clip_local(float(raw_start), clip)


def _absolute_to_clip_local(absolute: float, clip: dict[str, Any]) -> float:
    clip_start = float(clip.get("start", 0.0) or 0.0)
    if absolute < clip_start - 1e-6:
        raise ValueError(
            f"absolute beat {absolute:g} is before MIDI clip {clip.get('id')} start {clip_start:g}"
        )
    return round(max(0.0, absolute - clip_start), 6)


def _clip_contains_beat(clip: dict[str, Any], beat: float) -> bool:
    clip_start = float(clip.get("start", 0.0) or 0.0)
    clip_end = clip_start + float(clip.get("duration", 0.0) or 0.0)
    return clip_start - 1e-6 <= beat <= clip_end + 1e-6


def _note_absolute_start(note: dict[str, Any], clip: dict[str, Any]) -> float:
    return float(clip.get("start", 0.0) or 0.0) + float(note.get("start", 0.0) or 0.0)


def _event_absolute_start(event: dict[str, Any], clip: dict[str, Any]) -> float:
    return float(clip.get("start", 0.0) or 0.0) + float(event.get("start", 0.0) or 0.0)


def _find_event(container: dict[str, Any], op: dict[str, Any]) -> dict[str, Any] | None:
    raw_events = container.get("events", [])
    events = raw_events if isinstance(raw_events, list) else []
    for raw_event in events:
        if not isinstance(raw_event, dict):
            continue
        event = cast(dict[str, Any], raw_event)
        if _event_matches(event, op):
            return event
    return None


def _event_matches(event: dict[str, Any], op: dict[str, Any]) -> bool:
    event_id = op.get("event_id") or op.get("id")
    if event_id:
        return bool(event.get("id") == event_id)

    criteria = _event_match_criteria(op)
    criteria_seen = False

    event_type = _event_type_from_payload(criteria)
    if event_type:
        criteria_seen = True
        if str(event.get("type") or "") != event_type:
            return False

    beat = _first_present(criteria, ("start", "beat"))
    if beat is not None:
        criteria_seen = True
        if abs(float(event.get("start", 0.0) or 0.0) - float(beat)) > 1e-6:
            return False

    for key in ("channel", "pitch", "controller"):
        if key in criteria:
            criteria_seen = True
            if int(event.get(key, -1)) != int(criteria[key]):
                return False

    return criteria_seen


def _event_match_criteria(op: dict[str, Any]) -> dict[str, Any]:
    target = op.get("target")
    criteria: dict[str, Any] = dict(target) if isinstance(target, dict) else {}
    for key in (
        "type",
        "event_type",
        "kind",
        "message",
        "start",
        "beat",
        "local_start",
        "channel",
        "pitch",
        "controller",
        "cc",
    ):
        if key in op:
            criteria[key] = op[key]
    return _normalize_event_aliases(criteria)


def _event_payload_from_op(
    op: dict[str, Any],
    *,
    include_identity: bool = True,
) -> dict[str, Any]:
    raw_event = op.get("event")
    payload: dict[str, Any] = dict(raw_event) if isinstance(raw_event, dict) else {}

    for key in (
        "clip_id",
        "start",
        "beat",
        "local_start",
        "channel",
        "pitch",
        "velocity",
        "controller",
        "cc",
        "value",
        "program",
        "pressure",
        "data_b64",
        "data",
        "bytes",
    ):
        if key in op:
            payload[key] = op[key]

    explicit_type = _first_present(op, ("event_type", "kind", "message"))
    if explicit_type is not None:
        payload["type"] = explicit_type
    elif "type" in op:
        raw_type = str(op["type"]).strip().lower()
        if raw_type not in MIDI_EVENT_OPERATION_NAMES:
            payload["type"] = op["type"]

    if include_identity:
        if "id" in op:
            payload["id"] = op["id"]
    else:
        payload.pop("id", None)
        payload.pop("event_id", None)

    return _normalize_event_aliases(payload)


def _normalize_event_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize public MIDI event aliases to canonical project fields."""
    normalized = dict(payload)
    if "cc" in normalized and "controller" not in normalized:
        normalized["controller"] = normalized["cc"]
    return normalized


def _apply_midi_event_curve(
    clip: dict[str, Any],
    op: dict[str, Any],
    op_type: str,
) -> tuple[int, int]:
    target = _curve_event_target(op, op_type)
    value_field = _curve_value_field(str(target["type"]))
    minimum, maximum, default = _curve_value_bounds(str(target["type"]))
    points = _curve_points(op, value_field, minimum, maximum, default)
    start, end = _curve_range(op, points)
    resolution = _curve_resolution(op)
    sampled_points = _sample_curve(points, start, end, resolution)

    mode = str(op.get("mode") or "replace").strip().lower()
    if mode not in {"replace", "append"}:
        raise ValueError("MIDI event curve mode must be 'replace' or 'append'")

    deleted = 0
    if mode == "replace":
        before = len(clip["events"])
        clip["events"] = [
            event
            for event in clip["events"]
            if not (
                _event_matches_curve_target(event, target)
                and start - 1e-6 <= float(event.get("start", 0.0) or 0.0) <= end + 1e-6
            )
        ]
        deleted = before - len(clip["events"])

    for beat, value in sampled_points:
        event = {
            **target,
            "id": f"e_{uuid4().hex[:10]}",
            "start": beat,
            value_field: value,
        }
        clip["events"].append(_normalize_midi_event(event))

    return len(sampled_points), deleted


def _apply_velocity_curve(clip: dict[str, Any], op: dict[str, Any]) -> int:
    points = _curve_points(op, "velocity", 1, 127, 96)
    start, end = _curve_range(op, points)
    updated = 0
    for note in clip.get("notes", []):
        if not isinstance(note, dict):
            continue
        beat = float(note.get("start", 0.0) or 0.0)
        if not (start - 1e-6 <= beat <= end + 1e-6):
            continue
        velocity = _interpolate_curve_value(points, beat)
        if int(note.get("velocity", 0)) != velocity:
            updated += 1
        note["velocity"] = velocity
    return updated


def _curve_event_target(op: dict[str, Any], op_type: str) -> dict[str, Any]:
    raw_target = op.get("target")
    payload: dict[str, Any] = dict(raw_target) if isinstance(raw_target, dict) else {}
    op = _normalize_event_aliases(op)

    if op_type in {"cc_curve", "draw_controller_curve", "set_controller_curve"}:
        payload["type"] = "control_change"
    elif op_type == "pitch_bend_curve":
        payload["type"] = "pitch_bend"
    elif op_type in {"aftertouch_curve", "channel_pressure_curve"}:
        payload["type"] = "channel_pressure"

    explicit_type = _first_present(op, ("event_type", "kind", "message"))
    if explicit_type is not None:
        payload["type"] = explicit_type
    elif "type" in op:
        raw_type = str(op["type"]).strip().lower()
        if raw_type not in MIDI_EVENT_OPERATION_NAMES:
            payload["type"] = op["type"]

    if "controller" in op:
        payload["controller"] = op["controller"]
    if "channel" in op:
        payload["channel"] = op["channel"]
    if "pitch" in op:
        payload["pitch"] = op["pitch"]
    payload = _normalize_event_aliases(payload)

    event_type = _event_type_from_payload(payload) or "control_change"
    if event_type not in MIDI_CURVE_EVENT_TYPES:
        raise ValueError(f"MIDI event curves do not support {event_type}")

    target: dict[str, Any] = {
        "type": event_type,
        "start": 0.0,
        "channel": _bounded_int(payload.get("channel"), 0, 0, 15),
    }
    if event_type == "control_change":
        target["controller"] = _bounded_int(payload.get("controller"), 1, 0, 127)
        target["value"] = 0
    elif event_type == "pitch_bend":
        target["value"] = 0
    elif event_type == "channel_pressure":
        target["pressure"] = 0
    elif event_type == "polyphonic_key_pressure":
        target["pitch"] = _bounded_int(payload.get("pitch"), 60, 0, 127)
        target["pressure"] = 0
    return target


def _event_matches_curve_target(event: dict[str, Any], target: dict[str, Any]) -> bool:
    if str(event.get("type") or "") != str(target.get("type") or ""):
        return False
    if int(event.get("channel", 0)) != int(target.get("channel", 0)):
        return False
    if target.get("type") == "control_change":
        return int(event.get("controller", -1)) == int(target.get("controller", -2))
    if target.get("type") == "polyphonic_key_pressure":
        return int(event.get("pitch", -1)) == int(target.get("pitch", -2))
    return True


def _curve_value_field(event_type: str) -> str:
    return "pressure" if event_type in {"channel_pressure", "polyphonic_key_pressure"} else "value"


def _curve_value_bounds(event_type: str) -> tuple[int, int, int]:
    if event_type == "pitch_bend":
        return (-8192, 8191, 0)
    return (0, 127, 0)


def _curve_points(
    op: dict[str, Any],
    value_field: str,
    minimum: int,
    maximum: int,
    default: int,
) -> list[tuple[float, int]]:
    raw_points = op.get("points", op.get("curve"))
    points: list[tuple[float, int]] = []

    if isinstance(raw_points, list) and raw_points:
        for raw_point in raw_points:
            if isinstance(raw_point, dict):
                beat = _first_present(raw_point, ("start", "beat"))
                value = _first_present(
                    raw_point,
                    (value_field, "value", "pressure", "velocity"),
                    default=default,
                )
            elif isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
                beat = raw_point[0]
                value = raw_point[1]
            else:
                continue
            points.append(
                (
                    _non_negative_float(beat, 0.0),
                    _bounded_int(value, default, minimum, maximum),
                )
            )

    if not points:
        if any(key in op for key in ("start_value", "end_value", "from", "to")):
            if "start" not in op or "end" not in op:
                raise ValueError("MIDI curve start and end beats are required")
            start_value = _first_present(op, ("start_value", "from"), default=default)
            end_value = _first_present(op, ("end_value", "to"), default=start_value)
            points = [
                (
                    _non_negative_float(op.get("start"), 0.0),
                    _bounded_int(start_value, default, minimum, maximum),
                ),
                (
                    _non_negative_float(op.get("end"), 0.0),
                    _bounded_int(end_value, default, minimum, maximum),
                ),
            ]
        elif "value" in op or value_field in op:
            value = _first_present(op, (value_field, "value"), default=default)
            points = [
                (
                    _non_negative_float(op.get("start"), 0.0),
                    _bounded_int(value, default, minimum, maximum),
                )
            ]

    if not points:
        raise ValueError("MIDI curve requires points or start/end values")

    points.sort(key=lambda point: point[0])
    deduped: dict[float, int] = {}
    for beat, value in points:
        deduped[round(beat, 6)] = value
    return sorted(deduped.items())


def _curve_range(op: dict[str, Any], points: list[tuple[float, int]]) -> tuple[float, float]:
    start = _non_negative_float(op.get("start"), points[0][0])
    end = _non_negative_float(op.get("end"), points[-1][0])
    if end < start:
        raise ValueError("MIDI curve end must be greater than or equal to start")
    return start, end


def _curve_resolution(op: dict[str, Any]) -> float | None:
    if "resolution" in op:
        raw = op.get("resolution")
    elif "step" in op:
        raw = op.get("step")
    else:
        raw = 0.25
    if raw is None:
        return 0.25
    try:
        resolution = float(raw)
    except (TypeError, ValueError):
        return 0.25
    return resolution if resolution > 0 else None


def _sample_curve(
    points: list[tuple[float, int]],
    start: float,
    end: float,
    resolution: float | None,
) -> list[tuple[float, int]]:
    if resolution is None or abs(end - start) <= 1e-9:
        return [(beat, value) for beat, value in points if start - 1e-6 <= beat <= end + 1e-6] or [
            (start, _interpolate_curve_value(points, start))
        ]

    beats = _sample_beats_with_limit(start, end, resolution)
    return [(beat, _interpolate_curve_value(points, beat)) for beat in beats]


def _interpolate_curve_value(points: list[tuple[float, int]], beat: float) -> int:
    if beat <= points[0][0]:
        return points[0][1]
    if beat >= points[-1][0]:
        return points[-1][1]
    for left, right in pairwise(points):
        left_beat, left_value = left
        right_beat, right_value = right
        if left_beat <= beat <= right_beat:
            span = max(right_beat - left_beat, 1e-9)
            unit = (beat - left_beat) / span
            return round(left_value + (right_value - left_value) * unit)
    return points[-1][1]


def _find_note(container: dict[str, Any], op: dict[str, Any]) -> dict[str, Any] | None:
    raw_notes = container.get("notes", [])
    notes = raw_notes if isinstance(raw_notes, list) else []
    for raw_note in notes:
        if not isinstance(raw_note, dict):
            continue
        note = cast(dict[str, Any], raw_note)
        if _note_matches(note, op):
            return note
    return None


def _note_matches(note: dict[str, Any], op: dict[str, Any]) -> bool:
    note_id = op.get("id") or op.get("note_id")
    if note_id:
        return bool(note.get("id") == note_id)
    if "pitch" in op and int(note["pitch"]) != int(op["pitch"]):
        return False
    if "start" in op and abs(float(note["start"]) - float(op["start"])) > 1e-6:
        return False
    return "pitch" in op or "start" in op


def _normalize_note(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(note.get("id") or f"n_{uuid4().hex[:10]}"),
        "pitch": _bounded_int(note.get("pitch"), 60, 0, 127),
        "start": _non_negative_float(note.get("start"), 0.0),
        "duration": _positive_float(note.get("duration"), 0.25),
        "velocity": _bounded_int(note.get("velocity"), 96, 1, 127),
    }


def _event_type_from_payload(payload: dict[str, Any]) -> str:
    raw_type = _first_present(payload, ("event_type", "type", "kind", "message"), default="")
    return _normalize_midi_event_type(raw_type)


def _normalize_midi_event_type(value: Any) -> str:
    event_type = str(value or "").lower()
    event_type = event_type.replace("-", "_").replace(" ", "_")
    aliases = {
        "noteon": "note_on",
        "noteoff": "note_off",
        "cc": "control_change",
        "controller": "control_change",
        "pitchbend": "pitch_bend",
        "programchange": "program_change",
        "channelpressure": "channel_pressure",
        "aftertouch": "channel_pressure",
        "after_touch": "channel_pressure",
        "poly_pressure": "polyphonic_key_pressure",
        "poly_aftertouch": "polyphonic_key_pressure",
        "poly_after_touch": "polyphonic_key_pressure",
        "allnotesoff": "all_notes_off",
        "systemexclusive": "sysex",
        "system_exclusive": "sysex",
    }
    return aliases.get(event_type, event_type)


def _normalize_midi_event(event: dict[str, Any]) -> dict[str, Any]:
    event = _normalize_event_aliases(event)
    event_type = _event_type_from_payload(event)
    if event_type not in {
        "note_on",
        "note_off",
        "control_change",
        "pitch_bend",
        "program_change",
        "channel_pressure",
        "polyphonic_key_pressure",
        "all_notes_off",
        "sysex",
    }:
        event_type = "control_change"

    normalized: dict[str, Any] = {
        "id": str(event.get("id") or f"e_{uuid4().hex[:10]}"),
        "type": event_type,
        "start": _non_negative_float(event.get("start", event.get("beat")), 0.0),
    }
    if event_type != "sysex":
        normalized["channel"] = _bounded_int(event.get("channel"), 0, 0, 15)

    if event_type in {"note_on", "note_off", "polyphonic_key_pressure"}:
        normalized["pitch"] = _bounded_int(event.get("pitch"), 60, 0, 127)
    if event_type in {"note_on", "note_off"}:
        default_velocity = 96 if event_type == "note_on" else 0
        normalized["velocity"] = _bounded_int(event.get("velocity"), default_velocity, 0, 127)
    if event_type == "control_change":
        normalized["controller"] = _bounded_int(event.get("controller"), 0, 0, 127)
        normalized["value"] = _bounded_int(event.get("value"), 0, 0, 127)
    elif event_type == "pitch_bend":
        normalized["value"] = _bounded_int(event.get("value"), 0, -8192, 8191)
    elif event_type == "program_change":
        normalized["program"] = _bounded_int(event.get("program", event.get("value")), 0, 0, 127)
    elif event_type == "channel_pressure":
        normalized["pressure"] = _bounded_int(event.get("pressure", event.get("value")), 0, 0, 127)
    elif event_type == "polyphonic_key_pressure":
        normalized["pressure"] = _bounded_int(event.get("pressure", event.get("value")), 0, 0, 127)
    elif event_type == "sysex":
        normalized["data_b64"] = _normalize_sysex_b64(event)
    return normalized


def _normalize_sysex_b64(event: dict[str, Any]) -> str:
    data_b64 = str(event.get("data_b64") or "")
    if data_b64:
        try:
            base64.b64decode(data_b64, validate=True)
            return data_b64
        except (ValueError, binascii.Error):
            pass

    raw = event.get("data", event.get("bytes"))
    if isinstance(raw, list):
        payload = bytes(_bounded_int(value, 0, 0, 255) for value in raw)
    elif isinstance(raw, str):
        cleaned = raw.replace("0x", "").replace(",", " ").replace("-", " ")
        parts = [part for part in cleaned.split() if part]
        try:
            payload = bytes(int(part, 16) for part in parts)
        except ValueError:
            payload = b""
    else:
        payload = b""
    return base64.b64encode(payload).decode("ascii") if payload else ""


def normalize_audio_waveform(value: Any) -> list[float | dict[str, float]]:
    return _normalize_waveform(value)


def _normalize_waveform(value: Any) -> list[float | dict[str, float]]:
    if not isinstance(value, list):
        return []
    waveform: list[float | dict[str, float]] = []
    for point in value[:512]:
        normalized = _normalize_waveform_point(point)
        if normalized is not None:
            waveform.append(normalized)
    return waveform


def _normalize_waveform_point(point: Any) -> float | dict[str, float] | None:
    if isinstance(point, dict):
        return _normalize_waveform_metrics(point)

    parsed = _finite_float(point)
    if parsed is None:
        return None
    return _round_waveform_value(min(1.0, abs(parsed)))


def _normalize_waveform_metrics(point: dict[str, Any]) -> dict[str, float] | None:
    raw_min = _finite_float(point.get("min"))
    raw_max = _finite_float(point.get("max"))
    raw_peak = _finite_float(point.get("peak"))
    raw_rms = _finite_float(point.get("rms"))

    peak = min(1.0, abs(raw_peak)) if raw_peak is not None else None
    rms = min(1.0, abs(raw_rms)) if raw_rms is not None else None
    if raw_min is None and raw_max is None:
        if peak is None:
            return None
        min_value = -peak
        max_value = peak
    else:
        fallback = peak or 0.0
        if raw_min is None:
            min_value = -max(fallback, abs(raw_max or 0.0))
        else:
            min_value = _clamp_float(raw_min, -1.0, 1.0)
        if raw_max is None:
            max_value = max(fallback, abs(raw_min or 0.0))
        else:
            max_value = _clamp_float(raw_max, -1.0, 1.0)
        if min_value > max_value:
            min_value, max_value = max_value, min_value

    envelope_peak = max(abs(min_value), abs(max_value))
    if rms is None:
        rms = envelope_peak * 0.58
    if peak is None:
        peak = envelope_peak
    peak = min(1.0, max(peak, envelope_peak, rms))
    rms = min(rms, peak)
    return {
        "min": _round_waveform_value(min_value),
        "max": _round_waveform_value(max_value),
        "rms": _round_waveform_value(rms),
        "peak": _round_waveform_value(peak),
    }


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _round_waveform_value(value: float) -> float:
    rounded = round(value, 4)
    return 0.0 if rounded == 0 else rounded


def _midi_event_sort_key(event: dict[str, Any]) -> tuple[float, str, int, int, str]:
    return (
        float(event.get("start", 0.0) or 0.0),
        str(event.get("type") or ""),
        int(event.get("channel", -1)),
        int(event.get("pitch", event.get("controller", -1))),
        str(event.get("id") or ""),
    )


def _update_midi_clip_duration(clip: dict[str, Any]) -> None:
    note_end = max(
        (
            float(note.get("start", 0.0) or 0.0) + float(note.get("duration", 0.0) or 0.0)
            for note in clip.get("notes", [])
            if isinstance(note, dict)
        ),
        default=0.0,
    )
    event_end = max(
        (
            float(event.get("start", 0.0) or 0.0)
            for event in clip.get("events", [])
            if isinstance(event, dict)
        ),
        default=0.0,
    )
    clip["duration"] = max(float(clip.get("duration", 0.0) or 0.0), note_end, event_end, 0.25)


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _normalize_meter(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        num = _bounded_int(value[0], 4, 1, MAX_METER_NUMERATOR)
        den = _normalize_meter_denominator(value[1])
        return [num, den]
    return [4, 4]


def _normalize_meter_denominator(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 4
    return parsed if parsed in METER_DENOMINATORS else 4


def _track_color(value: Any, index: int) -> str:
    color = str(value or "").strip()
    if len(color) == 7 and color.startswith("#"):
        try:
            int(color[1:], 16)
            return color
        except ValueError:
            pass
    return DEFAULT_TRACK_COLORS[index % len(DEFAULT_TRACK_COLORS)]


def _ceil_to_bar(beats: float, bar: int = 4) -> float:
    if beats <= 0:
        return float(bar * 4)
    bars = int((beats + bar - 1e-9) // bar)
    if beats > bars * bar:
        bars += 1
    return float(max(bar * 4, bars * bar))


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nullable_non_negative_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _normalize_track_type(track: dict[str, Any], *, clips: list[dict[str, Any]]) -> str:
    raw_type = str(track.get("type", track.get("track_type", "")) or "").strip().lower()
    if raw_type in {"instrument", "audio", "automation", "bus"}:
        return raw_type
    if str(track.get("instrument") or "").strip().lower() == "audio track":
        return "audio"
    if clips and all(clip.get("type") == "audio" for clip in clips):
        return "audio"
    return "instrument"


def _normalize_track_channel_type(value: Any, *, track_type: str) -> str:
    if track_type != "audio":
        return "multichannel"
    parsed = str(value or "").strip().lower().replace("-", "_")
    if parsed in {"mono", "monophonic"}:
        return "mono"
    if parsed in {"multi", "multichannel", "multi_channel", "stereo"}:
        return "multichannel"
    return "multichannel"


def _normalize_plugin_slots(
    track: dict[str, Any],
    *,
    track_type: str = "instrument",
) -> list[dict[str, Any]]:
    if track_type not in {"instrument", "bus"}:
        return []

    raw_slots = track.get("plugin_slots")
    slots: list[dict[str, Any]] = []
    if isinstance(raw_slots, list) and raw_slots:
        slot_map: dict[str, dict[str, Any]] = {}
        slot_order: list[str] = []
        for raw_slot in raw_slots:
            if not isinstance(raw_slot, dict):
                continue
            slot = _normalize_plugin_slot(raw_slot)
            if track_type == "bus" and slot["id"] == "instrument":
                continue
            if slot["id"] not in slot_map:
                slot_order.append(slot["id"])
            slot_map[slot["id"]] = slot
        slots = [slot_map[slot_id] for slot_id in slot_order]

    if track_type == "instrument" and not any(slot.get("id") == "instrument" for slot in slots):
        slots.insert(
            0,
            _normalize_plugin_slot(
                {
                    "type": "builtin",
                    "name": track.get("instrument") or "ATRI Basic Synth",
                },
                slot_id="instrument",
            ),
        )
    return _sort_plugin_slots(slots)


def _normalize_track_sends(track: dict[str, Any]) -> list[dict[str, Any]]:
    raw_sends = track.get("sends")
    if not isinstance(raw_sends, list):
        return []

    sends_by_target: dict[int, dict[str, Any]] = {}
    target_order: list[int] = []
    for raw_send in raw_sends:
        if not isinstance(raw_send, dict):
            continue
        target_bus_id = _nullable_non_negative_int(
            raw_send.get("target_bus_id", raw_send.get("target_track_id"))
        )
        if target_bus_id is None:
            continue
        send_id = str(raw_send.get("id") or f"send_{target_bus_id}").strip()
        if not send_id:
            send_id = f"send_{target_bus_id}"
        if target_bus_id not in sends_by_target:
            target_order.append(target_bus_id)
        sends_by_target[target_bus_id] = {
            "id": send_id,
            "target_bus_id": target_bus_id,
            "level": _bounded_float(raw_send.get("level"), 1.0, 0.0, 2.0),
            "enabled": bool(raw_send.get("enabled", True)),
        }
    return [sends_by_target[target_bus_id] for target_bus_id in target_order]


def _repair_output_bus_routing(tracks: list[dict[str, Any]]) -> None:
    bus_ids = {int(track["id"]) for track in tracks if track.get("type") == "bus"}

    for track in tracks:
        output_bus_id = track.get("output_bus_id")
        if output_bus_id is None:
            track["output_bus_id"] = None
            continue
        if int(output_bus_id) not in bus_ids or int(output_bus_id) == int(track["id"]):
            track["output_bus_id"] = None

    outputs = {
        int(track["id"]): track.get("output_bus_id")
        for track in tracks
        if track.get("output_bus_id") is not None
    }

    def has_cycle(start_id: int) -> bool:
        seen: set[int] = set()
        current_id = start_id
        while current_id in outputs:
            if current_id in seen:
                return True
            seen.add(current_id)
            current_id = int(outputs[current_id])
        return False

    for track in tracks:
        if has_cycle(int(track["id"])):
            track["output_bus_id"] = None

    _repair_track_sends(tracks)


def _repair_track_sends(tracks: list[dict[str, Any]]) -> None:
    bus_ids = {int(track["id"]) for track in tracks if track.get("type") == "bus"}
    outputs = {
        int(track["id"]): int(track["output_bus_id"])
        for track in tracks
        if track.get("output_bus_id") is not None
    }
    send_edges: dict[int, list[int]] = {int(track["id"]): [] for track in tracks}

    for track in tracks:
        source_id = int(track["id"])
        repaired: list[dict[str, Any]] = []
        seen_targets: set[int] = set()
        for send in track.get("sends", []):
            if not isinstance(send, dict):
                continue
            target_bus_id = _nullable_non_negative_int(send.get("target_bus_id"))
            if target_bus_id is None:
                continue
            if target_bus_id not in bus_ids or target_bus_id == source_id:
                continue
            if target_bus_id in seen_targets:
                continue
            if _route_reaches(
                target_bus_id,
                source_id,
                outputs=outputs,
                sends=send_edges,
            ):
                continue
            send["target_bus_id"] = target_bus_id
            repaired.append(send)
            send_edges[source_id].append(target_bus_id)
            seen_targets.add(target_bus_id)
        track["sends"] = repaired


def _route_reaches(
    start_id: int,
    wanted_id: int,
    *,
    outputs: dict[int, int],
    sends: dict[int, list[int]],
) -> bool:
    seen: set[int] = set()
    stack = [start_id]
    while stack:
        current_id = stack.pop()
        if current_id == wanted_id:
            return True
        if current_id in seen:
            continue
        seen.add(current_id)
        output_id = outputs.get(current_id)
        if output_id is not None:
            stack.append(output_id)
        stack.extend(sends.get(current_id, []))
    return False


def _normalize_plugin_slot(
    plugin: dict[str, Any] | None, *, slot_id: str = "instrument"
) -> dict[str, Any]:
    plugin = plugin if isinstance(plugin, dict) else {}
    slot_id = str(plugin.get("id") or slot_id or "instrument").strip() or "instrument"
    plugin_type = str(plugin.get("type") or plugin.get("format") or "builtin").lower()
    if plugin_type not in {"empty", "builtin", "vst3", "vst2"}:
        plugin_type = "builtin"
    if slot_id == "instrument" and plugin_type == "empty":
        plugin_type = "builtin"
    if slot_id != "instrument" and plugin_type == "builtin":
        plugin_type = "empty"

    if plugin_type == "empty":
        name = "Empty"
    elif plugin_type == "builtin":
        name = str(plugin.get("name") or "ATRI Basic Synth")
    else:
        name = str(plugin.get("name") or "Plugin")
    slot: dict[str, Any] = {
        "id": slot_id,
        "type": plugin_type,
        "name": name,
        "path": str(plugin.get("path") or ""),
        "dll_path": str(plugin.get("dll_path") or ""),
        "vendor": str(plugin.get("vendor") or ""),
        "category": str(plugin.get("category") or ""),
        "version": str(plugin.get("version") or ""),
    }
    state_b64 = str(plugin.get("state_b64") or "")
    if state_b64:
        slot["state_b64"] = state_b64
    return slot


def _sort_plugin_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(slots, key=_plugin_slot_sort_key)


def _plugin_slot_sort_key(slot: dict[str, Any]) -> tuple[int, str]:
    slot_id = str(slot.get("id") or "")
    if slot_id == "instrument":
        return (0, slot_id)
    if slot_id.startswith("insert_"):
        try:
            return (100 + int(slot_id.removeprefix("insert_")), slot_id)
        except ValueError:
            return (199, slot_id)
    return (500, slot_id)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        parsed = default
    return parsed


def _non_negative_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, parsed)


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
