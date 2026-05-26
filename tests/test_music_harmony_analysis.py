import json

from core.music_project import load_project, save_project
from core.music_theory.music21_harmony import analyze_harmony
from core.music_theory.music21_transform import transpose_music
from core.tools import create_tools


def _note(note_id: str, pitch: int, start: float, duration: float = 4.0) -> dict:
    return {"id": note_id, "pitch": pitch, "start": start, "duration": duration, "velocity": 90}


def test_music21_harmony_analyze_infers_harmony_events_by_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tempo": 120,
            "time_signature": [4, 4],
            "tracks": [
                {
                    "id": 1,
                    "name": "Keys",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 16,
                            "notes": [
                                _note("c", 60, 0),
                                _note("e", 64, 0),
                                _note("g", 67, 0),
                                _note("f", 65, 4),
                                _note("a", 69, 4),
                                _note("c2", 72, 4),
                                _note("g2", 67, 8),
                                _note("b", 71, 8),
                                _note("d", 74, 8),
                                _note("f2", 77, 8),
                            ],
                        }
                    ],
                }
            ],
        }
    )

    result = analyze_harmony(track_ids=[1], beat_range=[0, 12], window_beats=4)

    assert result["key"]["tonic"] == "C"
    assert result["key"]["mode"] == "major"
    assert [(event["beat"], event["text"]) for event in result["events"]] == [
        (0.0, "C"),
        (4.0, "F"),
        (8.0, "G7"),
    ]
    assert all(event["confidence"] >= 0.8 for event in result["events"])


def test_music_harmony_analyze_tool_can_apply_harmony_lane_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Pads",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 8,
                            "notes": [
                                _note("c", 60, 0),
                                _note("e", 64, 0),
                                _note("g", 67, 0),
                                _note("f", 65, 4),
                                _note("a", 69, 4),
                                _note("c2", 72, 4),
                            ],
                        }
                    ],
                }
            ],
        }
    )
    tool = {tool.name: tool for tool in create_tools(str(tmp_path))}["music_harmony_analyze"]

    result = json.loads(tool.execute(track_ids=[1], range=[0, 8], window_beats=4, apply=True))

    assert result["applied"] is True
    assert [(event["beat"], event["text"]) for event in result["events"]] == [
        (0.0, "C"),
        (4.0, "F"),
    ]
    assert load_project()["harmony_events"] == [
        {"beat": 0.0, "text": "C"},
        {"beat": 4.0, "text": "F"},
    ]


def test_music21_harmony_analyze_reports_modulations_and_local_roman_numerals(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Progression",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 16,
                            "notes": [
                                _note("c", 60, 0),
                                _note("e", 64, 0),
                                _note("g", 67, 0),
                                _note("g7_g", 67, 4),
                                _note("g7_b", 71, 4),
                                _note("g7_d", 74, 4),
                                _note("g7_f", 77, 4),
                                _note("g2", 67, 8),
                                _note("b2", 71, 8),
                                _note("d2", 74, 8),
                                _note("d7_d", 74, 12),
                                _note("d7_fs", 78, 12),
                                _note("d7_a", 81, 12),
                                _note("d7_c", 84, 12),
                            ],
                        }
                    ],
                }
            ],
        }
    )

    result = analyze_harmony(
        track_ids=[1],
        beat_range=[0, 16],
        window_beats=4,
        key_window_beats=8,
    )

    assert [(event["beat"], event["key"]["name"]) for event in result["key_events"]] == [
        (0.0, "C major"),
        (8.0, "G major"),
    ]
    assert result["modulations"] == [{"beat": 8.0, "from_key": "C major", "to_key": "G major"}]
    assert [(event["text"], event["roman"]) for event in result["events"]] == [
        ("C", "I"),
        ("G7", "V7"),
        ("G", "I"),
        ("D7", "V7"),
    ]


def test_music_transpose_preview_and_apply_moves_notes_and_harmony_lane(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "harmony_events": [
                {"beat": 0, "text": "C"},
                {"beat": 4, "text": "G7"},
            ],
            "tracks": [
                {
                    "id": 1,
                    "name": "Lead",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 8,
                            "notes": [
                                _note("c", 60, 0),
                                _note("e", 64, 0),
                                _note("g", 67, 0),
                            ],
                        }
                    ],
                }
            ],
        }
    )

    preview = transpose_music(
        track_ids=[1],
        beat_range=[0, 8],
        from_key="C",
        to_key="D",
        transpose_harmony=True,
        apply=False,
    )

    assert preview["applied"] is False
    assert preview["semitones"] == 2
    assert [(note["pitch"], note["new_pitch"]) for note in preview["notes"]] == [
        (60, 62),
        (64, 66),
        (67, 69),
    ]
    assert [(event["text"], event["new_text"]) for event in preview["harmony_events"]] == [
        ("C", "D"),
        ("G7", "A7"),
    ]
    assert [note["pitch"] for note in load_project()["tracks"][0]["notes"]] == [60, 64, 67]

    applied = transpose_music(
        track_ids=[1],
        beat_range=[0, 8],
        semitones=2,
        transpose_harmony=True,
        apply=True,
    )

    assert applied["applied"] is True
    project = load_project()
    assert [note["pitch"] for note in project["tracks"][0]["notes"]] == [62, 66, 69]
    assert project["harmony_events"] == [
        {"beat": 0.0, "text": "D"},
        {"beat": 4.0, "text": "A7"},
    ]


def test_music_transpose_tool_is_registered_and_returns_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Bass",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 1,
                            "notes": [_note("bass_c", 36, 0, duration=1)],
                        }
                    ],
                }
            ]
        }
    )

    tool = {tool.name: tool for tool in create_tools(str(tmp_path))}["music_transpose"]
    result = json.loads(tool.execute(track_ids=[1], range=[0, 1], semitones=12))

    assert result["applied"] is False
    assert result["notes"][0]["new_pitch"] == 48


def test_music_transpose_selects_notes_that_overlap_range_boundaries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Sustained Pad",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 8,
                            "notes": [
                                _note("held_into_range", 60, 3.5, duration=2),
                                _note("after_range", 64, 8, duration=1),
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = transpose_music(track_ids=[1], beat_range=[4, 8], semitones=2)

    assert [(note["id"], note["new_pitch"]) for note in result["notes"]] == [
        ("held_into_range", 62)
    ]


def test_music_transpose_ignores_non_midi_track_types(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Automation",
                    "type": "automation",
                    "notes": [_note("stale_automation_note", 60, 0, duration=1)],
                },
                {
                    "id": 2,
                    "name": "Audio",
                    "type": "audio",
                    "notes": [_note("stale_audio_note", 62, 0, duration=1)],
                },
                {
                    "id": 3,
                    "name": "Instrument",
                    "type": "instrument",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 1,
                            "notes": [_note("real_note", 64, 0, duration=1)],
                        }
                    ],
                },
            ]
        }
    )

    result = transpose_music(beat_range=[0, 1], semitones=2)

    assert [(note["id"], note["new_pitch"]) for note in result["notes"]] == [("real_note", 66)]
