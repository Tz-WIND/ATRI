"""MIDI clip container helpers for Music Studio projects."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from core.music.model_constants import DEFAULT_TRACK_COLORS


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


def _clip_overlaps_range(clip: dict[str, Any], beat_range: tuple[float, float]) -> bool:
    clip_start = float(clip.get("start", 0.0) or 0.0)
    clip_end = clip_start + float(clip.get("duration", 0.0) or 0.0)
    return clip_start <= beat_range[1] + 1e-6 and clip_end >= beat_range[0] - 1e-6


def _track_midi_clips(track: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        clip
        for clip in track.get("clips", [])
        if isinstance(clip, dict) and clip.get("type") == "midi"
    ]


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
