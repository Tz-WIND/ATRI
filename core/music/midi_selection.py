"""MIDI selection, filtering, and query shape helpers."""

from __future__ import annotations

from typing import Any

from core.music import track_lookup
from core.music.midi_clip_model import (
    _clip_overlaps_range,
    _ensure_midi_clip,
)
from core.music.midi_event_model import (
    _event_lane_summaries,
    _midi_event_sort_key,
    _normalize_event_aliases,
    _normalize_midi_event_type,
)
from core.music.value_normalization import (
    _as_int_list,
    _as_str_list,
    _bounded_int,
    _int_range,
    _non_negative_float,
    _numeric_stats,
)


def _normalize_selection(
    project: dict[str, Any],
    selection: Any = None,
    *,
    track_id: int | None = None,
    base: dict[str, Any] | None = None,
    op: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = dict(base or {})
    if isinstance(selection, dict):
        raw.update({key: value for key, value in selection.items() if value is not None})
    if track_id is not None:
        raw["track_ids"] = [track_id]
    if op:
        for key in (
            "track_id",
            "track_ids",
            "clip_id",
            "clip_ids",
            "note_ids",
            "event_ids",
            "pitch_range",
            "controllers",
            "event_types",
            "channel",
        ):
            if key in op:
                raw[key] = op[key]
        if "range" in op:
            raw["range"] = op["range"]
        elif "start" in op or "end" in op:
            start, end = _selection_range(raw) or (0.0, project.get("length_beats", 0.0))
            raw["range"] = [
                _non_negative_float(op.get("start"), start),
                _non_negative_float(op.get("end"), end),
            ]

    has_track_filter = any(key in raw for key in ("track_id", "track_ids", "tracks"))
    track_ids = _as_int_list(raw.get("track_ids", raw.get("tracks")))
    if "track_id" in raw:
        track_ids.append(_bounded_int(raw.get("track_id"), 1, 0, 2**31 - 1))
    if has_track_filter:
        raw["track_ids"] = _resolve_selection_track_ids(project, track_ids)

    clip_ids = _as_str_list(raw.get("clip_ids", raw.get("clips")))
    if "clip_id" in raw:
        clip_ids.append(str(raw["clip_id"]))
    if clip_ids:
        raw["clip_ids"] = sorted(set(clip_ids))

    note_ids = _as_str_list(raw.get("note_ids", raw.get("notes")))
    if "note_id" in raw:
        note_ids.append(str(raw["note_id"]))
    if note_ids:
        raw["note_ids"] = sorted(set(note_ids))

    event_ids = _as_str_list(raw.get("event_ids", raw.get("events")))
    if "event_id" in raw:
        event_ids.append(str(raw["event_id"]))
    if event_ids:
        raw["event_ids"] = sorted(set(event_ids))

    controllers = _as_int_list(raw.get("controllers"))
    raw = _normalize_event_aliases(raw)
    if "controller" in raw:
        controllers.append(_bounded_int(raw["controller"], 0, 0, 127))
    if controllers:
        raw["controllers"] = sorted(set(_bounded_int(value, 0, 0, 127) for value in controllers))

    event_types = [
        _normalize_midi_event_type(value) for value in _as_str_list(raw.get("event_types"))
    ]
    if "event_type" in raw:
        event_types.append(_normalize_midi_event_type(raw["event_type"]))
    if event_types:
        raw["event_types"] = sorted(set(event_types))

    if "range" in raw:
        start, end = _selection_range(raw) or (0.0, 0.0)
        raw["range"] = [start, max(start, end)]
    if "pitch_range" in raw:
        raw["pitch_range"] = _int_range(raw["pitch_range"], 0, 127)
    if "channel" in raw:
        raw["channel"] = _bounded_int(raw["channel"], 0, 0, 15)
    if bool(raw.get("all_tracks")):
        raw["all_tracks"] = True
    return raw


def _validate_midi_batch_write_scope(
    selection: dict[str, Any] | None,
    *,
    track_id: int | None,
    all_tracks: bool,
) -> None:
    """Require explicit write scope so batch edits cannot silently hit every track."""
    raw_selection = selection if isinstance(selection, dict) else {}
    has_track_scope = track_id is not None or bool(
        raw_selection.get("track_ids") or raw_selection.get("track_id")
    )
    has_all_tracks_scope = bool(all_tracks or raw_selection.get("all_tracks"))
    if not has_track_scope and not has_all_tracks_scope:
        raise ValueError(
            "midi_batch_edit requires an explicit write scope: provide track_id, "
            "selection.track_ids, or all_tracks=true"
        )


def _selection_summary(selection: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "all_tracks",
        "track_ids",
        "clip_ids",
        "range",
        "pitch_range",
        "note_ids",
        "event_ids",
        "controllers",
        "event_types",
        "channel",
    ):
        if key in selection:
            summary[key] = selection[key]
    return summary


def _resolve_selection_track_ids(project: dict[str, Any], track_ids: list[int]) -> list[int]:
    resolved = []
    for requested_track_id in track_ids:
        try:
            resolved.append(int(track_lookup.find_track(project, requested_track_id)["id"]))
        except ValueError:
            continue
    return sorted(set(resolved))


def _selected_tracks(project: dict[str, Any], selection: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tracks = project.get("tracks", [])
    tracks = [track for track in raw_tracks if isinstance(track, dict)]
    if "track_ids" not in selection:
        return tracks
    track_ids = set(_as_int_list(selection.get("track_ids")))
    return [track for track in tracks if int(track.get("id", -1)) in track_ids]


def _selected_midi_clips(
    project: dict[str, Any],
    selection: dict[str, Any],
    *,
    create: bool = False,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    selected = []
    clip_ids = set(_as_str_list(selection.get("clip_ids")))
    beat_range = _selection_range(selection)
    for track in _selected_tracks(project, selection):
        clips = [
            clip
            for clip in track.get("clips", [])
            if isinstance(clip, dict) and clip.get("type") == "midi"
        ]
        if create and not clips and not clip_ids:
            clips = [_ensure_midi_clip(track)]
        for clip in clips:
            if clip_ids and str(clip.get("id")) not in clip_ids:
                continue
            if beat_range and not _clip_overlaps_range(clip, beat_range):
                continue
            selected.append((track, clip))

    if create and not selected and not clip_ids:
        for track in _selected_tracks(project, selection):
            selected.append((track, _ensure_midi_clip(track)))
    return selected


def _selected_note_refs(project: dict[str, Any], selection: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    beat_range = _selection_range(selection)
    note_ids = set(_as_str_list(selection.get("note_ids")))
    pitch_range = selection.get("pitch_range")
    for track, clip in _selected_midi_clips(project, selection):
        clip_start = float(clip.get("start", 0.0) or 0.0)
        for note in clip.get("notes", []):
            if not isinstance(note, dict):
                continue
            absolute_start = clip_start + float(note.get("start", 0.0) or 0.0)
            absolute_end = absolute_start + float(note.get("duration", 0.0) or 0.0)
            if note_ids and str(note.get("id")) not in note_ids:
                continue
            if pitch_range and not (
                int(pitch_range[0]) <= int(note["pitch"]) <= int(pitch_range[1])
            ):
                continue
            if beat_range and not (beat_range[0] - 1e-6 <= absolute_start <= beat_range[1] + 1e-6):
                continue
            refs.append(
                {
                    "track": track,
                    "clip": clip,
                    "note": note,
                    "absolute_start": absolute_start,
                    "absolute_end": absolute_end,
                }
            )
    return sorted(
        refs, key=lambda ref: (ref["absolute_start"], ref["note"]["pitch"], ref["note"]["id"])
    )


def _selected_event_refs(
    project: dict[str, Any], selection: dict[str, Any]
) -> list[dict[str, Any]]:
    refs = []
    beat_range = _selection_range(selection)
    event_ids = set(_as_str_list(selection.get("event_ids")))
    event_types = set(_as_str_list(selection.get("event_types")))
    controllers = set(_as_int_list(selection.get("controllers")))
    channel = selection.get("channel")
    for track, clip in _selected_midi_clips(project, selection):
        clip_start = float(clip.get("start", 0.0) or 0.0)
        for event in clip.get("events", []):
            if not isinstance(event, dict):
                continue
            absolute_start = clip_start + float(event.get("start", 0.0) or 0.0)
            event_type = str(event.get("type") or "")
            if event_ids and str(event.get("id")) not in event_ids:
                continue
            if event_types and event_type not in event_types:
                continue
            if controllers and int(event.get("controller", -1)) not in controllers:
                continue
            if channel is not None and int(event.get("channel", -1)) != int(channel):
                continue
            if beat_range and not (beat_range[0] - 1e-6 <= absolute_start <= beat_range[1] + 1e-6):
                continue
            refs.append(
                {
                    "track": track,
                    "clip": clip,
                    "event": event,
                    "absolute_start": absolute_start,
                }
            )
    return sorted(
        refs, key=lambda ref: _midi_event_sort_key({**ref["event"], "start": ref["absolute_start"]})
    )


def _track_query_summary(track: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": track["id"],
        "host_track_id": track.get("host_track_id"),
        "name": track["name"],
        "instrument": track["instrument"],
        "clips": len(track.get("clips", [])),
        "notes": len(track.get("notes", [])),
        "midi_events": len(track.get("midi_events", [])),
        "volume": track.get("volume"),
        "pan": track.get("pan"),
        "mute": track.get("mute"),
        "solo": track.get("solo"),
    }


def _clip_query_summary(track: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    notes = [note for note in clip.get("notes", []) if isinstance(note, dict)]
    events = [event for event in clip.get("events", []) if isinstance(event, dict)]
    return {
        "track_id": track["id"],
        "track_name": track["name"],
        "id": clip["id"],
        "name": clip["name"],
        "start": clip["start"],
        "duration": clip["duration"],
        "notes": len(notes),
        "midi_events": len(events),
        "velocity": _numeric_stats([note["velocity"] for note in notes]),
        "lanes": _event_lane_summaries(
            [{"event": event, "absolute_start": clip["start"] + event["start"]} for event in events]
        ),
    }


def _note_detail(ref: dict[str, Any]) -> dict[str, Any]:
    note = ref["note"]
    track = ref["track"]
    clip = ref["clip"]
    return {
        "kind": "note",
        "track_id": track["id"],
        "track_name": track["name"],
        "clip_id": clip["id"],
        "clip_name": clip["name"],
        "id": note["id"],
        "pitch": note["pitch"],
        "start": round(float(ref["absolute_start"]), 6),
        "local_start": note["start"],
        "duration": note["duration"],
        "end": round(float(ref["absolute_end"]), 6),
        "velocity": note["velocity"],
    }


def _event_detail(ref: dict[str, Any]) -> dict[str, Any]:
    event = ref["event"]
    track = ref["track"]
    clip = ref["clip"]
    payload = {
        key: event[key]
        for key in (
            "channel",
            "pitch",
            "velocity",
            "controller",
            "value",
            "program",
            "pressure",
            "data_b64",
        )
        if key in event
    }
    return {
        "kind": "event",
        "track_id": track["id"],
        "track_name": track["name"],
        "clip_id": clip["id"],
        "clip_name": clip["name"],
        "id": event["id"],
        "type": event["type"],
        "start": round(float(ref["absolute_start"]), 6),
        "local_start": event["start"],
        **payload,
    }


def _selection_range(selection: dict[str, Any]) -> tuple[float, float] | None:
    raw_range = selection.get("range")
    if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 2:
        start = _non_negative_float(raw_range[0], 0.0)
        end = _non_negative_float(raw_range[1], start)
        return (start, max(start, end))
    if "start" in selection or "end" in selection:
        start = _non_negative_float(selection.get("start"), 0.0)
        end = _non_negative_float(selection.get("end"), start)
        return (start, max(start, end))
    return None
