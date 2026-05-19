import pytest

from core.music_project import (
    automation_diff,
    automation_learned_parameter_rename,
    automation_learned_parameter_upsert,
    automation_learned_parameters_query,
    automation_query,
    automation_retarget,
    automation_write,
    create_track,
    delete_track,
    import_audio_clip,
    load_project,
    midi_batch_edit,
    midi_diff,
    midi_inspect,
    midi_query,
    midi_write,
    project_summary,
    save_project,
    set_track_plugin,
)


def test_delete_track_removes_requested_track(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()
    project, created = create_track("Rack Split")

    project, deleted = delete_track(created["id"])

    assert deleted["id"] == created["id"]
    assert all(track["id"] != created["id"] for track in project["tracks"])
    assert project_summary(project)["track_count"] == 2


def test_delete_track_rejects_last_remaining_track(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project({"tracks": [{"id": 1, "name": "Only Track"}]})

    with pytest.raises(ValueError, match="cannot delete the last track"):
        delete_track(1)


def test_create_track_supports_instrument_and_audio_track_types(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    _, instrument_track = create_track("Playable", track_type="instrument")
    _, audio_track = create_track("Mic In", track_type="audio", channel_type="mono")

    assert instrument_track["type"] == "instrument"
    assert instrument_track["channel_type"] == "multichannel"
    assert instrument_track["plugin_slots"][0]["id"] == "instrument"
    assert audio_track["type"] == "audio"
    assert audio_track["channel_type"] == "mono"
    assert audio_track["plugin_slots"] == []


def test_automation_write_creates_first_class_automation_track(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    project, summary = automation_write(
        {
            "kind": "track_volume",
            "track_id": 1,
            "label": "Impact Lead Volume",
        },
        points=[
            {"beat": 4, "value": 1.1},
            {"beat": 0, "value": 0.4},
            {"beat": 4, "value": 0.9},
        ],
        name="Impact Lead Volume",
    )

    automation_track = project["tracks"][-1]
    assert summary["created"] is True
    assert automation_track["type"] == "automation"
    assert automation_track["host_track_id"] is None
    assert automation_track["target"] == {
        "kind": "track_volume",
        "track_id": 1,
        "label": "Impact Lead Volume",
    }
    assert automation_track["clips"] == []
    assert automation_track["notes"] == []
    assert automation_track["midi_events"] == []
    point_pairs = [
        (point["beat"], point["value"]) for point in automation_track["automation"]["points"]
    ]
    assert point_pairs == [
        (0.0, 0.4),
        (4.0, 0.9),
    ]


def test_automation_write_accepts_global_tempo_and_meter_targets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    project, tempo_summary = automation_write(
        {"kind": "tempo_bpm"},
        points=[{"beat": 0, "value": 90}, {"beat": 4, "value": 132}],
        name="Tempo BPM",
    )
    tempo_track = project["tracks"][-1]

    assert tempo_summary["target_status"] == "valid"
    assert tempo_track["target"] == {"kind": "tempo_bpm", "label": "Tempo BPM"}
    assert tempo_track["automation"]["value_min"] == 1.0
    assert tempo_track["automation"]["value_max"] == 999.0
    assert [(point["beat"], point["value"]) for point in tempo_track["automation"]["points"]] == [
        (0.0, 90.0),
        (4.0, 132.0),
    ]

    project, meter_summary = automation_write(
        {"kind": "time_signature_numerator"},
        points=[{"beat": 0, "value": 3.2}, {"beat": 8, "value": 7.6}],
        name="Time Signature Numerator",
    )
    meter_track = project["tracks"][-1]

    assert meter_summary["target_status"] == "valid"
    assert meter_track["target"] == {
        "kind": "time_signature_numerator",
        "label": "Time Signature Numerator",
    }
    assert meter_track["automation"]["value_min"] == 1.0
    assert meter_track["automation"]["value_max"] == 255.0
    assert [(point["beat"], point["value"]) for point in meter_track["automation"]["points"]] == [
        (0.0, 3.0),
        (8.0, 8.0),
    ]


def test_automation_learned_parameters_upsert_and_rename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    project, first = automation_learned_parameter_upsert(
        {
            "target": {
                "kind": "plugin_parameter",
                "track_id": 1,
                "slot_id": "instrument",
                "param_index": 3,
                "param_id": 740,
                "label": "Cutoff",
            },
            "source": {
                "track_name": "Lead",
                "slot_label": "Instrument",
                "plugin_name": "Serum",
                "param_name": "Cutoff",
            },
            "value": 0.37,
        }
    )

    learned_id = first["id"]
    learned = project["automation_learned_parameters"][0]
    assert learned["id"] == learned_id
    assert learned["name"] == "Lead / Instrument / Serum / Cutoff"
    assert learned["target"]["param_id"] == 740
    assert learned["last_value"] == 0.37

    project, renamed = automation_learned_parameter_rename(learned_id, "Filter Cutoff")
    assert renamed["name"] == "Filter Cutoff"

    project, second = automation_learned_parameter_upsert(
        {
            "target": {
                "kind": "plugin_parameter",
                "track_id": 1,
                "slot_id": "instrument",
                "param_index": 3,
                "param_id": 740,
                "label": "Cutoff",
            },
            "source": {
                "track_name": "Lead",
                "slot_label": "Instrument",
                "plugin_name": "Serum",
                "param_name": "Cutoff",
            },
            "value": 0.61,
        }
    )

    assert second["id"] == learned_id
    assert len(project["automation_learned_parameters"]) == 1
    assert project["automation_learned_parameters"][0]["name"] == "Filter Cutoff"
    assert project["automation_learned_parameters"][0]["last_value"] == 0.61
    assert automation_learned_parameters_query()["items"][0]["id"] == learned_id


def test_automation_diff_and_retarget_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project, summary = automation_write(
        {"kind": "track_pan", "track_id": 1, "label": "Pan"},
        points=[{"beat": 0, "value": -0.5}, {"beat": 8, "value": 0.5}],
    )
    automation_track_id = summary["track_id"]

    project, diff = automation_diff(
        automation_track_id,
        [
            {"op": "update_point", "beat": 8, "value": 0.25},
            {"op": "add_point", "beat": 4, "value": 0.0},
            {"op": "delete_point", "beat": 0},
        ],
    )

    track = next(track for track in project["tracks"] if track["id"] == automation_track_id)
    assert diff == {
        "track_id": automation_track_id,
        "operations": 3,
        "added": 1,
        "updated": 1,
        "deleted": 1,
    }
    assert [(point["beat"], point["value"]) for point in track["automation"]["points"]] == [
        (4.0, 0.0),
        (8.0, 0.25),
    ]

    project, retarget = automation_retarget(
        automation_track_id,
        {
            "kind": "plugin_parameter",
            "track_id": 1,
            "slot_id": "instrument",
            "param_index": 12,
            "param_id": 345,
            "label": "Cutoff",
        },
    )

    track = next(track for track in project["tracks"] if track["id"] == automation_track_id)
    assert retarget["target"]["kind"] == "plugin_parameter"
    assert track["automation"]["value_min"] == 0.0
    assert track["automation"]["value_max"] == 1.0
    assert track["target"]["param_index"] == 12


def test_automation_query_reports_missing_targets_without_deleting_tracks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = save_project(
        {
            "tracks": [
                {"id": 1, "name": "Lead", "type": "instrument"},
                {
                    "id": 2,
                    "name": "Missing Plugin Cutoff",
                    "type": "automation",
                    "target": {
                        "kind": "plugin_parameter",
                        "track_id": 1,
                        "slot_id": "insert_3",
                        "param_index": 7,
                        "label": "Cutoff",
                    },
                    "automation": {"points": [{"beat": 0, "value": 0.2}]},
                },
            ]
        }
    )

    result = automation_query(include_points=True)

    assert len(project["tracks"]) == 2
    assert result["automation_track_count"] == 1
    assert result["tracks"][0]["id"] == 2
    assert result["tracks"][0]["target_status"] == "missing"
    assert result["tracks"][0]["points"] == [
        {
            "id": result["tracks"][0]["points"][0]["id"],
            "beat": 0.0,
            "value": 0.2,
            "curve": "linear",
        }
    ]


def test_project_accepts_free_time_signature_numerator_and_limited_denominator(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    project = save_project({"time_signature": [37, 16]}, tmp_path / "free.json")

    assert project["time_signature"] == [37, 16]

    project = save_project({"time_signature": [9, 3]}, tmp_path / "limited.json")

    assert project["time_signature"] == [9, 4]


def test_project_flattens_clip_midi_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    project = save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Controller Lane",
                    "clips": [
                        {
                            "id": "midi_1",
                            "type": "midi",
                            "start": 4,
                            "duration": 2,
                            "events": [
                                {
                                    "id": "cc1",
                                    "type": "cc",
                                    "start": 0.5,
                                    "channel": 2,
                                    "controller": 74,
                                    "value": 96,
                                },
                                {
                                    "id": "bend1",
                                    "type": "pitch_bend",
                                    "start": 1.0,
                                    "value": -200,
                                },
                                {
                                    "id": "sx1",
                                    "type": "sysex",
                                    "start": 1.5,
                                    "data": [240, 126, 247],
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    )

    events = project["tracks"][0]["midi_events"]
    assert [event["start"] for event in events] == [4.5, 5.0, 5.5]
    assert events[0]["type"] == "control_change"
    assert events[0]["channel"] == 2
    assert events[0]["controller"] == 74
    assert events[1]["type"] == "pitch_bend"
    assert events[1]["value"] == -200
    assert events[2]["data_b64"] == "8H73"


def test_midi_write_replaces_overlapping_notes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = load_project()
    original_count = len(project["tracks"][0]["notes"])

    project, summary = midi_write(
        1,
        [
            {"pitch": 65, "start": 0.0, "duration": 1.0, "velocity": 100},
            {"pitch": 68, "start": 1.0, "duration": 1.0, "velocity": 96},
        ],
        start=0,
        end=2,
    )

    notes = project["tracks"][0]["notes"]
    assert summary["notes_added"] == 2
    assert summary["notes_removed"] < original_count
    assert [note["pitch"] for note in notes if note["start"] < 2] == [65, 68]
    assert project_summary(project)["note_count"] >= 2


def test_midi_diff_adds_updates_and_deletes_notes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    project, summary = midi_diff(
        1,
        [
            {
                "op": "add_note",
                "note": {"id": "test_note", "pitch": 60, "start": 8, "duration": 1, "velocity": 80},
            },
            {"op": "update_note", "id": "test_note", "pitch": 62, "velocity": 91},
        ],
    )

    note = next(note for note in project["tracks"][0]["notes"] if note["id"] == "test_note")
    assert note["pitch"] == 62
    assert note["velocity"] == 91
    assert summary["added"] == 1
    assert summary["updated"] == 1

    project, summary = midi_diff(1, [{"op": "delete_note", "id": "test_note"}])

    assert all(note["id"] != "test_note" for note in project["tracks"][0]["notes"])
    assert summary["deleted"] == 1


def test_midi_diff_adds_updates_and_deletes_midi_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    project, summary = midi_diff(
        1,
        [
            {
                "op": "add_event",
                "event": {
                    "id": "cc74_a",
                    "type": "cc",
                    "start": 1,
                    "channel": 1,
                    "controller": 74,
                    "value": 32,
                },
            },
            {"op": "update_event", "event_id": "cc74_a", "value": 96},
        ],
    )

    event = next(event for event in project["tracks"][0]["midi_events"] if event["id"] == "cc74_a")
    assert event["type"] == "control_change"
    assert event["channel"] == 1
    assert event["controller"] == 74
    assert event["value"] == 96
    assert summary["events_added"] == 1
    assert summary["events_updated"] == 1
    assert summary["track_midi_event_count"] == 1

    project, summary = midi_diff(1, [{"op": "delete_event", "event_id": "cc74_a"}])

    assert all(event["id"] != "cc74_a" for event in project["tracks"][0]["midi_events"])
    assert summary["events_deleted"] == 1


def test_midi_event_cc_alias_works_for_normalize_diff_match_and_query(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    project = save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "CC Alias",
                    "clips": [
                        {
                            "id": "clip_cc",
                            "type": "midi",
                            "start": 0,
                            "duration": 4,
                            "events": [
                                {
                                    "id": "saved_cc",
                                    "type": "cc",
                                    "start": 0.5,
                                    "cc": 21,
                                    "value": 44,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )
    assert project["tracks"][0]["midi_events"][0]["controller"] == 21

    project, summary = midi_diff(
        1,
        [
            {
                "op": "add_event",
                "event": {"id": "alias_cc", "type": "cc", "start": 1.0, "cc": 74, "value": 32},
            },
            {"op": "update_event", "event_id": "alias_cc", "cc": 75, "value": 96},
        ],
    )

    event = next(
        event for event in project["tracks"][0]["midi_events"] if event["id"] == "alias_cc"
    )
    assert event["controller"] == 75
    assert event["value"] == 96
    assert summary["events_added"] == 1
    assert summary["events_updated"] == 1

    queried = midi_query(track_id=1, selection={"cc": 75}, include=["events"])
    assert queried["selected"]["midi_event_count"] == 1
    assert queried["events"]["lanes"][0]["id"] == "cc:75:ch0"

    project, summary = midi_diff(1, [{"op": "delete_event", "type": "cc", "cc": 75}])

    assert summary["events_deleted"] == 1
    assert all(event["id"] != "alias_cc" for event in project["tracks"][0]["midi_events"])


def test_midi_diff_draws_controller_curve_and_replaces_matching_range(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    project, summary = midi_diff(
        1,
        [
            {
                "op": "cc_curve",
                "controller": 1,
                "channel": 0,
                "start": 0,
                "end": 1,
                "start_value": 0,
                "end_value": 100,
                "resolution": 0.5,
            }
        ],
    )

    events = [
        event
        for event in project["tracks"][0]["midi_events"]
        if event["type"] == "control_change" and event["controller"] == 1
    ]
    assert [event["start"] for event in events] == [0, 0.5, 1]
    assert [event["value"] for event in events] == [0, 50, 100]
    assert summary["events_added"] == 3
    assert summary["curves_written"] == 1

    project, summary = midi_diff(
        1,
        [
            {
                "op": "cc_curve",
                "controller": 1,
                "channel": 0,
                "start": 0.5,
                "end": 1,
                "points": [[0.5, 64], [1, 127]],
                "resolution": 0.5,
            }
        ],
    )

    events = [
        event
        for event in project["tracks"][0]["midi_events"]
        if event["type"] == "control_change" and event["controller"] == 1
    ]
    assert [event["start"] for event in events] == [0, 0.5, 1]
    assert [event["value"] for event in events] == [0, 64, 127]
    assert summary["events_deleted"] == 2
    assert summary["events_added"] == 2


def test_midi_diff_applies_velocity_curve_to_notes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    midi_write(
        1,
        [
            {"id": "v1", "pitch": 60, "start": 0, "duration": 0.5, "velocity": 80},
            {"id": "v2", "pitch": 62, "start": 1, "duration": 0.5, "velocity": 80},
            {"id": "v3", "pitch": 64, "start": 2, "duration": 0.5, "velocity": 80},
        ],
        start=0,
        end=3,
    )

    project, summary = midi_diff(
        1,
        [
            {
                "op": "velocity_curve",
                "start": 0,
                "end": 2,
                "points": [[0, 40], [2, 100]],
            }
        ],
    )

    notes = [note for note in project["tracks"][0]["notes"] if note["id"] in {"v1", "v2", "v3"}]
    assert [note["velocity"] for note in notes] == [40, 70, 100]
    assert summary["updated"] == 3
    assert summary["curves_written"] == 1


def test_midi_batch_edit_applies_velocity_shape_and_humanize(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    midi_write(
        1,
        [
            {"id": "bv1", "pitch": 60, "start": 0, "duration": 0.5, "velocity": 80},
            {"id": "bv2", "pitch": 62, "start": 1, "duration": 0.5, "velocity": 80},
            {"id": "bv3", "pitch": 64, "start": 2, "duration": 0.5, "velocity": 80},
        ],
        start=0,
        end=3,
    )

    project, summary = midi_batch_edit(
        [
            {
                "op": "velocity_shape",
                "shape": "crescendo",
                "range": [0, 2],
                "min": 50,
                "max": 100,
            },
            {
                "op": "velocity_humanize",
                "range": [0, 2],
                "amount": 0,
                "seed": "fixed",
            },
        ],
        track_id=1,
    )

    notes = [note for note in project["tracks"][0]["notes"] if note["id"] in {"bv1", "bv2", "bv3"}]
    assert [note["velocity"] for note in notes] == [50, 75, 100]
    assert summary["notes_updated"] == 3
    assert summary["details"][0]["op"] == "velocity_shape"


def test_midi_batch_edit_requires_explicit_write_scope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    with pytest.raises(ValueError, match="requires an explicit write scope"):
        midi_batch_edit([{"op": "velocity_set", "velocity": 64}])

    with pytest.raises(ValueError, match="did not match any project tracks"):
        midi_batch_edit([{"op": "velocity_set", "velocity": 64}], track_id=999)


def test_invalid_explicit_track_selection_does_not_fall_back_to_all_tracks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = load_project()
    before_velocities = [note["velocity"] for track in project["tracks"] for note in track["notes"]]

    query = midi_query(track_id=999, include=["notes"])
    assert query["selected"]["track_count"] == 0
    assert query["selected"]["note_count"] == 0

    project, summary = midi_batch_edit(
        [
            {
                "op": "velocity_set",
                "velocity": 64,
                "selection": {"track_ids": [999]},
            }
        ],
        track_id=1,
    )

    assert summary["details"][0]["notes_updated"] == 0
    after_velocities = [note["velocity"] for track in project["tracks"] for note in track["notes"]]
    assert after_velocities == before_velocities


def test_midi_batch_edit_all_tracks_requires_explicit_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = load_project()
    original_count = sum(len(track["notes"]) for track in project["tracks"])

    project, summary = midi_batch_edit(
        [{"op": "velocity_set", "velocity": 64}],
        all_tracks=True,
    )

    assert summary["selection"]["all_tracks"] is True
    assert summary["notes_updated"] == original_count
    assert {note["velocity"] for track in project["tracks"] for note in track["notes"]} == {64}


def test_midi_batch_edit_draws_expression_curve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    project, summary = midi_batch_edit(
        [
            {
                "op": "expression_curve",
                "range": [0, 1],
                "shape": "swell",
                "min": 20,
                "max": 100,
                "resolution": 0.5,
            }
        ],
        track_id=1,
    )

    events = [
        event
        for event in project["tracks"][0]["midi_events"]
        if event["type"] == "control_change" and event["controller"] == 11
    ]
    assert [event["start"] for event in events] == [0, 0.5, 1]
    assert [event["value"] for event in events] == [20, 100, 20]
    assert summary["events_added"] == 3
    assert summary["curves_written"] == 1


def test_midi_batch_edit_preserves_explicit_curve_points_without_range(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Long Clip",
                    "clips": [
                        {
                            "id": "clip_long",
                            "type": "midi",
                            "start": 0,
                            "duration": 8,
                            "events": [],
                        }
                    ],
                }
            ]
        }
    )

    project, summary = midi_batch_edit(
        [
            {
                "op": "cc_curve",
                "cc": 74,
                "points": [[0, 0], [0.5, 64], [1, 127]],
                "resolution": 0,
            }
        ],
        track_id=1,
    )

    events = [
        event
        for event in project["tracks"][0]["midi_events"]
        if event["type"] == "control_change" and event["controller"] == 74
    ]
    assert [event["start"] for event in events] == [0, 0.5, 1]
    assert [event["value"] for event in events] == [0, 64, 127]
    assert summary["events_added"] == 3


def test_midi_batch_edit_rejects_curve_resolution_that_generates_too_many_points(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    load_project()

    with pytest.raises(ValueError, match="too many points"):
        midi_batch_edit(
            [
                {
                    "op": "cc_curve",
                    "cc": 74,
                    "range": [0, 1],
                    "from": 0,
                    "to": 127,
                    "resolution": 0.000001,
                }
            ],
            track_id=1,
        )


def test_midi_diff_rejects_curve_resolution_that_generates_too_many_points(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    with pytest.raises(ValueError, match="too many points"):
        midi_diff(
            1,
            [
                {
                    "op": "cc_curve",
                    "cc": 74,
                    "start": 0,
                    "end": 1,
                    "from": 0,
                    "to": 127,
                    "resolution": 0.000001,
                }
            ],
        )


def test_midi_batch_edit_explicit_curve_points_use_their_own_range_for_replace(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Long Clip",
                    "clips": [
                        {
                            "id": "clip_long",
                            "type": "midi",
                            "start": 0,
                            "duration": 8,
                            "events": [
                                {
                                    "id": "before",
                                    "type": "cc",
                                    "start": 4,
                                    "controller": 74,
                                    "value": 12,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    project, summary = midi_batch_edit(
        [
            {
                "op": "cc_curve",
                "cc": 74,
                "points": [[0, 0], [0.5, 64], [1, 127]],
                "resolution": 0,
            }
        ],
        track_id=1,
    )

    events = [
        event
        for event in project["tracks"][0]["midi_events"]
        if event["type"] == "control_change" and event["controller"] == 74
    ]
    assert [event["start"] for event in events] == [0, 0.5, 1, 4]
    assert [event["value"] for event in events] == [0, 64, 127, 12]
    assert summary["events_deleted"] == 0


def test_midi_query_summarizes_selected_velocity_and_controller_lanes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    midi_write(
        1,
        [
            {"id": "q1", "pitch": 60, "start": 0, "duration": 0.5, "velocity": 40},
            {"id": "q2", "pitch": 62, "start": 1, "duration": 0.5, "velocity": 100},
        ],
        start=0,
        end=2,
    )
    midi_batch_edit(
        [
            {
                "op": "cc_curve",
                "cc": 1,
                "range": [0, 1],
                "from": 0,
                "to": 64,
                "resolution": 0.5,
            }
        ],
        track_id=1,
    )

    summary = midi_query(track_id=1, selection={"range": [0, 1]}, include=["notes", "events"])

    assert summary["selected"]["note_count"] == 2
    assert summary["notes"]["velocity"]["min"] == 40
    assert summary["notes"]["velocity"]["max"] == 100
    assert summary["events"]["lanes"][0]["id"] == "cc:1:ch0"
    assert summary["events"]["lanes"][0]["count"] == 3


def test_midi_inspect_returns_detailed_selected_notes_and_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    midi_write(
        1,
        [{"id": "detail_note", "pitch": 60, "start": 0, "duration": 0.5, "velocity": 88}],
        start=0,
        end=1,
    )
    midi_diff(
        1,
        [
            {
                "op": "add_event",
                "event": {
                    "id": "detail_cc",
                    "type": "cc",
                    "start": 0.25,
                    "controller": 1,
                    "value": 64,
                },
            }
        ],
    )

    details = midi_inspect(track_id=1, selection={"range": [0, 1]}, limit=10)

    assert details["pagination"]["total"] == 2
    assert [item["kind"] for item in details["items"]] == ["note", "event"]
    assert details["items"][0]["id"] == "detail_note"
    assert details["items"][1]["id"] == "detail_cc"
    assert details["items"][1]["controller"] == 1


def test_midi_diff_uses_absolute_beats_from_inspect_for_nonzero_clip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Offset Clip",
                    "clips": [
                        {
                            "id": "clip_offset",
                            "type": "midi",
                            "start": 8,
                            "duration": 4,
                            "notes": [
                                {
                                    "id": "offset_note",
                                    "pitch": 60,
                                    "start": 0.5,
                                    "duration": 1,
                                    "velocity": 88,
                                }
                            ],
                            "events": [
                                {
                                    "id": "offset_cc",
                                    "type": "cc",
                                    "start": 0.25,
                                    "controller": 1,
                                    "value": 64,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    details = midi_inspect(track_id=1, selection={"range": [8, 10]}, limit=10)
    note_detail = next(item for item in details["items"] if item["id"] == "offset_note")
    event_detail = next(item for item in details["items"] if item["id"] == "offset_cc")
    assert note_detail["start"] == 8.5
    assert note_detail["local_start"] == 0.5
    assert event_detail["start"] == 8.25
    assert event_detail["local_start"] == 0.25

    project, _summary = midi_diff(
        1,
        [
            {"op": "update_note", "id": "offset_note", "start": 9.0},
            {"op": "update_event", "event_id": "offset_cc", "start": 9.25, "value": 96},
        ],
    )

    track = project["tracks"][0]
    clip = track["clips"][0]
    note = clip["notes"][0]
    event = clip["events"][0]
    assert note["start"] == 1.0
    assert event["start"] == 1.25
    assert track["notes"][0]["start"] == 9.0
    assert track["midi_events"][0]["start"] == 9.25
    assert track["midi_events"][0]["value"] == 96


def test_midi_diff_adds_note_to_offset_clip_using_absolute_beat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Offset Clip",
                    "clips": [
                        {
                            "id": "clip_offset",
                            "type": "midi",
                            "start": 8,
                            "duration": 4,
                            "notes": [],
                        }
                    ],
                }
            ]
        }
    )

    project, _summary = midi_diff(
        1,
        [
            {
                "op": "add_note",
                "clip_id": "clip_offset",
                "note": {"id": "new_abs", "pitch": 64, "start": 9, "duration": 1, "velocity": 90},
            }
        ],
    )

    track = project["tracks"][0]
    assert track["clips"][0]["notes"][0]["start"] == 1
    assert track["notes"][0]["start"] == 9


def test_project_preserves_zero_host_track_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = load_project()
    project["tracks"][0]["host_track_id"] = 0

    saved = save_project(project)

    assert saved["tracks"][0]["host_track_id"] == 0


def test_midi_write_accepts_zero_host_track_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = load_project()
    project["tracks"][0]["host_track_id"] = 0
    save_project(project)

    project, summary = midi_write(
        0,
        [{"pitch": 60, "start": 0, "duration": 0.5, "velocity": 100}],
        start=0,
        end=1,
    )

    assert summary["requested_track_id"] == 0
    assert summary["track_id"] == 1
    assert summary["host_track_id"] == 0
    assert project["tracks"][0]["notes"][0]["pitch"] == 60


def test_project_migrates_legacy_notes_to_midi_clip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    project = save_project(
        {
            "title": "Legacy",
            "tempo": 120,
            "time_signature": [4, 4],
            "length_beats": 16,
            "tracks": [
                {
                    "id": 1,
                    "name": "Keys",
                    "notes": [
                        {"id": "a", "pitch": 60, "start": 4, "duration": 1, "velocity": 90},
                        {"id": "b", "pitch": 64, "start": 5, "duration": 1, "velocity": 90},
                    ],
                }
            ],
        }
    )

    track = project["tracks"][0]
    assert len(track["clips"]) == 1
    assert track["clips"][0]["type"] == "midi"
    assert track["clips"][0]["start"] == 4
    assert [note["start"] for note in track["clips"][0]["notes"]] == [0, 1]
    assert [note["start"] for note in track["notes"]] == [4, 5]


def test_project_keeps_explicit_empty_clips_when_notes_are_stale(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    project = save_project(
        {
            "title": "Deleted Clips",
            "tracks": [
                {
                    "id": 1,
                    "name": "Keys",
                    "clips": [],
                    "notes": [
                        {"id": "stale", "pitch": 60, "start": 4, "duration": 1, "velocity": 90},
                    ],
                }
            ],
        }
    )

    track = project["tracks"][0]
    assert track["clips"] == []
    assert track["notes"] == []
    assert track["midi_events"] == []


def test_project_flattens_midi_clips_and_ignores_audio_clips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    project = save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Arrangement",
                    "clips": [
                        {
                            "id": "midi_1",
                            "type": "midi",
                            "start": 8,
                            "duration": 4,
                            "notes": [
                                {
                                    "id": "n1",
                                    "pitch": 67,
                                    "start": 0.5,
                                    "duration": 1,
                                    "velocity": 88,
                                }
                            ],
                        },
                        {"id": "audio_1", "type": "audio", "start": 1, "duration": 2},
                    ],
                }
            ],
        }
    )

    track = project["tracks"][0]
    assert len(track["clips"]) == 2
    assert len(track["notes"]) == 1
    assert track["notes"][0]["start"] == 8.5


def test_import_audio_clip_creates_new_audio_track_without_reusing_existing_lanes(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    load_project()
    source = tmp_path / "drop.wav"
    source.write_bytes(b"RIFF....WAVE")

    project, track, clip = import_audio_clip(
        source,
        name="Dropped Loop",
        start=4,
        duration_seconds=2.0,
        waveform=[0.1, 0.5, 1.2, "bad"],
    )

    assert track["id"] == 3
    assert track["name"] == "Dropped Loop"
    assert track["type"] == "audio"
    assert track["channel_type"] == "multichannel"
    assert track["plugin_slots"] == []
    assert clip["type"] == "audio"
    assert clip["start"] == 4
    assert clip["duration"] == 4
    assert clip["path"].endswith("drop.wav")
    assert clip["waveform"] == [0.1, 0.5, 1.0]
    assert project_summary(project)["audio_clip_count"] == 1


def test_import_audio_clip_preserves_structured_waveform_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()
    source = tmp_path / "drop.flac"
    source.write_bytes(b"fLaC")

    _, _, clip = import_audio_clip(
        source,
        name="Detailed Audio",
        duration_seconds=1.0,
        waveform=[
            {"min": -0.8, "max": 1.2, "rms": 0.42, "peak": 0.9},
            {"min": "bad"},
            {"min": 0.6, "max": -0.2, "rms": 0.2},
        ],
    )

    assert clip["waveform"] == [
        {"min": -0.8, "max": 1.0, "rms": 0.42, "peak": 1.0},
        {"min": -0.2, "max": 0.6, "rms": 0.2, "peak": 0.6},
    ]


def test_set_track_plugin_updates_instrument_slot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    project, track = set_track_plugin(
        1,
        {
            "type": "vst3",
            "name": "Example Synth",
            "path": "C:/VST3/Example.vst3",
            "vendor": "Example",
        },
    )

    slot = track["plugin_slots"][0]
    assert slot["type"] == "vst3"
    assert slot["name"] == "Example Synth"
    assert slot["path"] == "C:/VST3/Example.vst3"
    assert project_summary(project)["tracks"][0]["instrument"] == "Example Synth"


def test_plugin_slot_preserves_state_chunk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    _, track = set_track_plugin(
        1,
        {
            "type": "vst3",
            "name": "Example Synth",
            "path": "C:/VST3/Example.vst3",
            "state_b64": "AAECAw==",
        },
    )

    assert track["plugin_slots"][0]["state_b64"] == "AAECAw=="


def test_set_track_plugin_updates_insert_slot_without_changing_instrument(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    _, track = set_track_plugin(
        1,
        {
            "type": "vst3",
            "name": "Space Verb",
            "path": "C:/VST3/SpaceVerb.vst3",
            "vendor": "Example",
        },
        slot_id="insert_1",
    )

    slots = {slot["id"]: slot for slot in track["plugin_slots"]}
    assert slots["instrument"]["type"] == "builtin"
    assert slots["instrument"]["name"] == "ATRI Basic Synth"
    assert slots["insert_1"]["type"] == "vst3"
    assert slots["insert_1"]["name"] == "Space Verb"
    assert track["instrument"] == "ATRI Basic Synth"


def test_set_track_plugin_can_clear_insert_slot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()
    set_track_plugin(
        1,
        {"type": "vst3", "name": "Space Verb", "path": "C:/VST3/SpaceVerb.vst3"},
        slot_id="insert_1",
    )

    _, track = set_track_plugin(1, {"type": "empty"}, slot_id="insert_1")

    slots = {slot["id"]: slot for slot in track["plugin_slots"]}
    assert slots["insert_1"]["type"] == "empty"
    assert slots["insert_1"]["name"] == "Empty"
