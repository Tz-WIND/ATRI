"""Compatibility facade for Music Studio project APIs.

New code should import focused modules under :mod:`core.music`. This module is
kept so existing tools, routes, and tests using ``core.music_project`` continue
to resolve the same API surface.
"""

from __future__ import annotations

from core.music import (
    automation_operations,
    clip_operations,
    midi_operations,
    piano_lane_operations,
    project_model,
    project_repository,
    track_lookup,
    track_operations,
)


def _reexport_project_model() -> None:
    for name, value in project_model.__dict__.items():
        if name.startswith("__"):
            continue
        if getattr(value, "__module__", None) == project_model.__name__:
            globals()[name] = value
        elif name.startswith("_") and callable(value):
            globals()[name] = value
        elif name.isupper() and isinstance(
            value,
            (str, int, float, tuple, list, set, frozenset),
        ):
            globals()[name] = value


_reexport_project_model()
del _reexport_project_model

TIME_SIGNATURE_AUTOMATION_ERROR = project_model.TIME_SIGNATURE_AUTOMATION_ERROR

default_project = project_model.default_project
normalize_project = project_model.normalize_project
project_summary = project_model.project_summary

_apply_batch_velocity_operation = project_model._apply_batch_velocity_operation
_apply_midi_event_curve = project_model._apply_midi_event_curve
_bounded_int = project_model._bounded_int
_ensure_midi_clip = project_model._ensure_midi_clip
_event_payload_from_op = project_model._event_payload_from_op
_normalize_automation_target = project_model._normalize_automation_target
_normalize_clip = project_model._normalize_clip
_normalize_meter_events = project_model._normalize_meter_events
_normalize_midi_event = project_model._normalize_midi_event
_normalize_note = project_model._normalize_note
_normalize_piano_lane_id = project_model._normalize_piano_lane_id
_normalize_plugin_slot = project_model._normalize_plugin_slot
_normalize_selection = project_model._normalize_selection

PROJECT_INDEX_PATH = project_repository.PROJECT_INDEX_PATH
PROJECT_PATH = project_repository.PROJECT_PATH
PROJECTS_DIR = project_repository.PROJECTS_DIR
active_project_archive_id = project_repository.active_project_archive_id
list_project_archives = project_repository.list_project_archives
load_project = project_repository.load_project
save_project = project_repository.save_project
save_project_as_archive = project_repository.save_project_as_archive
set_active_project_archive = project_repository.set_active_project_archive

create_track = track_operations.create_track
delete_track = track_operations.delete_track
find_track = track_lookup.find_track
import_audio_clip = track_operations.import_audio_clip
set_track_plugin = track_operations.set_track_plugin
update_track = track_operations.update_track

clip_diff = clip_operations.clip_diff

automation_write = automation_operations.automation_write
automation_diff = automation_operations.automation_diff
automation_retarget = automation_operations.automation_retarget
automation_query = automation_operations.automation_query
automation_learned_parameters_query = automation_operations.automation_learned_parameters_query
automation_learned_parameter_upsert = automation_operations.automation_learned_parameter_upsert
automation_learned_parameter_rename = automation_operations.automation_learned_parameter_rename

piano_lane_write = piano_lane_operations.piano_lane_write
piano_lane_diff = piano_lane_operations.piano_lane_diff

midi_write = midi_operations.midi_write
midi_diff = midi_operations.midi_diff
midi_batch_edit = midi_operations.midi_batch_edit
midi_query = midi_operations.midi_query
midi_inspect = midi_operations.midi_inspect

normalize_audio_waveform = project_model.normalize_audio_waveform
