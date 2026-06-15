"""Projection helpers for syncing studio projects into the audio host."""

from __future__ import annotations

from itertools import pairwise
from typing import Any, TypedDict, cast

from core.music_project import find_track
from dashboard.studio.plugin_state import slot_index

CURVE_SAMPLE_STEP_BEATS = 1.0 / 64.0
CURVE_MAX_SAMPLES_PER_SEGMENT = 4096


class HostAutomationPoint(TypedDict):
    beat: float
    value: float
    curve: str
    curve_amount: Any


def is_automation_track(track: dict[str, Any]) -> bool:
    return str(track.get("type") or "").strip().lower() == "automation"


def host_track_id_for_project_target(
    project: dict[str, Any],
    target_track_id: Any,
) -> int | None:
    try:
        track = find_track(project, int(target_track_id))
    except (TypeError, ValueError):
        return None
    host_track_id = track.get("host_track_id")
    if host_track_id is None:
        return None
    return int(host_track_id)


def host_track_id_for_project_track(
    project: dict[str, Any],
    project_track_id: object,
) -> int | None:
    try:
        wanted = int(cast(Any, project_track_id))
    except (TypeError, ValueError):
        return None
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        try:
            track_id = int(cast(Any, track.get("id", -1)))
        except (TypeError, ValueError):
            continue
        if track_id != wanted:
            continue
        host_track_id = track.get("host_track_id")
        if host_track_id is None:
            return None
        return int(host_track_id)
    return None


def route_kind_for_host(track: dict[str, Any]) -> str:
    return "bus" if str(track.get("type") or "").strip().lower() == "bus" else "track"


def route_output_for_host(
    project: dict[str, Any],
    track: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    output_bus_id = track.get("output_bus_id")
    if output_bus_id is None:
        return None, None
    host_output_id = host_track_id_for_project_track(project, output_bus_id)
    if host_output_id is not None:
        return host_output_id, None
    return None, {
        "track_id": track.get("id"),
        "output_bus_id": output_bus_id,
        "reason": "output bus is not synced",
    }


def route_sends_for_host(
    project: dict[str, Any],
    track: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sends: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for send in track.get("sends", []):
        if not isinstance(send, dict):
            continue
        target_bus_id = send.get("target_bus_id")
        host_target_id = host_track_id_for_project_track(project, target_bus_id)
        if host_target_id is None:
            skipped.append(
                {
                    "track_id": track.get("id"),
                    "send_id": send.get("id"),
                    "target_bus_id": target_bus_id,
                    "reason": "send target bus is not synced",
                }
            )
            continue
        sends.append(
            {
                "target_track_id": host_target_id,
                "level": float(send.get("level", 1.0) or 0.0),
                "enabled": bool(send.get("enabled", True)),
            }
        )
    return sends, skipped


def master_bus_for_host(project: dict[str, Any]) -> dict[str, Any] | None:
    master_bus = project.get("master_bus")
    if not isinstance(master_bus, dict):
        return None
    master_bus["type"] = "bus"
    master_bus["name"] = str(master_bus.get("name") or "Master Bus")
    master_bus.setdefault("volume", 1.0)
    master_bus.setdefault("pan", 0.0)
    master_bus.setdefault("mute", False)
    master_bus.setdefault("solo", False)
    master_bus.setdefault("plugin_slots", [])
    master_bus.setdefault("notes", [])
    master_bus.setdefault("midi_events", [])
    master_bus.setdefault("clips", [])
    master_bus.setdefault("sends", [])
    return master_bus


def curve_amount(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not parsed or parsed != parsed:
        return 0.0
    return round(max(-1.0, min(1.0, parsed)), 6)


def curve_sample_beats(start: float, end: float) -> list[float]:
    if end <= start:
        return []
    step = CURVE_SAMPLE_STEP_BEATS
    estimated = int((end - start) / step)
    if estimated > CURVE_MAX_SAMPLES_PER_SEGMENT:
        step = (end - start) / CURVE_MAX_SAMPLES_PER_SEGMENT
    beats: list[float] = []
    beat = start + step
    while beat < end - 1e-9:
        beats.append(round(beat, 6))
        beat += step
    return beats


def curve_value(
    start_value: float,
    end_value: float,
    beat: float,
    start_beat: float,
    end_beat: float,
    amount: float,
    minimum: float,
    maximum: float,
) -> float:
    value_range = max(1e-9, maximum - minimum)
    position = max(0.0, min(1.0, (beat - start_beat) / max(1e-9, end_beat - start_beat)))
    start_unit = max(0.0, min(1.0, (start_value - minimum) / value_range))
    end_unit = max(0.0, min(1.0, (end_value - minimum) / value_range))
    linear_unit = start_unit + (end_unit - start_unit) * position
    bend = 4.0 * position * (1.0 - position) * curve_amount(amount)
    return minimum + max(0.0, min(1.0, linear_unit + bend)) * value_range


def midi_curve_lane_key(event: dict[str, Any]) -> tuple[Any, ...] | None:
    event_type = str(event.get("type") or event.get("kind") or "").strip().lower()
    event_type = event_type.replace("-", "_").replace(" ", "_")
    if event_type in {"cc", "controller"}:
        event_type = "control_change"
    elif event_type in {"pitchbend"}:
        event_type = "pitch_bend"
    elif event_type in {"aftertouch", "after_touch"}:
        event_type = "channel_pressure"
    if event_type == "control_change":
        return (event_type, int(event.get("channel", 0) or 0), int(event.get("controller", 0) or 0))
    if event_type in {"pitch_bend", "channel_pressure"}:
        return (event_type, int(event.get("channel", 0) or 0))
    if event_type == "polyphonic_key_pressure":
        return (
            event_type,
            int(event.get("channel", 0) or 0),
            int(event.get("pitch", 60) or 60),
        )
    return None


def midi_curve_value_field(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "").strip().lower()
    return "pressure" if event_type in {"channel_pressure", "polyphonic_key_pressure"} else "value"


def midi_curve_bounds(event: dict[str, Any]) -> tuple[float, float]:
    return (-8192.0, 8191.0) if str(event.get("type") or "") == "pitch_bend" else (0.0, 127.0)


def midi_curve_value(event: dict[str, Any]) -> float:
    field = midi_curve_value_field(event)
    return float(event.get(field, event.get("value", 0)) or 0)


HOST_MIDI_EVENT_KEYS = (
    "type",
    "start",
    "channel",
    "pitch",
    "velocity",
    "controller",
    "value",
    "program",
    "pressure",
    "data_b64",
)


def host_midi_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: event[key] for key in HOST_MIDI_EVENT_KEYS if key in event}


def midi_events_for_host(track: dict[str, Any]) -> list[dict[str, Any]]:
    events = [
        {**host_midi_event(event), "curve_amount": event.get("curve_amount")}
        for event in track.get("midi_events", [])
        if isinstance(event, dict)
    ]
    expanded = [host_midi_event(event) for event in events]
    lane_events: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for event in events:
        lane_key = midi_curve_lane_key(event)
        if lane_key is not None:
            lane_events.setdefault(lane_key, []).append(event)

    for lane in lane_events.values():
        lane.sort(key=lambda event: float(event.get("start", 0.0) or 0.0))
        occupied = {round(float(event.get("start", 0.0) or 0.0), 6) for event in lane}
        for left, right in pairwise(lane):
            start = float(left.get("start", 0.0) or 0.0)
            end = float(right.get("start", 0.0) or 0.0)
            amount = curve_amount(left.get("curve_amount"))
            if end <= start or (
                abs(midi_curve_value(left) - midi_curve_value(right)) < 1e-9 and abs(amount) < 1e-9
            ):
                continue
            minimum, maximum = midi_curve_bounds(left)
            value_field = midi_curve_value_field(left)
            for beat in curve_sample_beats(start, end):
                if beat in occupied:
                    continue
                value = round(
                    curve_value(
                        midi_curve_value(left),
                        midi_curve_value(right),
                        beat,
                        start,
                        end,
                        amount,
                        minimum,
                        maximum,
                    )
                )
                sampled = host_midi_event(left)
                sampled["start"] = beat
                sampled[value_field] = int(max(minimum, min(maximum, value)))
                expanded.append(sampled)

    return sorted(
        expanded,
        key=lambda event: (
            float(event.get("start", 0.0) or 0.0),
            str(event.get("type") or ""),
            int(event.get("controller", event.get("pitch", -1)) or -1),
        ),
    )


def automation_points_for_host(track: dict[str, Any]) -> list[dict[str, Any]]:
    automation = track.get("automation", {})
    value_min = float(automation.get("value_min", 0.0) or 0.0)
    value_max = float(automation.get("value_max", 1.0) or 1.0)
    if value_max < value_min:
        value_min, value_max = value_max, value_min
    raw_points: list[HostAutomationPoint] = [
        {
            "beat": float(point.get("beat", 0.0) or 0.0),
            "value": float(point.get("value", 0.0) or 0.0),
            "curve": str(point.get("curve") or "linear"),
            "curve_amount": point.get("curve_amount"),
        }
        for point in automation.get("points", [])
        if isinstance(point, dict)
    ]
    raw_points.sort(key=lambda point: point["beat"])
    points: list[dict[str, Any]] = [
        {"beat": point["beat"], "value": point["value"], "curve": point["curve"]}
        for point in raw_points
    ]
    for left, right in pairwise(raw_points):
        amount = curve_amount(left.get("curve_amount"))
        if abs(amount) < 1e-9 or left["curve"] == "hold" or right["beat"] <= left["beat"]:
            continue
        for beat in curve_sample_beats(left["beat"], right["beat"]):
            points.append(
                {
                    "beat": beat,
                    "value": round(
                        curve_value(
                            left["value"],
                            right["value"],
                            beat,
                            left["beat"],
                            right["beat"],
                            amount,
                            value_min,
                            value_max,
                        ),
                        6,
                    ),
                    "curve": "linear",
                }
            )
    return sorted(points, key=lambda point: point["beat"])


def automation_lanes_for_host(
    project: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lanes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict) or not is_automation_track(track):
            continue
        target_payload = track.get("target")
        target: dict[str, Any] = target_payload if isinstance(target_payload, dict) else {}
        kind = str(target.get("kind") or "")
        host_target: dict[str, Any]
        if kind == "tempo_bpm":
            host_target = {"kind": kind}
        else:
            host_track_id = host_track_id_for_project_target(project, target.get("track_id"))
            if host_track_id is None:
                skipped.append(
                    {"track_id": track.get("id"), "reason": "target track is not synced"}
                )
                continue
            if kind == "plugin_parameter":
                host_target = {
                    "kind": "plugin_parameter",
                    "track_id": host_track_id,
                    "slot_index": slot_index(str(target.get("slot_id") or "instrument")),
                    "param_index": int(target.get("param_index", 0) or 0),
                }
            elif kind in {"track_volume", "track_pan"}:
                host_target = {"kind": kind, "track_id": host_track_id}
            else:
                skipped.append(
                    {"track_id": track.get("id"), "reason": "unsupported automation target"}
                )
                continue
        lanes.append(
            {
                "target": host_target,
                "points": automation_points_for_host(track),
                "muted": bool(track.get("mute", False)),
            }
        )
    return lanes, skipped
