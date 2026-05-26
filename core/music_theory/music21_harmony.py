"""Harmony analysis for ATRI projects using music21 as the symbolic backend."""

from __future__ import annotations

import math
from typing import Any

from core.music_project import load_project, piano_lane_write

PITCH_CLASS_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
DEFAULT_CONFIDENCE = 0.55

CHORD_TEMPLATES: tuple[tuple[str, frozenset[int], int], ...] = (
    ("7", frozenset({0, 4, 7, 10}), 7),
    ("maj7", frozenset({0, 4, 7, 11}), 7),
    ("m7", frozenset({0, 3, 7, 10}), 7),
    ("m7b5", frozenset({0, 3, 6, 10}), 7),
    ("dim7", frozenset({0, 3, 6, 9}), 7),
    ("", frozenset({0, 4, 7}), 3),
    ("m", frozenset({0, 3, 7}), 3),
    ("dim", frozenset({0, 3, 6}), 3),
    ("aug", frozenset({0, 4, 8}), 3),
    ("sus4", frozenset({0, 5, 7}), 3),
    ("sus2", frozenset({0, 2, 7}), 3),
    ("5", frozenset({0, 7}), 2),
)


def analyze_harmony(
    *,
    track_ids: list[int] | None = None,
    beat_range: list[float] | tuple[float, float] | None = None,
    window_beats: float | None = None,
    key_window_beats: float | None = None,
    detect_modulations: bool = True,
    min_confidence: float = DEFAULT_CONFIDENCE,
    apply: bool = False,
    mode: str = "replace",
) -> dict[str, Any]:
    """Infer harmony lane events from the current project."""
    project = load_project()
    notes = _selected_notes(project, track_ids=track_ids, beat_range=beat_range)
    start, end = _analysis_range(notes, beat_range=beat_range, fallback_end=project["length_beats"])
    window = _window_beats(project, window_beats)
    key = _analyze_key(notes)
    key_window = _key_window_beats(key_window_beats, window)
    key_events = (
        _analyze_key_events(notes, start=start, end=end, key_window_beats=key_window)
        if detect_modulations
        else []
    )
    events, skipped = _analyze_windows(
        notes,
        start=start,
        end=end,
        window_beats=window,
        key=key,
        key_events=key_events,
        min_confidence=min_confidence,
    )
    result: dict[str, Any] = {
        "applied": False,
        "key": key,
        "key_events": key_events,
        "modulations": _modulations_from_key_events(key_events),
        "events": events,
        "skipped_windows": skipped,
        "summary": {
            "track_ids": track_ids or [],
            "range": [start, end],
            "window_beats": window,
            "note_count": len(notes),
            "event_count": len(events),
            "skipped_window_count": len(skipped),
            "key_window_beats": key_window,
            "min_confidence": _round_float(min_confidence),
        },
    }
    if apply:
        lane_events = [{"beat": event["beat"], "text": event["text"]} for event in events]
        _project, summary = piano_lane_write(
            "harmony",
            lane_events,
            mode=mode,
            start=start if mode == "replace" else None,
            end=end if mode == "replace" else None,
        )
        result["applied"] = True
        result["apply_summary"] = summary
    return result


def _music21():
    try:
        import music21
    except ImportError as e:
        raise RuntimeError(
            "music21 is not installed; run `uv sync` before using harmony analysis"
        ) from e
    return music21


def _selected_notes(
    project: dict[str, Any],
    *,
    track_ids: list[int] | None,
    beat_range: list[float] | tuple[float, float] | None,
) -> list[dict[str, Any]]:
    wanted_ids = {int(track_id) for track_id in track_ids or []}
    selected_range = _normalize_range(beat_range)
    rows: list[dict[str, Any]] = []
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
            start = _finite_float(note.get("start"), 0.0)
            duration = max(0.0, _finite_float(note.get("duration"), 0.0))
            end = start + duration
            if selected_range and (end <= selected_range[0] or start >= selected_range[1]):
                continue
            rows.append(
                {
                    "track_id": track_id,
                    "id": str(note.get("id") or ""),
                    "pitch": _bounded_int(note.get("pitch"), 60, 0, 127),
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "duration": round(duration, 6),
                }
            )
    return sorted(rows, key=lambda item: (item["start"], item["pitch"], item["track_id"]))


def _analysis_range(
    notes: list[dict[str, Any]],
    *,
    beat_range: list[float] | tuple[float, float] | None,
    fallback_end: float,
) -> tuple[float, float]:
    normalized = _normalize_range(beat_range)
    if normalized is not None:
        return normalized
    if notes:
        start = min(float(note["start"]) for note in notes)
        end = max(float(note["end"]) for note in notes)
        return round(start, 6), round(max(start, end), 6)
    return 0.0, max(0.0, _finite_float(fallback_end, 0.0))


def _window_beats(project: dict[str, Any], requested: float | None) -> float:
    if requested is not None:
        parsed = _finite_float(requested, 0.0)
        if parsed > 0:
            return round(parsed, 6)
    meter = project.get("time_signature")
    if isinstance(meter, list | tuple) and meter:
        numerator = _bounded_int(meter[0], 4, 1, 255)
        return float(numerator)
    return 4.0


def _key_window_beats(requested: float | None, harmony_window_beats: float) -> float:
    if requested is not None:
        parsed = _finite_float(requested, 0.0)
        if parsed > 0:
            return round(parsed, 6)
    return round(harmony_window_beats * 2, 6)


def _analyze_key(notes: list[dict[str, Any]]) -> dict[str, Any]:
    if not notes:
        return {"name": "", "tonic": "", "mode": "", "correlation": 0.0}
    m21 = _music21()
    stream = m21.stream.Stream()
    for row in notes:
        note = m21.note.Note(int(row["pitch"]))
        note.quarterLength = max(0.25, float(row["duration"]))
        stream.insert(float(row["start"]), note)
    try:
        key = stream.analyze("key")
    except Exception:
        return {"name": "", "tonic": "", "mode": "", "correlation": 0.0}
    tonic = _pitch_name(getattr(getattr(key, "tonic", None), "name", ""))
    mode = str(getattr(key, "mode", "") or "")
    correlation = _round_float(getattr(key, "correlationCoefficient", 0.0) or 0.0)
    return {
        "name": f"{tonic} {mode}".strip(),
        "tonic": tonic,
        "mode": mode,
        "correlation": correlation,
    }


def _analyze_key_events(
    notes: list[dict[str, Any]],
    *,
    start: float,
    end: float,
    key_window_beats: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor = start
    previous_name = ""
    while cursor < end - 1e-9:
        window_end = min(end, cursor + key_window_beats)
        window_notes = [
            note
            for note in notes
            if min(float(note["end"]), window_end) - max(float(note["start"]), cursor) > 1e-9
        ]
        key = _analyze_key(window_notes)
        name = str(key.get("name") or "")
        if name and name != previous_name:
            events.append({"beat": _round_float(cursor), "key": key})
            previous_name = name
        cursor = round(cursor + key_window_beats, 6)
    return events


def _modulations_from_key_events(key_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    modulations: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for event in key_events:
        key = event.get("key") if isinstance(event, dict) else {}
        if not isinstance(key, dict) or not key.get("name"):
            continue
        if previous is not None:
            previous_key = previous.get("key") if isinstance(previous, dict) else {}
            if isinstance(previous_key, dict) and previous_key.get("name") != key.get("name"):
                modulations.append(
                    {
                        "beat": event["beat"],
                        "from_key": previous_key.get("name", ""),
                        "to_key": key.get("name", ""),
                    }
                )
        previous = event
    return modulations


def _analyze_windows(
    notes: list[dict[str, Any]],
    *,
    start: float,
    end: float,
    window_beats: float,
    key: dict[str, Any],
    key_events: list[dict[str, Any]],
    min_confidence: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cursor = start
    previous_text = ""
    while cursor < end - 1e-9:
        window_end = min(end, cursor + window_beats)
        weights = _window_pitch_class_weights(notes, cursor, window_end)
        if weights:
            local_key = _key_for_beat(cursor, key_events, fallback=key)
            event = _label_window(weights, beat=cursor, key=local_key)
            if float(event["confidence"]) >= min_confidence:
                if event["text"] != previous_text:
                    events.append(event)
                    previous_text = str(event["text"])
            else:
                skipped.append(event)
        cursor = round(cursor + window_beats, 6)
    return events, skipped


def _key_for_beat(
    beat: float,
    key_events: list[dict[str, Any]],
    *,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    active = fallback
    for event in key_events:
        if float(event.get("beat", 0.0) or 0.0) <= beat + 1e-9:
            key = event.get("key")
            if isinstance(key, dict) and key.get("name"):
                active = key
        else:
            break
    return active


def _window_pitch_class_weights(
    notes: list[dict[str, Any]],
    start: float,
    end: float,
) -> dict[int, float]:
    weights: dict[int, float] = {}
    for note in notes:
        overlap = min(float(note["end"]), end) - max(float(note["start"]), start)
        if overlap <= 1e-9:
            continue
        pitch_class = int(note["pitch"]) % 12
        weights[pitch_class] = weights.get(pitch_class, 0.0) + overlap
    return weights


def _label_window(weights: dict[int, float], *, beat: float, key: dict[str, Any]) -> dict[str, Any]:
    pitch_classes = set(weights)
    best = _best_chord_label(weights)
    roman = _roman_numeral(best["root"], best["pitch_classes"], key)
    return {
        "beat": _round_float(beat),
        "text": best["text"],
        "confidence": best["confidence"],
        "pitch_classes": [PITCH_CLASS_NAMES[pitch] for pitch in sorted(pitch_classes)],
        "key": key,
        **({"roman": roman} if roman else {}),
    }


def _best_chord_label(weights: dict[int, float]) -> dict[str, Any]:
    total_weight = max(sum(weights.values()), 1e-9)
    candidates: list[dict[str, Any]] = []
    for root in sorted(weights, key=lambda pc: (-weights[pc], pc)):
        normalized = {(pitch - root) % 12 for pitch in weights}
        for suffix, template, priority in CHORD_TEMPLATES:
            matched = normalized & template
            if len(matched) < min(2, len(template)):
                continue
            matched_weight = sum(
                weight for pitch, weight in weights.items() if (pitch - root) % 12 in template
            )
            missing = len(template - normalized)
            extra = len(normalized - template)
            score = (matched_weight / total_weight) - (missing * 0.15) - (extra * 0.1)
            if 0 not in normalized:
                score -= 0.05
            candidates.append(
                {
                    "root": root,
                    "suffix": suffix,
                    "template": template,
                    "score": score,
                    "priority": priority,
                    "confidence": max(0.0, min(1.0, score)),
                }
            )
    if not candidates:
        root = max(weights, key=lambda pitch: weights[pitch])
        return {
            "root": root,
            "pitch_classes": set(weights),
            "text": PITCH_CLASS_NAMES[root],
            "confidence": 0.25,
        }
    winner = max(
        candidates,
        key=lambda item: (item["score"], item["priority"], weights[item["root"]]),
    )
    root_name = PITCH_CLASS_NAMES[int(winner["root"])]
    return {
        "root": int(winner["root"]),
        "pitch_classes": {(pitch - int(winner["root"])) % 12 for pitch in weights},
        "text": f"{root_name}{winner['suffix']}",
        "confidence": _round_float(float(winner["confidence"])),
    }


def _roman_numeral(root: int, pitch_classes: set[int], key: dict[str, Any]) -> str:
    if not key.get("tonic") or not key.get("mode"):
        return ""
    m21 = _music21()
    try:
        chord_pitches = [root + interval + 60 for interval in sorted(pitch_classes)]
        chord = m21.chord.Chord(chord_pitches)
        key_obj = m21.key.Key(str(key["tonic"]), str(key["mode"]))
        return str(m21.roman.romanNumeralFromChord(chord, key_obj).figure)
    except Exception:
        return ""


def _normalize_range(value: list[float] | tuple[float, float] | None) -> tuple[float, float] | None:
    if not isinstance(value, list | tuple) or len(value) < 2:
        return None
    start = max(0.0, _finite_float(value[0], 0.0))
    end = max(start, _finite_float(value[1], start))
    return round(start, 6), round(end, 6)


def _pitch_name(value: str) -> str:
    return str(value or "").replace("-", "b")


def _round_float(value: float) -> float:
    if not math.isfinite(float(value)):
        return 0.0
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0 else rounded


def _finite_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
