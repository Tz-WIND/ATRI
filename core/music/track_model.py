"""Track, routing, and plugin-slot normalization helpers."""

from __future__ import annotations

from typing import Any

from core.music.model_constants import DEFAULT_TRACK_COLORS
from core.music.value_normalization import (
    _bounded_float,
    _nullable_non_negative_int,
)


def _track_color(value: Any, index: int) -> str:
    color = str(value or "").strip()
    if len(color) == 7 and color.startswith("#"):
        try:
            int(color[1:], 16)
            return color
        except ValueError:
            pass
    return DEFAULT_TRACK_COLORS[index % len(DEFAULT_TRACK_COLORS)]


def _normalize_track_type(track: dict[str, Any], *, clips: list[dict[str, Any]]) -> str:
    raw_type = str(track.get("type", track.get("track_type", "")) or "").strip().lower()
    if raw_type in {"instrument", "audio", "automation", "bus"}:
        return raw_type
    if str(track.get("instrument") or "").strip().lower() == "audio track":
        return "audio"
    if clips and all(clip.get("type") == "audio" for clip in clips):
        return "audio"
    return "instrument"


def _normalize_track_channel_type(value: Any, *, track_type: str) -> str:
    if track_type != "audio":
        return "multichannel"
    parsed = str(value or "").strip().lower().replace("-", "_")
    if parsed in {"mono", "monophonic"}:
        return "mono"
    if parsed in {"multi", "multichannel", "multi_channel", "stereo"}:
        return "multichannel"
    return "multichannel"


def _normalize_master_bus(bus: Any) -> dict[str, Any]:
    raw = bus if isinstance(bus, dict) else {}
    return {
        "host_track_id": _nullable_non_negative_int(raw.get("host_track_id")),
        "name": str(raw.get("name") or "Master Bus"),
        "color": _track_color(raw.get("color"), 5),
        "volume": _bounded_float(raw.get("volume"), 1.0, 0.0, 2.0),
        "pan": _bounded_float(raw.get("pan"), 0.0, -1.0, 1.0),
        "mute": bool(raw.get("mute", False)),
        "solo": bool(raw.get("solo", False)),
        "plugin_slots": _normalize_plugin_slots(raw, track_type="bus"),
    }


def _normalize_plugin_slots(
    track: dict[str, Any],
    *,
    track_type: str = "instrument",
) -> list[dict[str, Any]]:
    if track_type not in {"instrument", "bus"}:
        return []

    raw_slots = track.get("plugin_slots")
    slots: list[dict[str, Any]] = []
    if isinstance(raw_slots, list) and raw_slots:
        slot_map: dict[str, dict[str, Any]] = {}
        slot_order: list[str] = []
        for raw_slot in raw_slots:
            if not isinstance(raw_slot, dict):
                continue
            slot = _normalize_plugin_slot(raw_slot)
            if track_type == "bus" and slot["id"] == "instrument":
                continue
            if slot["id"] not in slot_map:
                slot_order.append(slot["id"])
            slot_map[slot["id"]] = slot
        slots = [slot_map[slot_id] for slot_id in slot_order]

    if track_type == "instrument" and not any(slot.get("id") == "instrument" for slot in slots):
        slots.insert(
            0,
            _normalize_plugin_slot(
                {
                    "type": "builtin",
                    "name": track.get("instrument") or "ATRI Basic Synth",
                },
                slot_id="instrument",
            ),
        )
    return _sort_plugin_slots(slots)


def _normalize_track_sends(track: dict[str, Any]) -> list[dict[str, Any]]:
    raw_sends = track.get("sends")
    if not isinstance(raw_sends, list):
        return []

    sends_by_target: dict[int, dict[str, Any]] = {}
    target_order: list[int] = []
    for raw_send in raw_sends:
        if not isinstance(raw_send, dict):
            continue
        target_bus_id = _nullable_non_negative_int(
            raw_send.get("target_bus_id", raw_send.get("target_track_id"))
        )
        if target_bus_id is None:
            continue
        send_id = str(raw_send.get("id") or f"send_{target_bus_id}").strip()
        if not send_id:
            send_id = f"send_{target_bus_id}"
        if target_bus_id not in sends_by_target:
            target_order.append(target_bus_id)
        sends_by_target[target_bus_id] = {
            "id": send_id,
            "target_bus_id": target_bus_id,
            "level": _bounded_float(raw_send.get("level"), 1.0, 0.0, 2.0),
            "enabled": bool(raw_send.get("enabled", True)),
        }
    return [sends_by_target[target_bus_id] for target_bus_id in target_order]


def _repair_output_bus_routing(tracks: list[dict[str, Any]]) -> None:
    bus_ids = {int(track["id"]) for track in tracks if track.get("type") == "bus"}

    for track in tracks:
        output_bus_id = track.get("output_bus_id")
        if output_bus_id is None:
            track["output_bus_id"] = None
            continue
        if int(output_bus_id) not in bus_ids or int(output_bus_id) == int(track["id"]):
            track["output_bus_id"] = None

    outputs: dict[int, int] = {}
    for track in tracks:
        output_bus_id = track.get("output_bus_id")
        if output_bus_id is not None:
            outputs[int(track["id"])] = int(output_bus_id)

    def has_cycle(start_id: int) -> bool:
        seen: set[int] = set()
        current_id = start_id
        while current_id in outputs:
            if current_id in seen:
                return True
            seen.add(current_id)
            current_id = outputs[current_id]
        return False

    for track in tracks:
        if has_cycle(int(track["id"])):
            track["output_bus_id"] = None

    _repair_track_sends(tracks)


def _repair_track_sends(tracks: list[dict[str, Any]]) -> None:
    bus_ids = {int(track["id"]) for track in tracks if track.get("type") == "bus"}
    outputs = {
        int(track["id"]): int(track["output_bus_id"])
        for track in tracks
        if track.get("output_bus_id") is not None
    }
    send_edges: dict[int, list[int]] = {int(track["id"]): [] for track in tracks}

    for track in tracks:
        source_id = int(track["id"])
        repaired: list[dict[str, Any]] = []
        seen_targets: set[int] = set()
        for send in track.get("sends", []):
            if not isinstance(send, dict):
                continue
            target_bus_id = _nullable_non_negative_int(send.get("target_bus_id"))
            if target_bus_id is None:
                continue
            if target_bus_id not in bus_ids or target_bus_id == source_id:
                continue
            if target_bus_id in seen_targets:
                continue
            if _route_reaches(
                target_bus_id,
                source_id,
                outputs=outputs,
                sends=send_edges,
            ):
                continue
            send["target_bus_id"] = target_bus_id
            repaired.append(send)
            send_edges[source_id].append(target_bus_id)
            seen_targets.add(target_bus_id)
        track["sends"] = repaired


def _route_reaches(
    start_id: int,
    wanted_id: int,
    *,
    outputs: dict[int, int],
    sends: dict[int, list[int]],
) -> bool:
    seen: set[int] = set()
    stack = [start_id]
    while stack:
        current_id = stack.pop()
        if current_id == wanted_id:
            return True
        if current_id in seen:
            continue
        seen.add(current_id)
        output_id = outputs.get(current_id)
        if output_id is not None:
            stack.append(output_id)
        stack.extend(sends.get(current_id, []))
    return False


def _normalize_plugin_slot(
    plugin: dict[str, Any] | None, *, slot_id: str = "instrument"
) -> dict[str, Any]:
    plugin = plugin if isinstance(plugin, dict) else {}
    slot_id = str(plugin.get("id") or slot_id or "instrument").strip() or "instrument"
    plugin_type = str(plugin.get("type") or plugin.get("format") or "builtin").lower()
    if plugin_type not in {"empty", "builtin", "vst3", "vst2"}:
        plugin_type = "builtin"
    if slot_id == "instrument" and plugin_type == "empty":
        plugin_type = "builtin"
    if slot_id != "instrument" and plugin_type == "builtin":
        plugin_type = "empty"

    if plugin_type == "empty":
        name = "Empty"
    elif plugin_type == "builtin":
        name = str(plugin.get("name") or "ATRI Basic Synth")
    else:
        name = str(plugin.get("name") or "Plugin")
    slot: dict[str, Any] = {
        "id": slot_id,
        "type": plugin_type,
        "name": name,
        "path": str(plugin.get("path") or ""),
        "dll_path": str(plugin.get("dll_path") or ""),
        "vendor": str(plugin.get("vendor") or ""),
        "category": str(plugin.get("category") or ""),
        "version": str(plugin.get("version") or ""),
    }
    state_b64 = str(plugin.get("state_b64") or "")
    if state_b64:
        slot["state_b64"] = state_b64
    return slot


def _sort_plugin_slots(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(slots, key=_plugin_slot_sort_key)


def _plugin_slot_sort_key(slot: dict[str, Any]) -> tuple[int, str]:
    slot_id = str(slot.get("id") or "")
    if slot_id == "instrument":
        return (0, slot_id)
    if slot_id.startswith("insert_"):
        try:
            return (100 + int(slot_id.removeprefix("insert_")), slot_id)
        except ValueError:
            return (199, slot_id)
    return (500, slot_id)
