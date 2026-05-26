import json

from core.music_project import load_project, save_project
from core.music_theory.music21_harmony import analyze_harmony
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
