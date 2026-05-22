import json

from core.music_project import save_project
from core.piano_playability import piano_playability_check
from core.tools import create_tools


def test_piano_playability_marks_tenth_to_twelfth_span_warning_and_wider_error(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Piano",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 4,
                            "notes": [
                                {
                                    "id": "warning_low",
                                    "pitch": 64,
                                    "start": 0,
                                    "duration": 1,
                                    "velocity": 90,
                                },
                                {
                                    "id": "warning_high",
                                    "pitch": 80,
                                    "start": 0,
                                    "duration": 1,
                                    "velocity": 90,
                                },
                                {
                                    "id": "error_low",
                                    "pitch": 64,
                                    "start": 2,
                                    "duration": 1,
                                    "velocity": 90,
                                },
                                {
                                    "id": "error_high",
                                    "pitch": 84,
                                    "start": 2,
                                    "duration": 1,
                                    "velocity": 90,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = piano_playability_check(track_id=1)

    span_issues = [issue for issue in result["issues"] if issue["code"] == "hand_span"]
    assert [(issue["start"], issue["severity"]) for issue in span_issues] == [
        (0.0, "warning"),
        (2.0, "error"),
    ]
    assert result["summary"]["max_problem_severity"] == "error"


def test_piano_playability_reports_rapid_large_leaps_as_info_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Piano",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 2,
                            "notes": [
                                {
                                    "id": "bass_a",
                                    "pitch": 36,
                                    "start": 0,
                                    "duration": 0.25,
                                    "velocity": 88,
                                },
                                {
                                    "id": "bass_b",
                                    "pitch": 52,
                                    "start": 0.25,
                                    "duration": 0.25,
                                    "velocity": 88,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = piano_playability_check(track_id=1)

    assert result["issues"] == []
    assert result["difficulty_notes"] == [
        {
            "severity": "info",
            "code": "rapid_reposition",
            "start": 0.25,
            "hand": "left",
            "from_pitch": 36,
            "to_pitch": 52,
            "interval_semitones": 16,
            "message": "Large left-hand reposition in a short time window.",
        }
    ]
    assert result["summary"]["difficulty_note_count"] == 1
    assert result["summary"]["max_problem_severity"] is None
    assert result["summary"]["playability"] == "playable"


def test_piano_playability_marks_too_many_simultaneous_notes_for_one_hand_error(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Piano",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 1,
                            "notes": [
                                {
                                    "id": f"rh_{pitch}",
                                    "pitch": pitch,
                                    "start": 0,
                                    "duration": 1,
                                    "velocity": 88,
                                }
                                for pitch in [60, 62, 64, 65, 67, 69]
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = piano_playability_check(track_id=1)

    density_issues = [issue for issue in result["issues"] if issue["code"] == "hand_density"]
    assert density_issues == [
        {
            "severity": "error",
            "code": "hand_density",
            "start": 0.0,
            "end": 1.0,
            "hand": "right",
            "note_count": 6,
            "notes": ["rh_60", "rh_62", "rh_64", "rh_65", "rh_67", "rh_69"],
            "message": "Right hand has 6 simultaneous notes.",
            "suggestion": "Remove a note, roll the chord, or redistribute notes between hands.",
        }
    ]
    assert result["summary"]["playability"] == "likely_unplayable"


def test_piano_playability_allows_left_hand_above_right_when_right_hand_is_blocked(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Piano",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 4,
                            "notes": [
                                {
                                    "id": "rh_hold_low",
                                    "pitch": 67,
                                    "start": 0,
                                    "duration": 2,
                                    "velocity": 84,
                                },
                                {
                                    "id": "rh_hold_mid",
                                    "pitch": 71,
                                    "start": 0,
                                    "duration": 2,
                                    "velocity": 84,
                                },
                                {
                                    "id": "rh_hold_high",
                                    "pitch": 74,
                                    "start": 0,
                                    "duration": 2,
                                    "velocity": 84,
                                },
                                {
                                    "id": "left_crosses_above",
                                    "pitch": 79,
                                    "start": 1,
                                    "duration": 0.5,
                                    "velocity": 92,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = piano_playability_check(track_id=1)

    assert result["issues"] == []
    assert result["difficulty_notes"] == [
        {
            "severity": "info",
            "code": "left_hand_over_right_allowed",
            "start": 1.0,
            "hand": "left",
            "message": "Left hand crosses above a blocked right-hand position.",
            "notes": ["left_crosses_above"],
        }
    ]
    assert result["summary"]["max_problem_severity"] is None


def test_piano_playability_warns_when_left_hand_crosses_without_blocked_right_hand(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Piano",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 4,
                            "notes": [
                                {
                                    "id": "rh_hold",
                                    "pitch": 67,
                                    "start": 0,
                                    "duration": 2,
                                    "velocity": 84,
                                },
                                {
                                    "id": "left_crosses_above",
                                    "pitch": 79,
                                    "start": 1,
                                    "duration": 0.5,
                                    "velocity": 92,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = piano_playability_check(track_id=1)

    assert result["difficulty_notes"] == []
    crossing_issues = [issue for issue in result["issues"] if issue["code"] == "hand_crossing"]
    assert crossing_issues == [
        {
            "severity": "warning",
            "code": "hand_crossing",
            "start": 1.0,
            "end": 1.5,
            "hand": "left",
            "notes": ["left_crosses_above"],
            "message": "Left hand crosses above the right hand while the right hand can move.",
            "suggestion": "Assign the crossing note to the right hand or adjust the voicing.",
        }
    ]
    assert result["summary"]["max_problem_severity"] == "warning"


def test_piano_playability_warns_about_dense_extreme_register_clusters(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Piano",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 1,
                            "notes": [
                                {
                                    "id": f"low_{pitch}",
                                    "pitch": pitch,
                                    "start": 0,
                                    "duration": 1,
                                    "velocity": 86,
                                }
                                for pitch in [24, 28, 31, 35]
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = piano_playability_check(track_id=1)

    register_issues = [
        issue for issue in result["issues"] if issue["code"] == "extreme_register_density"
    ]
    assert register_issues == [
        {
            "severity": "warning",
            "code": "extreme_register_density",
            "start": 0.0,
            "end": 1.0,
            "register": "low",
            "note_count": 4,
            "notes": ["low_24", "low_28", "low_31", "low_35"],
            "message": "Dense low-register piano writing can become muddy.",
            "suggestion": "Thin the voicing, spread attacks, or move some notes upward.",
        }
    ]


def test_piano_playability_suggests_sustain_pedal_for_connected_arpeggio(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Piano",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 3,
                            "notes": [
                                {
                                    "id": f"arp_{index}",
                                    "pitch": pitch,
                                    "start": index * 0.5,
                                    "duration": 0.5,
                                    "velocity": 82,
                                }
                                for index, pitch in enumerate([60, 64, 67, 72])
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = piano_playability_check(track_id=1)

    assert result["issues"] == []
    assert result["suggestions"] == [
        {
            "code": "sustain_pedal_suggested",
            "start": 0.0,
            "end": 2.0,
            "message": "Connected arpeggio texture may benefit from CC64 sustain pedal.",
            "suggestion": "Add CC64 pedal down near the start and release after the phrase.",
        }
    ]
    assert result["summary"]["suggestion_count"] == 1
    assert result["summary"]["playability"] == "playable"


def test_piano_playability_tool_is_registered_read_only_and_outputs_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_project(
        {
            "tracks": [
                {
                    "id": 1,
                    "name": "Piano",
                    "clips": [
                        {
                            "id": "clip_1",
                            "type": "midi",
                            "start": 0,
                            "duration": 2,
                            "notes": [
                                {
                                    "id": "wide_low",
                                    "pitch": 64,
                                    "start": 0,
                                    "duration": 1,
                                    "velocity": 90,
                                },
                                {
                                    "id": "wide_high",
                                    "pitch": 84,
                                    "start": 0,
                                    "duration": 1,
                                    "velocity": 90,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )

    tools = {tool.name: tool for tool in create_tools(str(tmp_path))}
    tool = tools["piano_playability_check"]
    result = json.loads(tool.execute(track_id=1, selection={"range": [0, 1]}))

    assert tool.metadata()["capability"] == "music.piano.playability"
    assert tool.metadata()["read_only"] is True
    assert tool.metadata()["supports_parallel"] is True
    assert result["summary"]["playability"] == "likely_unplayable"
    assert result["issues"][0]["code"] == "hand_span"
