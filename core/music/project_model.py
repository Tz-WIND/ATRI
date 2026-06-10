"""Project data model and normalization helpers for Music Studio."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from core.music import track_lookup
from core.music.automation_model import (
    _automation_bounds_for_target,
    _automation_target_default_label,
    _automation_target_status,
    _automation_track_summary,
    _delete_automation_point,
    _learned_parameter_default_name,
    _learned_parameter_id,
    _new_automation_track,
    _normalize_automation_payload,
    _normalize_automation_point,
    _normalize_automation_points,
    _normalize_automation_target,
    _normalize_learned_parameter,
    _normalize_learned_parameters,
    _slot_label,
    _update_automation_point,
    _upsert_automation_point,
)
from core.music.clip_model import (
    _clip_id_from_op,
    _find_clip_record,
    _flatten_clip_midi_events,
    _flatten_clip_notes,
    _normalize_clip,
    _normalize_clips,
    _remove_clip_from_track,
    _target_track_for_clip_op,
)
from core.music.meter_harmony_model import (
    _normalize_harmony_events,
    _normalize_meter,
    _normalize_meter_denominator,
    _normalize_meter_events,
    _normalize_piano_subtrack_order,
)
from core.music.midi_batch_model import (
    _apply_batch_event_clear,
    _apply_batch_event_curve_operation,
    _apply_batch_velocity_operation,
    _batch_curve_points_for_range,
    _batch_event_curve_op,
    _batch_explicit_curve_points,
    _explicit_points_range,
    _operation_beat_range,
    _set_note_velocity,
    _shape_value,
)
from core.music.midi_clip_model import (
    _clip_overlaps_range,
    _ensure_midi_clip,
    _track_midi_clips,
    _update_midi_clip_duration,
)
from core.music.midi_curve_model import (
    _apply_midi_event_curve,
    _apply_velocity_curve,
    _curve_event_target,
    _curve_points,
    _curve_range,
    _curve_resolution,
    _curve_sample_beats,
    _curve_value_bounds,
    _curve_value_field,
    _event_matches_curve_target,
    _interpolate_curve_value,
    _sample_beats_with_limit,
    _sample_curve,
)
from core.music.midi_event_model import (
    _event_lane_key,
    _event_lane_summaries,
    _event_numeric_value,
    _event_type_from_payload,
    _midi_event_sort_key,
    _normalize_event_aliases,
    _normalize_midi_event,
    _normalize_midi_event_type,
    _normalize_sysex_b64,
    _normalized_curve_amount,
)
from core.music.midi_note_model import (
    _find_note,
    _normalize_note,
    _note_matches,
)
from core.music.midi_selection import (
    _clip_query_summary,
    _event_detail,
    _normalize_selection,
    _note_detail,
    _resolve_selection_track_ids,
    _selected_event_refs,
    _selected_midi_clips,
    _selected_note_refs,
    _selected_tracks,
    _selection_range,
    _selection_summary,
    _track_query_summary,
    _validate_midi_batch_write_scope,
)
from core.music.midi_timeline_model import (
    _absolute_to_clip_local,
    _clip_contains_beat,
    _curve_op_to_clip_local,
    _curve_point_to_clip_local,
    _delete_timeline_events,
    _delete_timeline_notes,
    _event_absolute_start,
    _event_match_criteria,
    _event_matches,
    _event_payload_from_op,
    _event_payload_to_clip_local,
    _find_event,
    _find_timeline_event,
    _find_timeline_note,
    _note_absolute_start,
    _note_payload_to_clip_local,
    _op_matches_clip,
    _payload_absolute_start,
    _payload_has_start,
    _payload_start_to_clip_local,
    _target_clip_for_timeline_write,
    _timeline_event_matches,
    _timeline_note_matches,
)
from core.music.model_constants import (
    DEFAULT_TRACK_COLORS,
    MIDI_CURVE_EVENT_TYPES,
    MIDI_CURVE_MAX_POINTS,
    MIDI_EVENT_OPERATION_NAMES,
    PIANO_SUBTRACK_IDS,
    TIME_SIGNATURE_AUTOMATION_ERROR,
)
from core.music.piano_lane_model import (
    _delete_piano_lane_event,
    _ensure_piano_subtrack_order,
    _event_from_piano_lane_op,
    _normalize_piano_lane_events,
    _normalize_piano_lane_id,
    _normalize_piano_lane_range,
    _piano_lane_beat_in_range,
    _piano_lane_event_beat,
    _piano_lane_event_field,
    _replace_piano_lane_event_range,
    _update_piano_lane_event,
    _upsert_piano_lane_event,
)
from core.music.track_model import (
    _normalize_master_bus,
    _normalize_plugin_slot,
    _normalize_plugin_slots,
    _normalize_track_channel_type,
    _normalize_track_sends,
    _normalize_track_type,
    _plugin_slot_sort_key,
    _repair_output_bus_routing,
    _repair_track_sends,
    _route_reaches,
    _sort_plugin_slots,
    _track_color,
)
from core.music.value_normalization import (
    _accent_matches,
    _as_int_list,
    _as_str_list,
    _beat_mod_matches,
    _beat_stats,
    _bounded_float,
    _bounded_int,
    _ceil_to_bar,
    _clamp_float,
    _finite_float,
    _first_present,
    _float_value,
    _int_range,
    _interpolate_scalar,
    _non_negative_float,
    _nullable_non_negative_int,
    _numeric_stats,
    _positive_float,
    _positive_int,
    _range_unit,
    _round_waveform_value,
    _stable_signed_amount,
)
from core.music.waveform_model import (
    _normalize_waveform,
    _normalize_waveform_metrics,
    _normalize_waveform_point,
    normalize_audio_waveform,
)

find_track = track_lookup.find_track

_REEXPORTED_CONSTANTS = (
    MIDI_CURVE_EVENT_TYPES,
    MIDI_CURVE_MAX_POINTS,
    MIDI_EVENT_OPERATION_NAMES,
    PIANO_SUBTRACK_IDS,
    TIME_SIGNATURE_AUTOMATION_ERROR,
)
_REEXPORTED_HELPERS = (
    _accent_matches,
    _absolute_to_clip_local,
    _apply_batch_event_clear,
    _apply_batch_event_curve_operation,
    _apply_batch_velocity_operation,
    _apply_midi_event_curve,
    _apply_velocity_curve,
    _automation_bounds_for_target,
    _automation_target_default_label,
    _automation_target_status,
    _automation_track_summary,
    _as_int_list,
    _as_str_list,
    _batch_curve_points_for_range,
    _batch_event_curve_op,
    _batch_explicit_curve_points,
    _beat_mod_matches,
    _beat_stats,
    _bounded_float,
    _bounded_int,
    _ceil_to_bar,
    _clamp_float,
    _clip_overlaps_range,
    _clip_contains_beat,
    _clip_id_from_op,
    _clip_query_summary,
    _curve_event_target,
    _curve_op_to_clip_local,
    _curve_point_to_clip_local,
    _curve_points,
    _curve_range,
    _curve_resolution,
    _curve_sample_beats,
    _curve_value_bounds,
    _curve_value_field,
    _delete_automation_point,
    _delete_piano_lane_event,
    _delete_timeline_events,
    _delete_timeline_notes,
    _ensure_piano_subtrack_order,
    _ensure_midi_clip,
    _event_absolute_start,
    _event_detail,
    _event_from_piano_lane_op,
    _event_lane_key,
    _event_lane_summaries,
    _event_match_criteria,
    _event_matches,
    _event_matches_curve_target,
    _event_numeric_value,
    _event_payload_from_op,
    _event_payload_to_clip_local,
    _event_type_from_payload,
    _explicit_points_range,
    _finite_float,
    _find_clip_record,
    _find_event,
    _find_note,
    _find_timeline_event,
    _find_timeline_note,
    _flatten_clip_midi_events,
    _flatten_clip_notes,
    _first_present,
    _float_value,
    _int_range,
    _interpolate_curve_value,
    _interpolate_scalar,
    _learned_parameter_default_name,
    _learned_parameter_id,
    _midi_event_sort_key,
    _new_automation_track,
    _note_absolute_start,
    _note_matches,
    _note_payload_to_clip_local,
    _non_negative_float,
    _nullable_non_negative_int,
    _normalize_automation_payload,
    _normalize_automation_point,
    _normalize_automation_points,
    _normalize_automation_target,
    _normalize_clip,
    _normalize_clips,
    _normalize_event_aliases,
    _normalize_harmony_events,
    _normalize_learned_parameter,
    _normalize_learned_parameters,
    _normalize_meter,
    _normalize_meter_denominator,
    _normalize_meter_events,
    _normalize_midi_event,
    _normalize_midi_event_type,
    _normalize_master_bus,
    _normalize_note,
    _normalize_piano_lane_events,
    _normalize_piano_lane_id,
    _normalize_piano_lane_range,
    _normalize_piano_subtrack_order,
    _normalize_selection,
    _normalize_sysex_b64,
    _normalize_plugin_slot,
    _normalize_plugin_slots,
    _normalize_track_channel_type,
    _normalize_track_sends,
    _normalize_track_type,
    _normalize_waveform,
    _normalize_waveform_metrics,
    _normalize_waveform_point,
    _numeric_stats,
    _op_matches_clip,
    _operation_beat_range,
    _payload_absolute_start,
    _payload_has_start,
    _payload_start_to_clip_local,
    _piano_lane_beat_in_range,
    _piano_lane_event_beat,
    _piano_lane_event_field,
    _plugin_slot_sort_key,
    _positive_float,
    _positive_int,
    _range_unit,
    _repair_output_bus_routing,
    _repair_track_sends,
    _replace_piano_lane_event_range,
    _resolve_selection_track_ids,
    _route_reaches,
    _remove_clip_from_track,
    _sample_beats_with_limit,
    _sample_curve,
    _selected_event_refs,
    _selected_midi_clips,
    _selected_note_refs,
    _selected_tracks,
    _selection_range,
    _selection_summary,
    _set_note_velocity,
    _shape_value,
    _slot_label,
    _round_waveform_value,
    _sort_plugin_slots,
    _stable_signed_amount,
    _target_clip_for_timeline_write,
    _target_track_for_clip_op,
    _timeline_event_matches,
    _timeline_note_matches,
    _track_color,
    _track_midi_clips,
    _track_query_summary,
    _update_automation_point,
    _update_midi_clip_duration,
    _update_piano_lane_event,
    _upsert_automation_point,
    _upsert_piano_lane_event,
    _validate_midi_batch_write_scope,
    _normalized_curve_amount,
    _note_detail,
    normalize_audio_waveform,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def default_project() -> dict[str, Any]:
    """Return a small playable starter project."""
    return {
        "version": 1,
        "title": "ATRI Session",
        "tempo": 120.0,
        "time_signature": [4, 4],
        "meter_events": [],
        "harmony_events": [],
        "piano_subtrack_order": [],
        "length_beats": 16.0,
        "updated_at": _now_iso(),
        "automation_learned_parameters": [],
        "master_bus": {
            "name": "Master Bus",
            "color": DEFAULT_TRACK_COLORS[5],
            "volume": 1.0,
            "pan": 0.0,
            "mute": False,
            "solo": False,
            "plugin_slots": [],
        },
        "tracks": [
            {
                "id": 1,
                "host_track_id": None,
                "name": "Impact Lead",
                "color": DEFAULT_TRACK_COLORS[0],
                "volume": 0.82,
                "pan": 0.0,
                "mute": False,
                "solo": False,
                "instrument": "ATRI Basic Synth",
                "notes": [
                    {"id": "demo_1", "pitch": 60, "start": 0.0, "duration": 0.75, "velocity": 92},
                    {"id": "demo_2", "pitch": 64, "start": 1.0, "duration": 0.75, "velocity": 86},
                    {"id": "demo_3", "pitch": 67, "start": 2.0, "duration": 1.0, "velocity": 94},
                    {"id": "demo_4", "pitch": 72, "start": 3.5, "duration": 0.5, "velocity": 88},
                    {"id": "demo_5", "pitch": 71, "start": 4.0, "duration": 0.75, "velocity": 82},
                    {"id": "demo_6", "pitch": 67, "start": 5.0, "duration": 0.75, "velocity": 88},
                    {"id": "demo_7", "pitch": 64, "start": 6.0, "duration": 1.0, "velocity": 84},
                    {"id": "demo_8", "pitch": 60, "start": 7.5, "duration": 0.5, "velocity": 90},
                ],
            },
            {
                "id": 2,
                "host_track_id": None,
                "name": "Sub Pulse",
                "color": DEFAULT_TRACK_COLORS[2],
                "volume": 0.7,
                "pan": -0.08,
                "mute": False,
                "solo": False,
                "instrument": "ATRI Basic Synth",
                "notes": [
                    {"id": "demo_b1", "pitch": 36, "start": 0.0, "duration": 1.5, "velocity": 78},
                    {"id": "demo_b2", "pitch": 36, "start": 2.0, "duration": 1.0, "velocity": 74},
                    {"id": "demo_b3", "pitch": 43, "start": 4.0, "duration": 1.5, "velocity": 82},
                    {"id": "demo_b4", "pitch": 41, "start": 6.0, "duration": 1.25, "velocity": 76},
                ],
            },
        ],
    }


def normalize_project(project: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(project, dict):
        project = {}

    base = default_project()
    normalized: dict[str, Any] = {
        "version": 1,
        "title": str(project.get("title") or base["title"]),
        "tempo": _positive_float(project.get("tempo"), base["tempo"]),
        "time_signature": _normalize_meter(project.get("time_signature")),
        "meter_events": _normalize_meter_events(project.get("meter_events")),
        "harmony_events": _normalize_harmony_events(project.get("harmony_events")),
        "piano_subtrack_order": _normalize_piano_subtrack_order(
            project.get("piano_subtrack_order")
        ),
        "length_beats": _positive_float(project.get("length_beats"), base["length_beats"]),
        "updated_at": str(project.get("updated_at") or _now_iso()),
        "automation_learned_parameters": _normalize_learned_parameters(
            project.get("automation_learned_parameters")
        ),
        "master_bus": _normalize_master_bus(project.get("master_bus")),
        "tracks": [],
    }

    raw_tracks = project.get("tracks")
    if not isinstance(raw_tracks, list):
        raw_tracks = deepcopy(base["tracks"])

    used_ids: set[int] = set()
    next_id = 1
    for index, raw_track in enumerate(raw_tracks):
        if not isinstance(raw_track, dict):
            continue
        track_id = _positive_int(raw_track.get("id"), next_id)
        while track_id in used_ids:
            track_id += 1
        used_ids.add(track_id)
        next_id = max(next_id, track_id + 1)

        track_color = _track_color(raw_track.get("color"), index)
        declared_type = (
            str(raw_track.get("type", raw_track.get("track_type", "")) or "").strip().lower()
        )
        if declared_type == "automation":
            clips: list[dict[str, Any]] = []
            notes: list[dict[str, Any]] = []
            midi_events: list[dict[str, Any]] = []
            track_type = "automation"
        else:
            legacy_notes = [
                _normalize_note(note)
                for note in raw_track.get("notes", [])
                if isinstance(note, dict)
            ]
            legacy_notes.sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
            clips = _normalize_clips(raw_track, legacy_notes=legacy_notes, track_color=track_color)
            notes = _flatten_clip_notes(clips)
            midi_events = _flatten_clip_midi_events(clips)
            track_type = _normalize_track_type(raw_track, clips=clips)

        normalized_track: dict[str, Any] = {
            "id": track_id,
            "host_track_id": None
            if track_type == "automation"
            else _nullable_non_negative_int(raw_track.get("host_track_id")),
            "type": track_type,
            "channel_type": _normalize_track_channel_type(
                raw_track.get("channel_type"),
                track_type=track_type,
            ),
            "name": str(raw_track.get("name") or f"Track {track_id}"),
            "color": track_color,
            "volume": _bounded_float(raw_track.get("volume"), 0.8, 0.0, 2.0),
            "pan": _bounded_float(raw_track.get("pan"), 0.0, -1.0, 1.0),
            "mute": bool(raw_track.get("mute", False)),
            "solo": bool(raw_track.get("solo", False)),
            "instrument": str(
                raw_track.get("instrument")
                or (
                    "Automation"
                    if track_type == "automation"
                    else "Bus"
                    if track_type == "bus"
                    else "Audio Track"
                    if track_type == "audio"
                    else "ATRI Basic Synth"
                )
            ),
            "plugin_slots": _normalize_plugin_slots(raw_track, track_type=track_type),
            "output_bus_id": _nullable_non_negative_int(raw_track.get("output_bus_id")),
            "sends": [] if track_type == "automation" else _normalize_track_sends(raw_track),
            "clips": clips,
            "notes": notes,
            "midi_events": midi_events,
        }
        if track_type == "automation":
            normalized_track["target"] = _normalize_automation_target(raw_track.get("target"))
            normalized_track["automation"] = _normalize_automation_payload(
                raw_track.get("automation"),
                target=normalized_track["target"],
            )
        normalized["tracks"].append(normalized_track)

    if not normalized["tracks"]:
        normalized["tracks"] = deepcopy(base["tracks"])

    _repair_output_bus_routing(normalized["tracks"])

    max_clip_end = max(
        (
            clip["start"] + clip["duration"]
            for track in normalized["tracks"]
            for clip in track["clips"]
        ),
        default=0.0,
    )
    max_automation_end = max(
        (
            point["beat"]
            for track in normalized["tracks"]
            if track.get("type") == "automation"
            for point in track.get("automation", {}).get("points", [])
            if isinstance(point, dict)
        ),
        default=0.0,
    )
    max_meter_event_end = max(
        (event["beat"] for event in normalized["meter_events"]),
        default=0.0,
    )
    max_harmony_event_end = max(
        (event["beat"] for event in normalized["harmony_events"]),
        default=0.0,
    )
    max_end = max(max_clip_end, max_automation_end, max_meter_event_end, max_harmony_event_end)
    normalized["length_beats"] = max(normalized["length_beats"], _ceil_to_bar(max_end))
    return normalized


def project_summary(project: dict[str, Any]) -> dict[str, Any]:
    project = normalize_project(project)
    note_count = sum(len(track["notes"]) for track in project["tracks"])
    midi_event_count = sum(len(track["midi_events"]) for track in project["tracks"])
    audio_clip_count = sum(
        1
        for track in project["tracks"]
        for clip in track.get("clips", [])
        if isinstance(clip, dict) and clip.get("type") == "audio"
    )
    return {
        "title": project["title"],
        "tempo": project["tempo"],
        "time_signature": project["time_signature"],
        "meter_events": project["meter_events"],
        "harmony_events": project["harmony_events"],
        "piano_subtrack_order": project["piano_subtrack_order"],
        "length_beats": project["length_beats"],
        "track_count": len(project["tracks"]),
        "note_count": note_count,
        "midi_event_count": midi_event_count,
        "audio_clip_count": audio_clip_count,
        "tracks": [
            {
                "id": track["id"],
                "name": track["name"],
                "type": track["type"],
                "channel_type": track["channel_type"],
                "notes": len(track["notes"]),
                "midi_events": len(track["midi_events"]),
                "clips": len(track.get("clips", [])),
                "audio_clips": sum(
                    1
                    for clip in track.get("clips", [])
                    if isinstance(clip, dict) and clip.get("type") == "audio"
                ),
                "instrument": track["instrument"],
                "plugin_slots": track.get("plugin_slots", []),
            }
            for track in project["tracks"]
        ],
    }


def _piano_lane_write_summary(
    project: dict[str, Any],
    lane: str,
    mode: str,
    events_written: int,
    events_removed: int,
) -> dict[str, Any]:
    event_count = len(project[_piano_lane_event_field(lane)])
    return {
        "lane": lane,
        "mode": mode,
        "events_written": events_written,
        "events_removed": events_removed,
        "event_count": event_count,
        "project": project_summary(project),
    }


def _piano_lane_diff_summary(
    project: dict[str, Any],
    lane: str,
    operation_count: int,
    changed: dict[str, int],
) -> dict[str, Any]:
    event_count = len(project[_piano_lane_event_field(lane)])
    return {
        "lane": lane,
        "operations": operation_count,
        **changed,
        "event_count": event_count,
        "project": project_summary(project),
    }
