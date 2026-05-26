"""Music21-assisted symbolic transformations for ATRI projects."""

from __future__ import annotations

import re
from typing import Any

from core.music_project import load_project, midi_diff, piano_lane_write
from core.music_theory.music21_harmony import PITCH_CLASS_NAMES

SHARP_PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
PITCH_CLASS_BY_NAME = {
    "c": 0,
    "b#": 0,
    "c#": 1,
    "db": 1,
    "d": 2,
    "d#": 3,
    "eb": 3,
    "e": 4,
    "fb": 4,
    "e#": 5,
    "f": 5,
    "f#": 6,
    "gb": 6,
    "g": 7,
    "g#": 8,
    "ab": 8,
    "a": 9,
    "a#": 10,
    "bb": 10,
    "b": 11,
    "cb": 11,
}
CHORD_ROOT_RE = re.compile(r"^([A-Ga-g])([#b♯♭-]?)(.*)$")
SLASH_ROOT_RE = re.compile(r"/([A-Ga-g])([#b♯♭-]?)")


def transpose_music(
    *,
    track_ids: list[int] | None = None,
    beat_range: list[float] | tuple[float, float] | None = None,
    semitones: int | None = None,
    from_key: str = "",
    to_key: str = "",
    transpose_harmony: bool = True,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or apply a MIDI/harmony transposition.

    from_key and to_key accept root names only, such as C, F#, or Bb. They do
    not model major/minor modes.
    """
    interval = _transpose_interval(semitones=semitones, from_key=from_key, to_key=to_key)
    project = load_project()
    selected_range = _normalize_range(beat_range, fallback_end=project["length_beats"])
    notes = _selected_note_updates(
        project,
        track_ids=track_ids,
        beat_range=selected_range,
        interval=interval,
    )
    harmony_events = (
        _selected_harmony_updates(project, beat_range=selected_range, interval=interval)
        if transpose_harmony
        else []
    )
    result: dict[str, Any] = {
        "applied": False,
        "semitones": interval,
        "range": [selected_range[0], selected_range[1]],
        "track_ids": track_ids or [],
        "notes": notes,
        "harmony_events": harmony_events,
        "summary": {
            "note_count": len(notes),
            "harmony_event_count": len(harmony_events),
        },
    }
    if apply:
        _apply_note_updates(notes)
        if transpose_harmony:
            piano_lane_write(
                "harmony",
                [{"beat": event["beat"], "text": event["new_text"]} for event in harmony_events],
                mode="replace",
                start=selected_range[0],
                end=selected_range[1],
            )
        result["applied"] = True
    return result


def _transpose_interval(*, semitones: int | None, from_key: str, to_key: str) -> int:
    if semitones is not None:
        return int(semitones)
    if not from_key or not to_key:
        raise ValueError("semitones or both from_key and to_key are required")
    source = _pitch_class(from_key)
    target = _pitch_class(to_key)
    interval = target - source
    if interval > 6:
        interval -= 12
    elif interval < -6:
        interval += 12
    return interval


def _selected_note_updates(
    project: dict[str, Any],
    *,
    track_ids: list[int] | None,
    beat_range: tuple[float, float],
    interval: int,
) -> list[dict[str, Any]]:
    wanted_ids = {int(track_id) for track_id in track_ids or []}
    updates: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        if str(track.get("type") or "instrument") not in {"instrument", "bus"}:
            continue
        track_id = int(track.get("id", -1))
        if wanted_ids and track_id not in wanted_ids:
            continue
        for note in track.get("notes", []):
            if not isinstance(note, dict):
                continue
            start = float(note.get("start", 0.0) or 0.0)
            pitch = int(note.get("pitch", 60) or 60)
            duration = max(0.0, float(note.get("duration", 0.0) or 0.0))
            end = start + duration
            if end <= beat_range[0] or start >= beat_range[1]:
                continue
            new_pitch = pitch + interval
            if not 0 <= new_pitch <= 127:
                raise ValueError(f"transposed pitch out of MIDI range: {pitch} -> {new_pitch}")
            updates.append(
                {
                    "track_id": track_id,
                    "id": str(note.get("id") or ""),
                    "start": round(start, 6),
                    "pitch": pitch,
                    "new_pitch": new_pitch,
                }
            )
    return updates


def _selected_harmony_updates(
    project: dict[str, Any],
    *,
    beat_range: tuple[float, float],
    interval: int,
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for event in project.get("harmony_events", []):
        if not isinstance(event, dict):
            continue
        beat = float(event.get("beat", 0.0) or 0.0)
        if not (beat_range[0] - 1e-9 <= beat < beat_range[1] + 1e-9):
            continue
        text = str(event.get("text") or "")
        updates.append(
            {
                "beat": round(beat, 6),
                "text": text,
                "new_text": transpose_harmony_text(text, interval),
            }
        )
    return updates


def _apply_note_updates(notes: list[dict[str, Any]]) -> None:
    operations_by_track: dict[int, list[dict[str, Any]]] = {}
    for note in notes:
        note_id = str(note.get("id") or "")
        if not note_id:
            continue
        operations_by_track.setdefault(int(note["track_id"]), []).append(
            {
                "op": "update_note",
                "id": note_id,
                "pitch": int(note["new_pitch"]),
            }
        )
    for track_id, operations in operations_by_track.items():
        if operations:
            midi_diff(track_id, operations)


def transpose_harmony_text(text: str, semitones: int) -> str:
    match = CHORD_ROOT_RE.match(text.strip())
    if not match:
        return text
    root = _transpose_root(match.group(1), match.group(2), semitones)
    suffix = SLASH_ROOT_RE.sub(
        lambda item: "/" + _transpose_root(item.group(1), item.group(2), semitones),
        match.group(3),
    )
    return f"{root}{suffix}"


def _transpose_root(letter: str, accidental: str, semitones: int) -> str:
    raw = _normalize_pitch_name(letter + accidental)
    pitch_class = (_pitch_class(raw) + semitones) % 12
    names = SHARP_PITCH_CLASS_NAMES if "#" in raw else PITCH_CLASS_NAMES
    return names[pitch_class]


def _pitch_class(value: str) -> int:
    normalized = _normalize_pitch_name(value)
    if normalized not in PITCH_CLASS_BY_NAME:
        raise ValueError(f"unsupported pitch or key root: {value}")
    return PITCH_CLASS_BY_NAME[normalized]


def _normalize_pitch_name(value: str) -> str:
    cleaned = str(value or "").strip().split()[0]
    match = CHORD_ROOT_RE.match(cleaned)
    if not match:
        return cleaned.lower().replace("♯", "#").replace("♭", "b").replace("-", "b")
    return (match.group(1) + match.group(2)).lower().replace("♯", "#").replace("♭", "b").replace(
        "-", "b"
    )


def _normalize_range(
    value: list[float] | tuple[float, float] | None,
    *,
    fallback_end: float,
) -> tuple[float, float]:
    if isinstance(value, list | tuple) and len(value) >= 2:
        start = max(0.0, float(value[0] or 0.0))
        end = max(start, float(value[1] or start))
        return round(start, 6), round(end, 6)
    return 0.0, max(0.0, float(fallback_end or 0.0))
