"""MIDI event normalization and query helpers."""

from __future__ import annotations

import base64
import binascii
from typing import Any
from uuid import uuid4

from core.music.value_normalization import (
    _beat_stats,
    _bounded_float,
    _bounded_int,
    _first_present,
    _non_negative_float,
    _numeric_stats,
)


def _event_lane_summaries(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lanes: dict[str, dict[str, Any]] = {}
    for ref in refs:
        event = ref["event"]
        key = _event_lane_key(event)
        lane = lanes.setdefault(
            key,
            {
                "id": key,
                "type": event["type"],
                "channel": event.get("channel"),
                "controller": event.get("controller"),
                "pitch": event.get("pitch"),
                "count": 0,
                "starts": [],
                "values": [],
            },
        )
        lane["count"] += 1
        lane["starts"].append(ref["absolute_start"])
        event_value = _event_numeric_value(event)
        if event_value is not None:
            lane["values"].append(event_value)
    summaries = []
    for lane in lanes.values():
        summaries.append(
            {
                "id": lane["id"],
                "type": lane["type"],
                "channel": lane["channel"],
                "controller": lane["controller"],
                "pitch": lane["pitch"],
                "count": lane["count"],
                "beat_range": _beat_stats(lane["starts"]),
                "value": _numeric_stats(lane["values"]),
            }
        )
    return sorted(summaries, key=lambda lane: lane["id"])


def _event_lane_key(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "")
    channel = event.get("channel", "")
    if event_type == "control_change":
        return f"cc:{event.get('controller', 0)}:ch{channel}"
    if event_type == "polyphonic_key_pressure":
        return f"poly_pressure:{event.get('pitch', 0)}:ch{channel}"
    return f"{event_type}:ch{channel}"


def _event_numeric_value(event: dict[str, Any]) -> int | None:
    for key in ("value", "pressure", "program", "velocity"):
        if key in event:
            return int(event[key])
    return None


def _normalize_event_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize public MIDI event aliases to canonical project fields."""
    normalized = dict(payload)
    if "cc" in normalized and "controller" not in normalized:
        normalized["controller"] = normalized["cc"]
    return normalized


def _event_type_from_payload(payload: dict[str, Any]) -> str:
    raw_type = _first_present(payload, ("event_type", "type", "kind", "message"), default="")
    return _normalize_midi_event_type(raw_type)


def _normalize_midi_event_type(value: Any) -> str:
    event_type = str(value or "").lower()
    event_type = event_type.replace("-", "_").replace(" ", "_")
    aliases = {
        "noteon": "note_on",
        "noteoff": "note_off",
        "cc": "control_change",
        "controller": "control_change",
        "pitchbend": "pitch_bend",
        "programchange": "program_change",
        "channelpressure": "channel_pressure",
        "aftertouch": "channel_pressure",
        "after_touch": "channel_pressure",
        "poly_pressure": "polyphonic_key_pressure",
        "poly_aftertouch": "polyphonic_key_pressure",
        "poly_after_touch": "polyphonic_key_pressure",
        "allnotesoff": "all_notes_off",
        "systemexclusive": "sysex",
        "system_exclusive": "sysex",
    }
    return aliases.get(event_type, event_type)


def _normalize_midi_event(event: dict[str, Any]) -> dict[str, Any]:
    event = _normalize_event_aliases(event)
    event_type = _event_type_from_payload(event)
    if event_type not in {
        "note_on",
        "note_off",
        "control_change",
        "pitch_bend",
        "program_change",
        "channel_pressure",
        "polyphonic_key_pressure",
        "all_notes_off",
        "sysex",
    }:
        event_type = "control_change"

    normalized: dict[str, Any] = {
        "id": str(event.get("id") or f"e_{uuid4().hex[:10]}"),
        "type": event_type,
        "start": _non_negative_float(event.get("start", event.get("beat")), 0.0),
    }
    if event_type != "sysex":
        normalized["channel"] = _bounded_int(event.get("channel"), 0, 0, 15)

    if event_type in {"note_on", "note_off", "polyphonic_key_pressure"}:
        normalized["pitch"] = _bounded_int(event.get("pitch"), 60, 0, 127)
    if event_type in {"note_on", "note_off"}:
        default_velocity = 96 if event_type == "note_on" else 0
        normalized["velocity"] = _bounded_int(event.get("velocity"), default_velocity, 0, 127)
    if event_type == "control_change":
        normalized["controller"] = _bounded_int(event.get("controller"), 0, 0, 127)
        normalized["value"] = _bounded_int(event.get("value"), 0, 0, 127)
    elif event_type == "pitch_bend":
        normalized["value"] = _bounded_int(event.get("value"), 0, -8192, 8191)
    elif event_type == "program_change":
        normalized["program"] = _bounded_int(event.get("program", event.get("value")), 0, 0, 127)
    elif event_type == "channel_pressure":
        normalized["pressure"] = _bounded_int(event.get("pressure", event.get("value")), 0, 0, 127)
    elif event_type == "polyphonic_key_pressure":
        normalized["pressure"] = _bounded_int(event.get("pressure", event.get("value")), 0, 0, 127)
    elif event_type == "sysex":
        normalized["data_b64"] = _normalize_sysex_b64(event)
    curve_amount = _normalized_curve_amount(event)
    if abs(curve_amount) > 1e-6:
        normalized["curve_amount"] = curve_amount
    return normalized


def _normalized_curve_amount(value: dict[str, Any]) -> float:
    curve_amount = _bounded_float(
        _first_present(value, ("curve_amount", "curveAmount"), default=0.0),
        0.0,
        -1.0,
        1.0,
    )
    return round(curve_amount, 6)


def _normalize_sysex_b64(event: dict[str, Any]) -> str:
    data_b64 = str(event.get("data_b64") or "")
    if data_b64:
        try:
            base64.b64decode(data_b64, validate=True)
            return data_b64
        except (ValueError, binascii.Error):
            pass

    raw = event.get("data", event.get("bytes"))
    if isinstance(raw, list):
        payload = bytes(_bounded_int(value, 0, 0, 255) for value in raw)
    elif isinstance(raw, str):
        cleaned = raw.replace("0x", "").replace(",", " ").replace("-", " ")
        parts = [part for part in cleaned.split() if part]
        try:
            payload = bytes(int(part, 16) for part in parts)
        except ValueError:
            payload = b""
    else:
        payload = b""
    return base64.b64encode(payload).decode("ascii") if payload else ""


def _midi_event_sort_key(event: dict[str, Any]) -> tuple[float, str, int, int, str]:
    return (
        float(event.get("start", 0.0) or 0.0),
        str(event.get("type") or ""),
        int(event.get("channel", -1)),
        int(event.get("pitch", event.get("controller", -1))),
        str(event.get("id") or ""),
    )
