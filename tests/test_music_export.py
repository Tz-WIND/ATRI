import base64
import json
import zipfile

from core.music_export import build_export_manifest, write_dawproject_archive, write_project_midi


def test_write_project_midi_with_notes_should_create_type_one_file(tmp_path):
    project = {
        "title": "MIDI Test",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 4,
        "tracks": [
            {
                "id": 1,
                "type": "instrument",
                "name": "Lead",
                "notes": [{"id": "n1", "pitch": 60, "start": 0, "duration": 1, "velocity": 96}],
                "midi_events": [
                    {
                        "id": "cc1",
                        "type": "control_change",
                        "start": 0.5,
                        "controller": 1,
                        "value": 80,
                    }
                ],
            }
        ],
    }
    path = tmp_path / "session.mid"

    summary = write_project_midi(project, path)

    data = path.read_bytes()
    assert data.startswith(b"MThd")
    assert data[8:10] == b"\x00\x01"
    assert b"Lead" in data
    assert b"\xff\x51\x03" in data
    assert summary["note_count"] == 1
    assert summary["event_count"] == 1


def test_build_export_manifest_should_include_bridge_contract_fields():
    project = {
        "title": "Bridge Session",
        "tempo": 128,
        "time_signature": [7, 8],
        "length_beats": 16,
    }
    export = {
        "id": "export123",
        "format": "midi",
        "files": [{"role": "midi", "filename": "export123.mid"}],
        "track_ids": [3],
    }

    manifest = build_export_manifest(project, export, consumer="bridge")

    assert manifest["schema_version"] == 1
    assert manifest["consumer"] == "bridge"
    assert manifest["export_id"] == "export123"
    assert manifest["project"]["tempo"] == 128.0
    assert manifest["files"][0]["filename"] == "export123.mid"
    assert manifest["capabilities"]["midi"] is True


def test_write_project_midi_with_selected_track_ids_should_filter_tracks(tmp_path):
    project = {
        "title": "Selected MIDI",
        "tempo": 100,
        "time_signature": [4, 4],
        "tracks": [
            {
                "id": 1,
                "type": "instrument",
                "name": "Skip",
                "notes": [{"id": "skip", "pitch": 60, "start": 0, "duration": 1}],
                "midi_events": [],
            },
            {
                "id": 2,
                "type": "instrument",
                "name": "Keep",
                "notes": [{"id": "keep", "pitch": 64, "start": 1, "duration": 0.5}],
                "midi_events": [],
            },
        ],
    }
    path = tmp_path / "selected.mid"

    summary = write_project_midi(project, path, track_ids=[2])

    data = path.read_bytes()
    assert b"Keep" in data
    assert b"Skip" not in data
    assert summary["track_ids"] == [2]
    assert summary["note_count"] == 1


def test_write_project_midi_with_beat_range_exports_only_that_region(tmp_path):
    project = {
        "title": "Range MIDI",
        "tempo": 120,
        "time_signature": [4, 4],
        "tracks": [
            {
                "id": 1,
                "type": "instrument",
                "name": "Lead",
                "notes": [
                    {"id": "before", "pitch": 50, "start": 1, "duration": 0.5},
                    {"id": "inside", "pitch": 64, "start": 5, "duration": 1.0},
                    {"id": "after", "pitch": 72, "start": 10, "duration": 0.5},
                ],
                "midi_events": [
                    {
                        "id": "cc-before",
                        "type": "control_change",
                        "start": 1.5,
                        "controller": 1,
                        "value": 30,
                    },
                    {
                        "id": "cc-inside",
                        "type": "control_change",
                        "start": 6,
                        "controller": 1,
                        "value": 90,
                    },
                ],
            }
        ],
    }
    path = tmp_path / "range.mid"

    summary = write_project_midi(project, path, track_ids=[1], beat_range=(4, 8))

    assert path.read_bytes().startswith(b"MThd")
    assert summary["track_ids"] == [1]
    assert summary["beat_range"] == [4.0, 8.0]
    assert summary["note_count"] == 1
    assert summary["event_count"] == 1


def test_write_dawproject_archive_should_preserve_plugin_state_files(tmp_path):
    audio_path = tmp_path / "loop.wav"
    audio_path.write_bytes(b"RIFF....WAVE")
    state_bytes = b"vst3-state-bytes"
    project = {
        "title": "DAWProject Test",
        "tempo": 126,
        "time_signature": [4, 4],
        "length_beats": 8,
        "tracks": [
            {
                "id": 1,
                "type": "instrument",
                "name": "Lead",
                "color": "#4e79ff",
                "volume": 0.8,
                "pan": 0.1,
                "notes": [{"id": "n1", "pitch": 60, "start": 0, "duration": 1, "velocity": 96}],
                "midi_events": [],
                "plugin_slots": [
                    {
                        "id": "instrument",
                        "type": "vst3",
                        "name": "Example Synth",
                        "vendor": "Example Vendor",
                        "path": "C:/VST3/Example Synth.vst3",
                        "state_b64": base64.b64encode(state_bytes).decode("ascii"),
                    }
                ],
            },
            {
                "id": 2,
                "type": "audio",
                "name": "Loop",
                "notes": [],
                "midi_events": [],
                "clips": [
                    {
                        "id": "clip_loop",
                        "type": "audio",
                        "name": "Loop Clip",
                        "path": str(audio_path),
                        "start": 0,
                        "duration": 4,
                    }
                ],
                "plugin_slots": [],
            },
        ],
    }
    archive_path = tmp_path / "session.dawproject"

    export = write_dawproject_archive(
        project,
        archive_path,
        export_id="export123",
        consumer="bridge",
        workspace_root=tmp_path,
    )

    assert export["format"] == "dawproject"
    assert export["plugin_state_count"] == 1
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        assert {"project.xml", "metadata.xml", "atri-export-manifest.json"} <= names
        state_name = next(name for name in names if name.startswith("plugins/"))
        assert archive.read(state_name) == state_bytes
        assert archive.read("media/audio/loop.wav") == b"RIFF....WAVE"
        project_xml = archive.read("project.xml").decode("utf-8")
        manifest = json.loads(archive.read("atri-export-manifest.json").decode("utf-8"))

    assert "Example Synth" in project_xml
    assert "ParameterChunk" in project_xml
    assert manifest["consumer"] == "bridge"
    assert manifest["capabilities"]["dawproject"] is True
    assert manifest["plugin_states"][0]["state_sha256"]


def test_write_dawproject_archive_should_only_embed_workspace_audio_files(tmp_path):
    workspace = tmp_path / "workspace"
    samples = workspace / "samples"
    samples.mkdir(parents=True)
    inside_audio = samples / "loop.wav"
    inside_audio.write_bytes(b"RIFF-inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    absolute_secret = outside / "absolute-secret.wav"
    traversal_secret = outside / "traversal-secret.wav"
    absolute_secret.write_bytes(b"RIFF-absolute-secret")
    traversal_secret.write_bytes(b"RIFF-traversal-secret")
    project = {
        "title": "DAWProject Path Jail",
        "tempo": 120,
        "time_signature": [4, 4],
        "tracks": [
            {
                "id": 1,
                "type": "audio",
                "name": "Audio",
                "notes": [],
                "midi_events": [],
                "clips": [
                    {
                        "id": "inside",
                        "type": "audio",
                        "name": "Inside",
                        "path": "samples/loop.wav",
                    },
                    {
                        "id": "absolute",
                        "type": "audio",
                        "name": "Absolute Secret",
                        "path": str(absolute_secret),
                    },
                    {
                        "id": "traversal",
                        "type": "audio",
                        "name": "Traversal Secret",
                        "path": "../outside/traversal-secret.wav",
                    },
                ],
                "plugin_slots": [],
            }
        ],
    }
    archive_path = tmp_path / "session.dawproject"

    export = write_dawproject_archive(
        project,
        archive_path,
        export_id="export123",
        workspace_root=workspace,
    )

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        project_xml = archive.read("project.xml").decode("utf-8")
        manifest = json.loads(archive.read("atri-export-manifest.json").decode("utf-8"))

    assert "media/audio/loop.wav" in names
    assert not any("absolute-secret" in name for name in names)
    assert not any("traversal-secret" in name for name in names)
    assert "Absolute Secret" not in project_xml
    assert "Traversal Secret" not in project_xml
    assert [file["filename"] for file in export["files"] if file["role"] == "audio"] == ["loop.wav"]
    assert [file["filename"] for file in manifest["files"] if file["role"] == "audio"] == [
        "loop.wav"
    ]
