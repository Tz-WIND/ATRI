"""Piano lane model helpers for meter and harmony events."""

from __future__ import annotations

from typing import Any, cast

from core.music.meter_harmony_model import (
    _normalize_harmony_events,
    _normalize_meter_events,
    _normalize_piano_subtrack_order,
)
from core.music.model_constants import PIANO_SUBTRACK_IDS
from core.music.value_normalization import (
    _first_present,
    _non_negative_float,
)


def _normalize_piano_lane_id(lane: str) -> str:
    lane_id = str(lane or "").strip().lower()
    if lane_id in PIANO_SUBTRACK_IDS:
        return lane_id
    raise ValueError("lane must be 'meter' or 'harmony'")


def _piano_lane_event_field(lane: str) -> str:
    return "meter_events" if lane == "meter" else "harmony_events"


def _normalize_piano_lane_events(lane: str, value: Any) -> list[dict[str, Any]]:
    if lane == "meter":
        return _normalize_meter_events(value)
    return _normalize_harmony_events(value)


def _normalize_piano_lane_range(
    start: Any,
    end: Any,
) -> tuple[float, float | None] | None:
    if start is None and end is None:
        return None
    start_beat = _non_negative_float(start, 0.0) if start is not None else 0.0
    end_beat = _non_negative_float(end, start_beat) if end is not None else None
    if end_beat is not None and end_beat < start_beat:
        raise ValueError("end must be greater than or equal to start")
    return start_beat, end_beat


def _piano_lane_beat_in_range(beat: float, start: float, end: float | None) -> bool:
    return beat >= start and (end is None or beat < end)


def _ensure_piano_subtrack_order(project: dict[str, Any], lane: str) -> None:
    order = _normalize_piano_subtrack_order(project.get("piano_subtrack_order"))
    if project.get(_piano_lane_event_field(lane)) and lane not in order:
        order.append(lane)
    project["piano_subtrack_order"] = order


def _event_from_piano_lane_op(lane: str, op: dict[str, Any]) -> dict[str, Any]:
    raw_event = op.get("event")
    event_payload = cast(dict[str, Any], raw_event) if isinstance(raw_event, dict) else op
    normalized = _normalize_piano_lane_events(lane, [event_payload])
    if not normalized:
        raise ValueError(f"{lane} event is required")
    return normalized[0]


def _piano_lane_event_beat(op: dict[str, Any]) -> float:
    raw_event = op.get("event")
    payload = cast(dict[str, Any], raw_event) if isinstance(raw_event, dict) else op
    return round(_non_negative_float(_first_present(payload, ("beat", "start"), 0.0), 0.0), 6)


def _upsert_piano_lane_event(
    events: list[dict[str, Any]],
    event: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    event_beat = float(event["beat"])
    did_add = not any(abs(float(item["beat"]) - event_beat) <= 1e-6 for item in events)
    kept = [item for item in events if abs(float(item["beat"]) - event_beat) > 1e-6]
    return sorted([*kept, event], key=lambda item: float(item["beat"])), did_add


def _update_piano_lane_event(
    lane: str,
    events: list[dict[str, Any]],
    op: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    beat = _piano_lane_event_beat(op)
    existing = next(
        (event for event in events if abs(float(event["beat"]) - beat) <= 1e-6),
        None,
    )
    raw_event = op.get("event")
    updates = dict(raw_event) if isinstance(raw_event, dict) else dict(op)
    updates.pop("op", None)
    updates.pop("type", None)
    payload = {**(existing or {}), **updates, "beat": beat}
    next_events, did_add = _upsert_piano_lane_event(
        events,
        _event_from_piano_lane_op(lane, payload),
    )
    return next_events, not did_add


def _delete_piano_lane_event(
    events: list[dict[str, Any]],
    op: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    beat = _piano_lane_event_beat(op)
    kept = [event for event in events if abs(float(event["beat"]) - beat) > 1e-6]
    return kept, len(events) - len(kept)


def _replace_piano_lane_event_range(
    lane: str,
    events: list[dict[str, Any]],
    op: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    event_range = _normalize_piano_lane_range(op.get("start"), op.get("end")) or (0.0, None)
    range_start, range_end = event_range
    kept = []
    deleted = 0
    for event in events:
        if _piano_lane_beat_in_range(float(event["beat"]), range_start, range_end):
            deleted += 1
        else:
            kept.append(event)
    incoming = _normalize_piano_lane_events(lane, op.get("events") or [])
    return [*kept, *incoming], {"deleted": deleted, "added": len(incoming)}
