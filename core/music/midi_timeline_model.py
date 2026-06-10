"""MIDI timeline-to-clip conversion and lookup helpers."""

from __future__ import annotations

from typing import Any, cast

from core.music.midi_clip_model import _ensure_midi_clip, _track_midi_clips
from core.music.midi_event_model import (
    _event_type_from_payload,
    _normalize_event_aliases,
)
from core.music.model_constants import MIDI_EVENT_OPERATION_NAMES
from core.music.value_normalization import (
    _first_present,
    _non_negative_float,
)


def _target_clip_for_timeline_write(
    track: dict[str, Any],
    payload: dict[str, Any],
    *,
    create: bool = False,
) -> dict[str, Any]:
    clip_id = payload.get("clip_id")
    if clip_id:
        for clip in _track_midi_clips(track):
            if str(clip.get("id")) == str(clip_id):
                return clip
        raise ValueError(f"MIDI clip {clip_id} not found on track {track.get('id')}")

    absolute_start = _payload_absolute_start(payload)
    if absolute_start is not None:
        for clip in _track_midi_clips(track):
            if _clip_contains_beat(clip, absolute_start):
                return clip

    clips = _track_midi_clips(track)
    if clips:
        return clips[0]
    if create:
        return _ensure_midi_clip(track)
    raise ValueError(f"track {track.get('id')} has no MIDI clip")


def _note_payload_to_clip_local(note: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    payload = dict(note)
    if _payload_has_start(payload):
        payload["start"] = _payload_start_to_clip_local(payload, clip)
    return payload


def _event_payload_to_clip_local(event: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event)
    if _payload_has_start(payload):
        payload["start"] = _payload_start_to_clip_local(payload, clip)
        payload.pop("beat", None)
        payload.pop("local_start", None)
    return payload


def _curve_op_to_clip_local(op: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    local_op = dict(op)
    if "range" in local_op and isinstance(local_op["range"], (list, tuple)):
        raw_range = local_op["range"]
        if len(raw_range) >= 2:
            local_op["start"] = raw_range[0]
            local_op["end"] = raw_range[1]

    if "start" in local_op or "beat" in local_op or "local_start" in local_op:
        local_op["start"] = _payload_start_to_clip_local(local_op, clip)
        local_op.pop("beat", None)
        local_op.pop("local_start", None)
    if "end" in local_op:
        local_op["end"] = _absolute_to_clip_local(float(local_op["end"]), clip)

    for key in ("points", "curve"):
        if isinstance(local_op.get(key), list):
            local_op[key] = [_curve_point_to_clip_local(point, clip) for point in local_op[key]]
    return local_op


def _curve_point_to_clip_local(point: Any, clip: dict[str, Any]) -> Any:
    if isinstance(point, dict):
        local_point = dict(point)
        if "local_start" in local_point:
            local_point["start"] = _non_negative_float(local_point["local_start"], 0.0)
            local_point.pop("local_start", None)
        elif "start" in local_point or "beat" in local_point:
            local_point["start"] = _payload_start_to_clip_local(local_point, clip)
            local_point.pop("beat", None)
        return local_point
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        return [_absolute_to_clip_local(float(point[0]), clip), *list(point[1:])]
    return point


def _find_timeline_note(
    track: dict[str, Any],
    op: dict[str, Any],
) -> dict[str, Any] | None:
    for clip in _track_midi_clips(track):
        for note in clip.get("notes", []):
            if isinstance(note, dict) and _timeline_note_matches(note, op, clip):
                return {"clip": clip, "note": note}
    return None


def _delete_timeline_notes(track: dict[str, Any], op: dict[str, Any]) -> int:
    deleted = 0
    for clip in _track_midi_clips(track):
        before = len(clip.get("notes", []))
        clip["notes"] = [
            note
            for note in clip.get("notes", [])
            if not (isinstance(note, dict) and _timeline_note_matches(note, op, clip))
        ]
        deleted += before - len(clip["notes"])
    return deleted


def _timeline_note_matches(note: dict[str, Any], op: dict[str, Any], clip: dict[str, Any]) -> bool:
    if not _op_matches_clip(op, clip):
        return False
    note_id = op.get("id") or op.get("note_id")
    if note_id:
        return bool(note.get("id") == note_id)

    criteria_seen = False
    if "pitch" in op:
        criteria_seen = True
        if int(note["pitch"]) != int(op["pitch"]):
            return False
    if "local_start" in op:
        criteria_seen = True
        if abs(float(note["start"]) - float(op["local_start"])) > 1e-6:
            return False
    elif "start" in op or "beat" in op:
        criteria_seen = True
        absolute_start = float(_first_present(op, ("start", "beat")))
        if abs(_note_absolute_start(note, clip) - absolute_start) > 1e-6:
            return False
    return criteria_seen


def _find_timeline_event(
    track: dict[str, Any],
    op: dict[str, Any],
) -> dict[str, Any] | None:
    for clip in _track_midi_clips(track):
        for event in clip.get("events", []):
            if isinstance(event, dict) and _timeline_event_matches(event, op, clip):
                return {"clip": clip, "event": event}
    return None


def _delete_timeline_events(track: dict[str, Any], op: dict[str, Any]) -> int:
    deleted = 0
    for clip in _track_midi_clips(track):
        before = len(clip.get("events", []))
        clip["events"] = [
            event
            for event in clip.get("events", [])
            if not (isinstance(event, dict) and _timeline_event_matches(event, op, clip))
        ]
        deleted += before - len(clip["events"])
    return deleted


def _timeline_event_matches(
    event: dict[str, Any],
    op: dict[str, Any],
    clip: dict[str, Any],
) -> bool:
    if not _op_matches_clip(op, clip):
        return False
    event_id = op.get("event_id") or op.get("id")
    if event_id:
        return bool(event.get("id") == event_id)

    criteria = _event_match_criteria(op)
    criteria_seen = False

    event_type = _event_type_from_payload(criteria)
    if event_type:
        criteria_seen = True
        if str(event.get("type") or "") != event_type:
            return False

    if "local_start" in criteria:
        criteria_seen = True
        if abs(float(event.get("start", 0.0) or 0.0) - float(criteria["local_start"])) > 1e-6:
            return False
    else:
        beat = _first_present(criteria, ("start", "beat"))
        if beat is not None:
            criteria_seen = True
            if abs(_event_absolute_start(event, clip) - float(beat)) > 1e-6:
                return False

    for key in ("channel", "pitch", "controller"):
        if key in criteria:
            criteria_seen = True
            if int(event.get(key, -1)) != int(criteria[key]):
                return False

    return criteria_seen


def _op_matches_clip(op: dict[str, Any], clip: dict[str, Any]) -> bool:
    clip_id = op.get("clip_id")
    return not clip_id or str(clip.get("id")) == str(clip_id)


def _payload_has_start(payload: dict[str, Any]) -> bool:
    return "local_start" in payload or "start" in payload or "beat" in payload


def _payload_absolute_start(payload: dict[str, Any]) -> float | None:
    if "local_start" in payload:
        return None
    raw_start = _first_present(payload, ("start", "beat"))
    if raw_start is None:
        return None
    return _non_negative_float(raw_start, 0.0)


def _payload_start_to_clip_local(payload: dict[str, Any], clip: dict[str, Any]) -> float:
    if "local_start" in payload:
        return _non_negative_float(payload["local_start"], 0.0)
    raw_start = _first_present(payload, ("start", "beat"), default=0.0)
    return _absolute_to_clip_local(float(raw_start), clip)


def _absolute_to_clip_local(absolute: float, clip: dict[str, Any]) -> float:
    clip_start = float(clip.get("start", 0.0) or 0.0)
    if absolute < clip_start - 1e-6:
        raise ValueError(
            f"absolute beat {absolute:g} is before MIDI clip {clip.get('id')} start {clip_start:g}"
        )
    return round(max(0.0, absolute - clip_start), 6)


def _clip_contains_beat(clip: dict[str, Any], beat: float) -> bool:
    clip_start = float(clip.get("start", 0.0) or 0.0)
    clip_end = clip_start + float(clip.get("duration", 0.0) or 0.0)
    return clip_start - 1e-6 <= beat <= clip_end + 1e-6


def _note_absolute_start(note: dict[str, Any], clip: dict[str, Any]) -> float:
    return float(clip.get("start", 0.0) or 0.0) + float(note.get("start", 0.0) or 0.0)


def _event_absolute_start(event: dict[str, Any], clip: dict[str, Any]) -> float:
    return float(clip.get("start", 0.0) or 0.0) + float(event.get("start", 0.0) or 0.0)


def _find_event(container: dict[str, Any], op: dict[str, Any]) -> dict[str, Any] | None:
    raw_events = container.get("events", [])
    events = raw_events if isinstance(raw_events, list) else []
    for raw_event in events:
        if not isinstance(raw_event, dict):
            continue
        event = cast(dict[str, Any], raw_event)
        if _event_matches(event, op):
            return event
    return None


def _event_matches(event: dict[str, Any], op: dict[str, Any]) -> bool:
    event_id = op.get("event_id") or op.get("id")
    if event_id:
        return bool(event.get("id") == event_id)

    criteria = _event_match_criteria(op)
    criteria_seen = False

    event_type = _event_type_from_payload(criteria)
    if event_type:
        criteria_seen = True
        if str(event.get("type") or "") != event_type:
            return False

    beat = _first_present(criteria, ("start", "beat"))
    if beat is not None:
        criteria_seen = True
        if abs(float(event.get("start", 0.0) or 0.0) - float(beat)) > 1e-6:
            return False

    for key in ("channel", "pitch", "controller"):
        if key in criteria:
            criteria_seen = True
            if int(event.get(key, -1)) != int(criteria[key]):
                return False

    return criteria_seen


def _event_match_criteria(op: dict[str, Any]) -> dict[str, Any]:
    target = op.get("target")
    criteria: dict[str, Any] = dict(target) if isinstance(target, dict) else {}
    for key in (
        "type",
        "event_type",
        "kind",
        "message",
        "start",
        "beat",
        "local_start",
        "channel",
        "pitch",
        "controller",
        "cc",
    ):
        if key in op:
            criteria[key] = op[key]
    return _normalize_event_aliases(criteria)


def _event_payload_from_op(
    op: dict[str, Any],
    *,
    include_identity: bool = True,
) -> dict[str, Any]:
    raw_event = op.get("event")
    payload: dict[str, Any] = dict(raw_event) if isinstance(raw_event, dict) else {}

    for key in (
        "clip_id",
        "start",
        "beat",
        "local_start",
        "channel",
        "pitch",
        "velocity",
        "controller",
        "cc",
        "value",
        "program",
        "pressure",
        "data_b64",
        "data",
        "bytes",
    ):
        if key in op:
            payload[key] = op[key]

    explicit_type = _first_present(op, ("event_type", "kind", "message"))
    if explicit_type is not None:
        payload["type"] = explicit_type
    elif "type" in op:
        raw_type = str(op["type"]).strip().lower()
        if raw_type not in MIDI_EVENT_OPERATION_NAMES:
            payload["type"] = op["type"]

    if include_identity:
        if "id" in op:
            payload["id"] = op["id"]
    else:
        payload.pop("id", None)
        payload.pop("event_id", None)

    return _normalize_event_aliases(payload)
