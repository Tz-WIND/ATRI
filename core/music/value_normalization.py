"""Small value coercion helpers for Music Studio project modeling."""

from __future__ import annotations

import hashlib
import math
from typing import Any


def _accent_matches(beat: float, op: dict[str, Any]) -> bool:
    pattern = str(op.get("pattern") or "downbeats").strip().lower()
    tolerance = _bounded_float(op.get("tolerance"), 1e-4, 0.0, 0.5)
    if pattern == "backbeat":
        return _beat_mod_matches(beat, 4.0, 1.0, tolerance) or _beat_mod_matches(
            beat,
            4.0,
            3.0,
            tolerance,
        )
    if pattern in {"offbeat", "upbeats"}:
        return _beat_mod_matches(beat, 1.0, 0.5, tolerance)
    every = _float_value(_first_present(op, ("every", "grid"), default=4.0), 4.0)
    offset = _float_value(op.get("offset"), 0.0)
    return _beat_mod_matches(beat, max(every, 1e-6), offset, tolerance)


def _beat_mod_matches(beat: float, every: float, offset: float, tolerance: float) -> bool:
    delta = (beat - offset) % every
    return delta <= tolerance or every - delta <= tolerance


def _stable_signed_amount(seed: str, amount: int) -> int:
    if amount <= 0:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return digest[0] % (amount * 2 + 1) - amount


def _range_unit(value: float, start: float, end: float) -> float:
    if end <= start:
        return 1.0
    return max(0.0, min(1.0, (value - start) / (end - start)))


def _interpolate_scalar(start: Any, end: Any, unit: float) -> float:
    return float(start) + (float(end) - float(start)) * max(0.0, min(1.0, unit))


def _numeric_stats(values: list[Any]) -> dict[str, Any]:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {
        "count": len(numeric),
        "min": min(numeric),
        "max": max(numeric),
        "avg": round(sum(numeric) / len(numeric), 3),
    }


def _beat_stats(values: list[Any]) -> dict[str, Any]:
    stats = _numeric_stats(values)
    if stats["count"] == 0:
        return stats
    return {**stats, "min": round(stats["min"], 6), "max": round(stats["max"], 6)}


def _as_int_list(value: Any) -> list[int]:
    raw_items = value if isinstance(value, list) else [] if value in (None, "") else [value]
    items = []
    for item in raw_items:
        try:
            items.append(int(item))
        except (TypeError, ValueError):
            continue
    return items


def _as_str_list(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else [] if value in (None, "") else [value]
    return [str(item) for item in raw_items if str(item)]


def _int_range(value: Any, minimum: int, maximum: int) -> list[int]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        start = _bounded_int(value[0], minimum, minimum, maximum)
        end = _bounded_int(value[1], maximum, minimum, maximum)
        return [min(start, end), max(start, end)]
    return [minimum, maximum]


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _finite_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _round_waveform_value(value: float) -> float:
    rounded = round(value, 4)
    return 0.0 if rounded == 0 else rounded


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


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
