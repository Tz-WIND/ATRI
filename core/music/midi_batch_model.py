"""High-level MIDI batch edit helpers."""

from __future__ import annotations

import math
from typing import Any

from core.music.midi_curve_model import (
    _apply_midi_event_curve,
    _curve_event_target,
    _curve_points,
    _curve_resolution,
    _curve_sample_beats,
    _curve_value_bounds,
    _curve_value_field,
    _event_matches_curve_target,
    _interpolate_curve_value,
)
from core.music.midi_selection import (
    _selected_event_refs,
    _selected_midi_clips,
    _selected_note_refs,
    _selection_range,
)
from core.music.value_normalization import (
    _accent_matches,
    _bounded_float,
    _bounded_int,
    _first_present,
    _float_value,
    _interpolate_scalar,
    _range_unit,
    _stable_signed_amount,
)


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
