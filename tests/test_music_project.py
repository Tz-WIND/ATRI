import pytest

from core.music_project import (
    create_track,
    delete_track,
    load_project,
    midi_diff,
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
