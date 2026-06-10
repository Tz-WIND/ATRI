"""Automation track model helpers for Music Studio projects."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from core.music import track_lookup
from core.music.midi_event_model import _normalized_curve_amount
from core.music.model_constants import (
    GLOBAL_AUTOMATION_TARGET_KINDS,
    MAX_METER_NUMERATOR,
    TRACK_AUTOMATION_TARGET_KINDS,
)
from core.music.track_model import _track_color
from core.music.value_normalization import (
    _bounded_float,
    _bounded_int,
    _first_present,
    _non_negative_float,
    _positive_int,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_automation_track(
    track_id: int,
    *,
    target: dict[str, Any],
    automation: dict[str, Any],
    name: str = "",
    color: str | None = None,
) -> dict[str, Any]:
    return {
        "id": track_id,
        "host_track_id": None,
        "type": "automation",
        "channel_type": "multichannel",
        "name": str(name or target.get("label") or f"Automation {track_id}").strip()
        or f"Automation {track_id}",
        "color": _track_color(color, track_id - 1),
        "volume": 0.8,
        "pan": 0.0,
        "mute": False,
        "solo": False,
        "instrument": "Automation",
        "plugin_slots": [],
        "target": target,
        "automation": automation,
        "clips": [],
        "notes": [],
        "midi_events": [],
    }


def _normalize_automation_target(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    kind = str(raw.get("kind") or raw.get("type") or "track_volume").strip().lower()
    if kind == "time_signature_numerator":
        kind = "unassigned"
    elif kind not in {
        *TRACK_AUTOMATION_TARGET_KINDS,
        *GLOBAL_AUTOMATION_TARGET_KINDS,
        "unassigned",
    }:
        kind = "track_volume"
    target: dict[str, Any] = {"kind": kind}
    if kind in TRACK_AUTOMATION_TARGET_KINDS:
        target["track_id"] = _positive_int(raw.get("track_id"), 1)
    if kind == "plugin_parameter":
        target["slot_id"] = str(raw.get("slot_id") or "instrument").strip() or "instrument"
        target["param_index"] = _bounded_int(raw.get("param_index"), 0, 0, 2**31 - 1)
        if raw.get("param_id") not in (None, ""):
            target["param_id"] = _bounded_int(raw.get("param_id"), 0, 0, 2**31 - 1)
    label = str(raw.get("label") or raw.get("name") or _automation_target_default_label(target))
    if label:
        target["label"] = label
    return target


def _automation_target_default_label(target: dict[str, Any]) -> str:
    kind = target.get("kind")
    if kind == "unassigned":
        return "Unassigned"
    if kind == "tempo_bpm":
        return "Tempo BPM"
    if kind == "time_signature_numerator":
        return "Time Signature Numerator"
    if kind == "track_pan":
        return "Pan"
    if kind == "plugin_parameter":
        return f"Parameter {target.get('param_index', 0)}"
    return "Volume"


def _automation_bounds_for_target(target: dict[str, Any]) -> tuple[float, float, float]:
    kind = target.get("kind")
    if kind == "tempo_bpm":
        return (1.0, 999.0, 120.0)
    if kind == "time_signature_numerator":
        return (1.0, float(MAX_METER_NUMERATOR), 4.0)
    if kind == "track_pan":
        return (-1.0, 1.0, 0.0)
    if kind == "track_volume":
        return (0.0, 2.0, 0.8)
    return (0.0, 1.0, 0.0)


def _normalize_automation_payload(value: Any, *, target: dict[str, Any]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    minimum, maximum, default = _automation_bounds_for_target(target)
    value_min = _bounded_float(raw.get("value_min"), minimum, minimum, maximum)
    value_max = _bounded_float(raw.get("value_max"), maximum, minimum, maximum)
    if value_max < value_min:
        value_min, value_max = value_max, value_min
    default_value = _bounded_float(raw.get("default_value"), default, value_min, value_max)
    payload = {
        "value_min": value_min,
        "value_max": value_max,
        "default_value": default_value,
        "points": _normalize_automation_points(raw.get("points") or [], target=target),
    }
    return payload


def _normalize_automation_points(value: Any, *, target: dict[str, Any]) -> list[dict[str, Any]]:
    raw_points = value if isinstance(value, list) else []
    points: list[dict[str, Any]] = []
    for raw_point in raw_points:
        if isinstance(raw_point, dict):
            points.append(_normalize_automation_point(raw_point, target=target))
    by_beat: dict[float, dict[str, Any]] = {}
    for point in points:
        by_beat[round(float(point["beat"]), 6)] = point
    return [by_beat[beat] for beat in sorted(by_beat)]


def _normalize_automation_point(value: dict[str, Any], *, target: dict[str, Any]) -> dict[str, Any]:
    minimum, maximum, default = _automation_bounds_for_target(target)
    beat = _non_negative_float(_first_present(value, ("beat", "start"), default=0.0), 0.0)
    point_id = str(value.get("id") or f"pt_{uuid4().hex[:10]}")
    curve = str(value.get("curve") or "linear").strip().lower()
    if curve not in {"linear", "hold"}:
        curve = "linear"
    point_value = _bounded_float(value.get("value"), default, minimum, maximum)
    if target.get("kind") == "time_signature_numerator":
        point_value = float(round(point_value))
    point = {
        "id": point_id,
        "beat": round(beat, 6),
        "value": point_value,
        "curve": curve,
    }
    curve_amount = _normalized_curve_amount(value)
    if abs(curve_amount) > 1e-6:
        point["curve_amount"] = curve_amount
    return point


def _upsert_automation_point(
    points: list[dict[str, Any]],
    point: dict[str, Any],
) -> list[dict[str, Any]]:
    kept = [item for item in points if abs(float(item["beat"]) - float(point["beat"])) > 1e-6]
    return sorted([*kept, point], key=lambda item: float(item["beat"]))


def _update_automation_point(
    points: list[dict[str, Any]],
    point: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    updated = 0
    next_points = []
    for existing in points:
        same_id = point.get("id") and str(existing.get("id")) == str(point.get("id"))
        same_beat = abs(float(existing["beat"]) - float(point["beat"])) <= 1e-6
        if same_id or same_beat:
            next_points.append({**existing, **point, "id": existing.get("id") or point["id"]})
            updated += 1
        else:
            next_points.append(existing)
    if not updated:
        next_points.append(point)
    return (
        sorted(next_points, key=lambda item: float(item["beat"])),
        updated if updated else 1,
    )


def _delete_automation_point(
    points: list[dict[str, Any]],
    op: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    before = len(points)
    point_id = str(op.get("id") or op.get("point_id") or "")
    has_beat = "beat" in op or "start" in op
    beat = _non_negative_float(_first_present(op, ("beat", "start"), default=0.0), 0.0)
    kept = []
    for point in points:
        if point_id and str(point.get("id")) == point_id:
            continue
        if has_beat and abs(float(point["beat"]) - beat) <= 1e-6:
            continue
        kept.append(point)
    return kept, before - len(kept)


def _automation_target_status(project: dict[str, Any], target: dict[str, Any]) -> str:
    if target.get("kind") == "unassigned":
        return "unassigned"
    if target.get("kind") in GLOBAL_AUTOMATION_TARGET_KINDS:
        return "valid"
    try:
        target_track = track_lookup.find_track(project, int(target.get("track_id", -1)))
    except (TypeError, ValueError):
        return "missing"
    kind = target.get("kind")
    if kind in {"track_volume", "track_pan"}:
        return "valid"
    if kind == "plugin_parameter":
        slot_id = str(target.get("slot_id") or "instrument")
        slots = target_track.get("plugin_slots") if isinstance(target_track, dict) else []
        if not isinstance(slots, list):
            slots = []
        if slot_id == "instrument" and not slots:
            return "unvalidated"
        slot = next(
            (item for item in slots if isinstance(item, dict) and item.get("id") == slot_id),
            None,
        )
        if not slot or slot.get("type") == "empty":
            return "missing"
        return "unvalidated"
    return "missing"


def _automation_track_summary(
    project: dict[str, Any],
    track: dict[str, Any],
    *,
    include_points: bool,
) -> dict[str, Any]:
    points = track.get("automation", {}).get("points", [])
    beats = [float(point.get("beat", 0.0)) for point in points if isinstance(point, dict)]
    values = [float(point.get("value", 0.0)) for point in points if isinstance(point, dict)]
    row = {
        "id": track["id"],
        "name": track["name"],
        "color": track["color"],
        "mute": track["mute"],
        "target": track.get("target"),
        "target_status": _automation_target_status(project, track.get("target") or {}),
        "point_count": len(points),
        "beat_range": [min(beats), max(beats)] if beats else None,
        "value_range": [min(values), max(values)] if values else None,
    }
    if include_points:
        row["points"] = points
    return row


def _normalize_learned_parameters(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    learned: dict[str, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        try:
            item = _normalize_learned_parameter(raw)
        except ValueError:
            continue
        learned[item["id"]] = item
    return [learned[key] for key in sorted(learned)]


def _normalize_learned_parameter(value: dict[str, Any]) -> dict[str, Any]:
    target = _normalize_automation_target(value.get("target"))
    if target.get("kind") != "plugin_parameter":
        raise ValueError("learned automation parameter target must be a plugin parameter")
    raw_source = value.get("source")
    source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
    now = _now_iso()
    item_id = str(value.get("id") or _learned_parameter_id(target)).strip()
    name = str(value.get("name") or _learned_parameter_default_name(target, source)).strip()
    return {
        "id": item_id,
        "name": name or _learned_parameter_default_name(target, source),
        "target": target,
        "source": {
            "track_name": str(source.get("track_name") or ""),
            "slot_id": str(source.get("slot_id") or target.get("slot_id") or "instrument"),
            "slot_label": str(source.get("slot_label") or _slot_label(target.get("slot_id"))),
            "plugin_name": str(source.get("plugin_name") or ""),
            "param_name": str(source.get("param_name") or target.get("label") or ""),
            "units": str(source.get("units") or ""),
        },
        "last_value": _bounded_float(value.get("value", value.get("last_value")), 0.0, 0.0, 1.0),
        "created_at": str(value.get("created_at") or now),
        "last_captured_at": str(value.get("last_captured_at") or now),
    }


def _learned_parameter_id(target: dict[str, Any]) -> str:
    slot_id = str(target.get("slot_id") or "instrument")
    param_key = target.get("param_id", target.get("param_index", 0))
    raw = f"{target.get('track_id')}:{slot_id}:{param_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"learned_plugin_parameter_{digest}"


def _learned_parameter_default_name(target: dict[str, Any], source: dict[str, Any]) -> str:
    parts = [
        str(source.get("track_name") or f"Track {target.get('track_id')}").strip(),
        str(source.get("slot_label") or _slot_label(target.get("slot_id"))).strip(),
        str(source.get("plugin_name") or "Plugin").strip(),
        str(source.get("param_name") or target.get("label") or "Parameter").strip(),
    ]
    return " / ".join(part for part in parts if part)


def _slot_label(slot_id: Any) -> str:
    slot = str(slot_id or "instrument")
    if slot == "instrument":
        return "Instrument"
    if slot.startswith("insert_"):
        suffix = slot.removeprefix("insert_")
        return f"Insert {suffix}"
    return slot
