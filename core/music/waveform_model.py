"""Audio waveform normalization helpers."""

from __future__ import annotations

from typing import Any

from core.music.value_normalization import (
    _clamp_float,
    _finite_float,
    _round_waveform_value,
)


def normalize_audio_waveform(value: Any) -> list[float | dict[str, float]]:
    return _normalize_waveform(value)


def _normalize_waveform(value: Any) -> list[float | dict[str, float]]:
    if not isinstance(value, list):
        return []
    waveform: list[float | dict[str, float]] = []
    for point in value[:512]:
        normalized = _normalize_waveform_point(point)
        if normalized is not None:
            waveform.append(normalized)
    return waveform


def _normalize_waveform_point(point: Any) -> float | dict[str, float] | None:
    if isinstance(point, dict):
        return _normalize_waveform_metrics(point)

    parsed = _finite_float(point)
    if parsed is None:
        return None
    return _round_waveform_value(min(1.0, abs(parsed)))


def _normalize_waveform_metrics(point: dict[str, Any]) -> dict[str, float] | None:
    raw_min = _finite_float(point.get("min"))
    raw_max = _finite_float(point.get("max"))
    raw_peak = _finite_float(point.get("peak"))
    raw_rms = _finite_float(point.get("rms"))

    peak = min(1.0, abs(raw_peak)) if raw_peak is not None else None
    rms = min(1.0, abs(raw_rms)) if raw_rms is not None else None
    if raw_min is None and raw_max is None:
        if peak is None:
            return None
        min_value = -peak
        max_value = peak
    else:
        fallback = peak or 0.0
        if raw_min is None:
            min_value = -max(fallback, abs(raw_max or 0.0))
        else:
            min_value = _clamp_float(raw_min, -1.0, 1.0)
        if raw_max is None:
            max_value = max(fallback, abs(raw_min or 0.0))
        else:
            max_value = _clamp_float(raw_max, -1.0, 1.0)
        if min_value > max_value:
            min_value, max_value = max_value, min_value

    envelope_peak = max(abs(min_value), abs(max_value))
    if rms is None:
        rms = envelope_peak * 0.58
    if peak is None:
        peak = envelope_peak
    peak = min(1.0, max(peak, envelope_peak, rms))
    rms = min(rms, peak)
    return {
        "min": _round_waveform_value(min_value),
        "max": _round_waveform_value(max_value),
        "rms": _round_waveform_value(rms),
        "peak": _round_waveform_value(peak),
    }
