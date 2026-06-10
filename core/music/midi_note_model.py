"""MIDI note normalization and lookup helpers."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from core.music.value_normalization import (
    _bounded_int,
    _non_negative_float,
    _positive_float,
)


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
