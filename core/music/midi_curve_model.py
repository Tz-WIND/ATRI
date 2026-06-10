"""MIDI curve parsing, sampling, and application helpers."""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any
from uuid import uuid4

from core.music.midi_event_model import (
    _event_type_from_payload,
    _normalize_event_aliases,
    _normalize_midi_event,
)
from core.music.model_constants import (
    MIDI_CURVE_EVENT_TYPES,
    MIDI_CURVE_MAX_POINTS,
    MIDI_EVENT_OPERATION_NAMES,
)
from core.music.value_normalization import (
    _bounded_int,
    _first_present,
    _non_negative_float,
)


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


def _curve_sample_beats(start: float, end: float, resolution: float | None) -> list[float]:
    if abs(end - start) <= 1e-9:
        return [round(start, 6)]
    if resolution is None:
        return [round(start, 6), round(end, 6)]
    return _sample_beats_with_limit(start, end, resolution)


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
