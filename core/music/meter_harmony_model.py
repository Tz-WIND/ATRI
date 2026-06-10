"""Project-level meter and harmony normalization helpers."""

from __future__ import annotations

from typing import Any

from core.music.model_constants import (
    MAX_METER_NUMERATOR,
    METER_DENOMINATORS,
    PIANO_SUBTRACK_IDS,
)
from core.music.value_normalization import (
    _bounded_int,
    _first_present,
    _non_negative_float,
)


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


def _normalize_meter_events(value: Any) -> list[dict[str, Any]]:
    raw_events = value if isinstance(value, list) else []
    by_beat: dict[float, dict[str, Any]] = {}
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue
        beat = round(_non_negative_float(_first_present(raw_event, ("beat", "start"), 0.0), 0.0), 6)
        numerator = _bounded_int(raw_event.get("numerator"), 4, 1, MAX_METER_NUMERATOR)
        denominator = _normalize_meter_denominator(raw_event.get("denominator"))
        by_beat[beat] = {
            "beat": beat,
            "numerator": numerator,
            "denominator": denominator,
        }
    return [by_beat[beat] for beat in sorted(by_beat)]


def _normalize_harmony_events(value: Any) -> list[dict[str, Any]]:
    raw_events = value if isinstance(value, list) else []
    by_beat: dict[float, dict[str, Any]] = {}
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue
        text = str(_first_present(raw_event, ("text", "label", "name", "chord"), "") or "").strip()
        if not text:
            continue
        beat = round(_non_negative_float(_first_present(raw_event, ("beat", "start"), 0.0), 0.0), 6)
        by_beat[beat] = {
            "beat": beat,
            "text": text,
        }
    return [by_beat[beat] for beat in sorted(by_beat)]


def _normalize_piano_subtrack_order(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else []
    order: list[str] = []
    for raw_item in raw_items:
        item = str(raw_item or "").strip().lower()
        if item not in PIANO_SUBTRACK_IDS or item in order:
            continue
        order.append(item)
    return order
