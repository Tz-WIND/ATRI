"""Shared Music Studio project model constants."""

DEFAULT_TRACK_COLORS = ["#4e79ff", "#d95b55", "#5f916b", "#d7b66f", "#b489d6", "#58a7b8"]

MIDI_EVENT_OPERATION_NAMES = {
    "add_event",
    "add_midi_event",
    "delete_event",
    "delete_midi_event",
    "update_event",
    "modify_event",
    "update_midi_event",
    "modify_midi_event",
    "draw_event_curve",
    "set_event_curve",
    "replace_event_curve",
    "draw_controller_curve",
    "set_controller_curve",
    "cc_curve",
    "pitch_bend_curve",
    "aftertouch_curve",
    "channel_pressure_curve",
    "velocity_curve",
    "draw_velocity_curve",
    "set_velocity_curve",
}

MIDI_CURVE_EVENT_TYPES = {
    "control_change",
    "pitch_bend",
    "channel_pressure",
    "polyphonic_key_pressure",
}

TRACK_AUTOMATION_TARGET_KINDS = {"plugin_parameter", "track_volume", "track_pan"}
GLOBAL_AUTOMATION_TARGET_KINDS = {"tempo_bpm"}
TIME_SIGNATURE_AUTOMATION_ERROR = (
    "time_signature_numerator is not an automation target; "
    "use studio_piano_lane_write or studio_piano_lane_diff"
)

MIDI_CURVE_MAX_POINTS = 4096
METER_DENOMINATORS = {2, 4, 8, 16, 32}
MAX_METER_NUMERATOR = 255
PIANO_SUBTRACK_IDS = ("meter", "harmony")
