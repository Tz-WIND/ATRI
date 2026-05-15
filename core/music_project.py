"""Persistent AI music workstation project state.

The Rust host is intentionally a realtime renderer, not the source of truth for
session data. This module keeps the editable DAW project in JSON so the
dashboard, Agent tools, and host sync path all operate on the same data model.
"""

from __future__ import annotations

import base64
import binascii
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from core.utils import atomic_write_text

PROJECT_PATH = Path("data/music_workstation/project.json")

DEFAULT_TRACK_COLORS = ["#4e79ff", "#d95b55", "#5f916b", "#d7b66f", "#b489d6", "#58a7b8"]


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
        legacy_notes = [
            _normalize_note(note) for note in raw_track.get("notes", []) if isinstance(note, dict)
        ]
        legacy_notes.sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
        clips = _normalize_clips(raw_track, legacy_notes=legacy_notes, track_color=track_color)
        notes = _flatten_clip_notes(clips)
        midi_events = _flatten_clip_midi_events(clips)
        normalized["tracks"].append(
            {
                "id": track_id,
                "host_track_id": _nullable_non_negative_int(raw_track.get("host_track_id")),
                "name": str(raw_track.get("name") or f"Track {track_id}"),
                "color": track_color,
                "volume": _bounded_float(raw_track.get("volume"), 0.8, 0.0, 2.0),
                "pan": _bounded_float(raw_track.get("pan"), 0.0, -1.0, 1.0),
                "mute": bool(raw_track.get("mute", False)),
                "solo": bool(raw_track.get("solo", False)),
                "instrument": str(raw_track.get("instrument") or "ATRI Basic Synth"),
                "plugin_slots": _normalize_plugin_slots(raw_track),
                "clips": clips,
                "notes": notes,
                "midi_events": midi_events,
            }
        )

    if not normalized["tracks"]:
        normalized["tracks"] = deepcopy(base["tracks"])

    max_end = max(
        (
            clip["start"] + clip["duration"]
            for track in normalized["tracks"]
            for clip in track["clips"]
        ),
        default=0.0,
    )
    normalized["length_beats"] = max(normalized["length_beats"], _ceil_to_bar(max_end))
    return normalized


def create_track(
    name: str = "Instrument",
    *,
    color: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = load_project()
    existing = [int(track["id"]) for track in project["tracks"]]
    track_id = max(existing, default=0) + 1
    track: dict[str, Any] = {
        "id": track_id,
        "host_track_id": None,
        "name": name.strip() or f"Track {track_id}",
        "color": _track_color(color, track_id - 1),
        "volume": 0.8,
        "pan": 0.0,
        "mute": False,
        "solo": False,
        "instrument": "ATRI Basic Synth",
        "clips": [],
        "notes": [],
        "midi_events": [],
    }
    project["tracks"].append(track)
    project = save_project(project)
    return project, find_track(project, track_id)


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
    if "instrument" in updates:
        track["instrument"] = str(updates["instrument"] or "ATRI Basic Synth")
    if "clips" in updates and isinstance(updates["clips"], list):
        track["clips"] = updates["clips"]
    if "plugin_slots" in updates and isinstance(updates["plugin_slots"], list):
        track["plugin_slots"] = _normalize_plugin_slots({"plugin_slots": updates["plugin_slots"]})
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
    slot = _normalize_plugin_slot(plugin, slot_id=slot_id)
    slots = [s for s in track.get("plugin_slots", []) if s.get("id") != slot["id"]]
    track["plugin_slots"] = _sort_plugin_slots([slot, *slots])
    if slot["id"] == "instrument":
        track["instrument"] = slot["name"]
    project = save_project(project)
    return project, find_track(project, track_id)


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
    clip["duration"] = max(
        float(clip.get("duration", 0.0) or 0.0),
        max((note["start"] + note["duration"] for note in clip["notes"]), default=0.0),
    )
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
    clip = _ensure_midi_clip(track)
    changed = {"added": 0, "deleted": 0, "updated": 0}

    for op in operations:
        op_type = str(op.get("op") or op.get("type") or "")
        if op_type == "add_note":
            raw_note = op.get("note")
            note_data = cast(dict[str, Any], raw_note) if isinstance(raw_note, dict) else op
            clip["notes"].append(_normalize_note(note_data))
            changed["added"] += 1
        elif op_type == "delete_note":
            before = len(clip["notes"])
            clip["notes"] = [note for note in clip["notes"] if not _note_matches(note, op)]
            changed["deleted"] += before - len(clip["notes"])
        elif op_type in {"update_note", "modify_note"}:
            note = _find_note(clip, op)
            if note is None:
                continue
            for key in ("pitch", "start", "duration", "velocity"):
                if key in op:
                    note[key] = _normalize_note({**note, key: op[key]})[key]
            changed["updated"] += 1
        else:
            raise ValueError(f"unsupported MIDI diff operation: {op_type}")

    clip["notes"].sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
    clip["duration"] = max(
        float(clip.get("duration", 0.0) or 0.0),
        max((note["start"] + note["duration"] for note in clip["notes"]), default=0.0),
    )
    project = save_project(project)
    synced_track = find_track(project, track_id)
    summary = {
        "track_id": track["id"],
        "requested_track_id": track_id,
        "host_track_id": track.get("host_track_id"),
        "operations": len(operations),
        **changed,
        "track_note_count": len(synced_track["notes"]),
    }
    return project, summary


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
    return {
        "title": project["title"],
        "tempo": project["tempo"],
        "time_signature": project["time_signature"],
        "length_beats": project["length_beats"],
        "track_count": len(project["tracks"]),
        "note_count": note_count,
        "tracks": [
            {
                "id": track["id"],
                "name": track["name"],
                "notes": len(track["notes"]),
                "clips": len(track.get("clips", [])),
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
    else:
        clips = []

    if clips:
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
        "color": _track_color(clip.get("color") or track_color, 0),
        "source": str(clip.get("source") or ""),
        "path": str(clip.get("path") or ""),
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


def _normalize_midi_event(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or event.get("kind") or event.get("message") or "").lower()
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
        "poly_pressure": "polyphonic_key_pressure",
        "poly_aftertouch": "polyphonic_key_pressure",
        "allnotesoff": "all_notes_off",
        "systemexclusive": "sysex",
        "system_exclusive": "sysex",
    }
    event_type = aliases.get(event_type, event_type)
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


def _midi_event_sort_key(event: dict[str, Any]) -> tuple[float, str, int, int, str]:
    return (
        float(event.get("start", 0.0) or 0.0),
        str(event.get("type") or ""),
        int(event.get("channel", -1)),
        int(event.get("pitch", event.get("controller", -1))),
        str(event.get("id") or ""),
    )


def _normalize_meter(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        num = _bounded_int(value[0], 4, 1, 32)
        den = _bounded_int(value[1], 4, 1, 32)
        return [num, den]
    return [4, 4]


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


def _normalize_plugin_slots(track: dict[str, Any]) -> list[dict[str, Any]]:
    raw_slots = track.get("plugin_slots")
    if isinstance(raw_slots, list) and raw_slots:
        slot_map: dict[str, dict[str, Any]] = {}
        slot_order: list[str] = []
        for raw_slot in raw_slots:
            if not isinstance(raw_slot, dict):
                continue
            slot = _normalize_plugin_slot(raw_slot)
            if slot["id"] not in slot_map:
                slot_order.append(slot["id"])
            slot_map[slot["id"]] = slot
        slots = [slot_map[slot_id] for slot_id in slot_order]
    else:
        slots = []
    if not any(slot.get("id") == "instrument" for slot in slots):
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
