"""Clip normalization and lookup helpers for Music Studio projects."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from core.music import track_lookup
from core.music.midi_event_model import _midi_event_sort_key, _normalize_midi_event
from core.music.midi_note_model import _normalize_note
from core.music.track_model import _track_color
from core.music.value_normalization import (
    _bounded_float,
    _first_present,
    _non_negative_float,
    _positive_float,
)
from core.music.waveform_model import _normalize_waveform


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


def _clip_id_from_op(op: dict[str, Any]) -> str:
    clip_payload = op.get("clip") if isinstance(op.get("clip"), dict) else {}
    clip_id = op.get("clip_id") or op.get("id") or cast(dict[str, Any], clip_payload).get("id")
    if not clip_id:
        raise ValueError("clip_id is required")
    return str(clip_id)


def _target_track_for_clip_op(
    project: dict[str, Any],
    op: dict[str, Any],
    *,
    default_track: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_track_id = op.get("target_track_id", op.get("track_id"))
    if raw_track_id is None or raw_track_id == "":
        if default_track is not None:
            return default_track
        raise ValueError("track_id is required")
    return track_lookup.find_track(project, int(str(raw_track_id)))


def _find_clip_record(project: dict[str, Any], clip_id: str) -> dict[str, dict[str, Any]] | None:
    for raw_track in project.get("tracks", []):
        if not isinstance(raw_track, dict):
            continue
        track = cast(dict[str, Any], raw_track)
        for raw_clip in track.get("clips", []):
            if isinstance(raw_clip, dict) and str(raw_clip.get("id")) == str(clip_id):
                return {"track": track, "clip": cast(dict[str, Any], raw_clip)}
    return None


def _remove_clip_from_track(track: dict[str, Any], clip_id: str) -> None:
    clips = track.get("clips")
    if not isinstance(clips, list):
        track["clips"] = []
        return
    track["clips"] = [
        clip
        for clip in clips
        if not (isinstance(clip, dict) and str(clip.get("id")) == str(clip_id))
    ]


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
