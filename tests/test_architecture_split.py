import importlib
import importlib.util
import inspect

from core import music_project
from core.music import (
    automation_operations,
    clip_operations,
    midi_operations,
    model_constants,
    piano_lane_operations,
    project_model,
    project_repository,
    track_model,
    track_operations,
    value_normalization,
    waveform_model,
)
from dashboard import music as music_routes
from dashboard.studio import bridge_context, export_options


def _import_music_module(name: str):
    qualified_name = f"core.music.{name}"
    assert importlib.util.find_spec(qualified_name) is not None, f"{qualified_name} should exist"
    return importlib.import_module(qualified_name)


def test_music_project_archive_api_is_extracted_from_legacy_facade():
    assert music_project.load_project.__module__ == "core.music.project_repository"
    assert music_project.save_project.__module__ == "core.music.project_repository"
    assert music_project.list_project_archives.__module__ == "core.music.project_repository"


def test_music_project_model_api_is_extracted_from_legacy_facade():
    assert music_project.default_project.__module__ == "core.music.project_model"
    assert music_project.normalize_project.__module__ == "core.music.project_model"
    assert music_project.project_summary.__module__ == "core.music.project_model"


def test_waveform_model_api_is_extracted_from_legacy_facade():
    assert music_project.normalize_audio_waveform.__module__ == "core.music.waveform_model"
    assert project_model.normalize_audio_waveform is waveform_model.normalize_audio_waveform
    assert project_model._normalize_waveform is waveform_model._normalize_waveform


def test_project_model_uses_extracted_constants_and_value_helpers():
    assert project_model.DEFAULT_TRACK_COLORS is model_constants.DEFAULT_TRACK_COLORS
    assert project_model.MIDI_CURVE_MAX_POINTS == model_constants.MIDI_CURVE_MAX_POINTS
    assert project_model.PIANO_SUBTRACK_IDS is model_constants.PIANO_SUBTRACK_IDS

    assert project_model._bounded_int is value_normalization._bounded_int
    assert project_model._positive_float is value_normalization._positive_float
    assert project_model._non_negative_float is value_normalization._non_negative_float
    assert project_model._numeric_stats is value_normalization._numeric_stats
    assert music_project._bounded_int is value_normalization._bounded_int


def test_project_model_uses_extracted_track_model_helpers():
    assert project_model._track_color is track_model._track_color
    assert project_model._normalize_track_type is track_model._normalize_track_type
    assert project_model._normalize_plugin_slots is track_model._normalize_plugin_slots
    assert project_model._normalize_track_sends is track_model._normalize_track_sends
    assert project_model._repair_output_bus_routing is track_model._repair_output_bus_routing
    assert music_project._normalize_plugin_slot is track_model._normalize_plugin_slot


def test_project_model_uses_extracted_automation_model_helpers():
    automation_model = _import_music_module("automation_model")

    assert project_model._normalize_automation_target is (
        automation_model._normalize_automation_target
    )
    assert project_model._normalize_automation_payload is (
        automation_model._normalize_automation_payload
    )
    assert project_model._normalize_automation_point is (
        automation_model._normalize_automation_point
    )
    assert project_model._automation_track_summary is automation_model._automation_track_summary
    assert project_model._normalize_learned_parameter is (
        automation_model._normalize_learned_parameter
    )
    assert music_project._normalize_automation_target is (
        automation_model._normalize_automation_target
    )


def test_project_model_uses_extracted_meter_harmony_model_helpers():
    meter_harmony_model = _import_music_module("meter_harmony_model")

    assert project_model._normalize_meter is meter_harmony_model._normalize_meter
    assert project_model._normalize_meter_events is meter_harmony_model._normalize_meter_events
    assert project_model._normalize_harmony_events is (
        meter_harmony_model._normalize_harmony_events
    )
    assert project_model._normalize_piano_subtrack_order is (
        meter_harmony_model._normalize_piano_subtrack_order
    )
    assert music_project._normalize_meter_events is meter_harmony_model._normalize_meter_events


def test_project_model_uses_extracted_piano_lane_model_helpers():
    piano_lane_model = _import_music_module("piano_lane_model")

    assert project_model._normalize_piano_lane_id is piano_lane_model._normalize_piano_lane_id
    assert project_model._piano_lane_event_field is piano_lane_model._piano_lane_event_field
    assert project_model._normalize_piano_lane_events is (
        piano_lane_model._normalize_piano_lane_events
    )
    assert project_model._event_from_piano_lane_op is piano_lane_model._event_from_piano_lane_op
    assert project_model._upsert_piano_lane_event is piano_lane_model._upsert_piano_lane_event
    assert music_project._normalize_piano_lane_id is piano_lane_model._normalize_piano_lane_id


def test_project_model_uses_extracted_midi_clip_helpers():
    midi_clip_model = _import_music_module("midi_clip_model")

    assert project_model._ensure_midi_clip is midi_clip_model._ensure_midi_clip
    assert project_model._track_midi_clips is midi_clip_model._track_midi_clips
    assert project_model._update_midi_clip_duration is (midi_clip_model._update_midi_clip_duration)
    assert music_project._ensure_midi_clip is midi_clip_model._ensure_midi_clip


def test_project_model_uses_extracted_midi_event_model_helpers():
    midi_event_model = _import_music_module("midi_event_model")

    assert project_model._normalize_midi_event is midi_event_model._normalize_midi_event
    assert project_model._normalize_midi_event_type is midi_event_model._normalize_midi_event_type
    assert project_model._normalize_event_aliases is midi_event_model._normalize_event_aliases
    assert project_model._midi_event_sort_key is midi_event_model._midi_event_sort_key
    assert project_model._event_lane_summaries is midi_event_model._event_lane_summaries
    assert music_project._normalize_midi_event is midi_event_model._normalize_midi_event


def test_project_model_uses_extracted_midi_note_model_helpers():
    midi_note_model = _import_music_module("midi_note_model")

    assert project_model._normalize_note is midi_note_model._normalize_note
    assert project_model._find_note is midi_note_model._find_note
    assert project_model._note_matches is midi_note_model._note_matches
    assert music_project._normalize_note is midi_note_model._normalize_note


def test_project_model_uses_extracted_midi_selection_helpers():
    midi_selection = _import_music_module("midi_selection")

    assert project_model._normalize_selection is midi_selection._normalize_selection
    assert project_model._validate_midi_batch_write_scope is (
        midi_selection._validate_midi_batch_write_scope
    )
    assert project_model._selected_note_refs is midi_selection._selected_note_refs
    assert project_model._selected_event_refs is midi_selection._selected_event_refs
    assert project_model._selection_range is midi_selection._selection_range
    assert music_project._normalize_selection is midi_selection._normalize_selection


def test_project_model_uses_extracted_clip_model_helpers():
    clip_model = _import_music_module("clip_model")

    assert project_model._normalize_clips is clip_model._normalize_clips
    assert project_model._normalize_clip is clip_model._normalize_clip
    assert project_model._find_clip_record is clip_model._find_clip_record
    assert project_model._flatten_clip_notes is clip_model._flatten_clip_notes
    assert project_model._flatten_clip_midi_events is clip_model._flatten_clip_midi_events
    assert music_project._normalize_clip is clip_model._normalize_clip


def test_project_model_uses_extracted_midi_timeline_model_helpers():
    midi_timeline_model = _import_music_module("midi_timeline_model")

    assert project_model._target_clip_for_timeline_write is (
        midi_timeline_model._target_clip_for_timeline_write
    )
    assert project_model._event_payload_from_op is midi_timeline_model._event_payload_from_op
    assert project_model._find_timeline_note is midi_timeline_model._find_timeline_note
    assert project_model._find_timeline_event is midi_timeline_model._find_timeline_event
    assert project_model._payload_start_to_clip_local is (
        midi_timeline_model._payload_start_to_clip_local
    )
    assert music_project._event_payload_from_op is midi_timeline_model._event_payload_from_op


def test_project_model_uses_extracted_midi_curve_model_helpers():
    midi_curve_model = _import_music_module("midi_curve_model")

    assert project_model._apply_midi_event_curve is midi_curve_model._apply_midi_event_curve
    assert project_model._apply_velocity_curve is midi_curve_model._apply_velocity_curve
    assert project_model._curve_points is midi_curve_model._curve_points
    assert project_model._sample_beats_with_limit is midi_curve_model._sample_beats_with_limit
    assert project_model._interpolate_curve_value is midi_curve_model._interpolate_curve_value
    assert music_project._apply_midi_event_curve is midi_curve_model._apply_midi_event_curve


def test_project_model_uses_extracted_midi_batch_model_helpers():
    midi_batch_model = _import_music_module("midi_batch_model")

    assert project_model._apply_batch_velocity_operation is (
        midi_batch_model._apply_batch_velocity_operation
    )
    assert project_model._apply_batch_event_curve_operation is (
        midi_batch_model._apply_batch_event_curve_operation
    )
    assert project_model._apply_batch_event_clear is midi_batch_model._apply_batch_event_clear
    assert project_model._batch_curve_points_for_range is (
        midi_batch_model._batch_curve_points_for_range
    )
    assert project_model._shape_value is midi_batch_model._shape_value
    assert music_project._apply_batch_velocity_operation is (
        midi_batch_model._apply_batch_velocity_operation
    )


def test_music_modules_depend_on_project_model_not_legacy_facade():
    assert project_repository._project_model() is project_model
    assert track_operations._project_model() is project_model
    assert clip_operations._project_model() is project_model
    assert automation_operations._project_model() is project_model
    assert piano_lane_operations._project_model() is project_model
    assert midi_operations._project_model() is project_model


def test_music_project_track_api_is_extracted_from_legacy_facade():
    assert music_project.create_track.__module__ == "core.music.track_operations"
    assert music_project.update_track.__module__ == "core.music.track_operations"
    assert music_project.delete_track.__module__ == "core.music.track_operations"
    assert music_project.set_track_plugin.__module__ == "core.music.track_operations"


def test_track_lookup_is_shared_without_project_model_track_operations_cycle():
    track_lookup = importlib.import_module("core.music.track_lookup")

    assert project_model.find_track is track_lookup.find_track
    assert track_operations.find_track is track_lookup.find_track
    assert music_project.find_track is track_lookup.find_track


def test_music_project_clip_api_is_extracted_from_legacy_facade():
    assert music_project.clip_diff.__module__ == "core.music.clip_operations"


def test_music_project_automation_api_is_extracted_from_legacy_facade():
    assert music_project.automation_write.__module__ == "core.music.automation_operations"
    assert music_project.automation_diff.__module__ == "core.music.automation_operations"
    assert music_project.automation_retarget.__module__ == "core.music.automation_operations"
    assert music_project.automation_query.__module__ == "core.music.automation_operations"
    assert (
        music_project.automation_learned_parameters_query.__module__
        == "core.music.automation_operations"
    )


def test_music_project_piano_lane_api_is_extracted_from_legacy_facade():
    assert music_project.piano_lane_write.__module__ == "core.music.piano_lane_operations"
    assert music_project.piano_lane_diff.__module__ == "core.music.piano_lane_operations"


def test_music_project_midi_api_is_extracted_from_legacy_facade():
    assert music_project.midi_write.__module__ == "core.music.midi_operations"
    assert music_project.midi_diff.__module__ == "core.music.midi_operations"
    assert music_project.midi_batch_edit.__module__ == "core.music.midi_operations"
    assert music_project.midi_query.__module__ == "core.music.midi_operations"
    assert music_project.midi_inspect.__module__ == "core.music.midi_operations"


def test_dashboard_bridge_context_api_is_extracted_from_music_routes():
    assert music_routes.record_bridge_host_context.__module__ == "dashboard.studio.bridge_context"
    assert (
        music_routes.bridge_host_context_for_instance.__module__
        == "dashboard.studio.bridge_context"
    )


def test_dashboard_export_options_are_extracted_from_music_routes():
    assert music_routes._normalize_export_format.__module__ == "dashboard.studio.export_options"
    assert music_routes._export_time_range.__module__ == "dashboard.studio.export_options"
    assert music_routes.StudioExportError.__module__ == "dashboard.studio.export_options"


def test_dashboard_studio_modules_do_not_depend_on_music_route_facade():
    assert "dashboard.music" not in inspect.getsource(export_options)
    assert "dashboard.music" not in inspect.getsource(bridge_context)
