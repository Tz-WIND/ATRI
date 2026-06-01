import base64
import json
import zipfile

import pytest

from core.music_export import (
    build_export_manifest,
    read_dawproject_archive,
    write_dawproject_archive,
    write_project_midi,
)


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


def test_build_export_manifest_should_include_bridge_preview_and_selection_summary():
    project = {"title": "Bridge Selection", "tempo": 128, "time_signature": [4, 4]}
    export = {
        "id": "export456",
        "format": "midi",
        "beat_range": [4.0, 8.0],
        "bridge_scope": {"instance_id": "bridge-selection"},
        "bridge_export": {
            "source": "bridge",
            "range_source": "selection",
            "primary_file": "selection.mid",
        },
        "bridge_preview": {
            "kind": "midi_region",
            "filename": "selection.mid",
            "range_source": "selection",
            "track_count": 1,
        },
        "selection_summary": {
            "range_beats": [4.0, 8.0],
            "project_track_ids": [3],
        },
        "files": [{"role": "midi", "filename": "selection.mid"}],
    }

    manifest = build_export_manifest(project, export, consumer="bridge")

    assert manifest["range"] == {"beat_range": [4.0, 8.0], "source": "selection"}
    assert manifest["bridge"]["scope"] == {"instance_id": "bridge-selection"}
    assert manifest["bridge"]["export"]["range_source"] == "selection"
    assert manifest["bridge"]["preview"]["track_count"] == 1
    assert manifest["selection"] == {"range_beats": [4.0, 8.0], "project_track_ids": [3]}


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


def test_read_dawproject_archive_should_import_midi_tracks(tmp_path):
    archive_path = tmp_path / "host-session.dawproject"
    project_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Project version="1.0" application="Host DAW">
  <Transport>
    <Tempo value="132"/>
    <TimeSignature numerator="3" denominator="4"/>
  </Transport>
  <Structure>
    <Track id="track_lead" name="Host Lead" color="#4e79ff" type="instrument">
      <Channel volume="0.7" pan="-0.25" mute="false" solo="true"/>
    </Track>
    <Track id="track_bass" name="Host Bass" type="instrument"/>
  </Structure>
  <Arrangement>
    <Lane track="track_lead">
      <Clip name="Lead Phrase" time="4" duration="2">
        <Notes>
          <Note time="0.5" duration="0.75" key="64" velocity="0.75" channel="1"/>
          <Note time="1.0" duration="0.5" key="67" velocity="96"/>
        </Notes>
      </Clip>
    </Lane>
    <Lane track="track_bass">
      <Clip time="0" duration="1">
        <Notes>
          <Note time="0" duration="1" key="36" velocity="0.5"/>
        </Notes>
      </Clip>
    </Lane>
  </Arrangement>
</Project>
"""
    metadata_xml = """<?xml version="1.0" encoding="UTF-8"?>
<MetaData>
  <Title>Host Session</Title>
  <Application name="Host DAW"/>
</MetaData>
"""
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.xml", project_xml)
        archive.writestr("metadata.xml", metadata_xml)

    project, summary = read_dawproject_archive(archive_path)

    assert project["title"] == "Host Session"
    assert project["tempo"] == 132.0
    assert project["time_signature"] == [3, 4]
    assert summary == {
        "source": str(archive_path),
        "format": "dawproject",
        "track_count": 2,
        "midi_clip_count": 2,
        "note_count": 3,
        "tempo": 132.0,
        "time_signature": [3, 4],
    }
    lead = project["tracks"][0]
    assert lead["name"] == "Host Lead"
    assert lead["color"] == "#4e79ff"
    assert lead["volume"] == 0.7
    assert lead["pan"] == -0.25
    assert lead["solo"] is True
    assert lead["clips"][0]["start"] == 4.0
    assert lead["clips"][0]["notes"][0] == {
        "id": "track_lead_clip_1_note_1",
        "pitch": 64,
        "start": 0.5,
        "duration": 0.75,
        "velocity": 95,
    }
    assert lead["notes"][0]["start"] == 4.5


def test_read_dawproject_archive_should_reject_dtd_entities(tmp_path):
    archive_path = tmp_path / "unsafe.dawproject"
    project_xml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Project [
  <!ENTITY unsafe "bad">
]>
<Project name="&unsafe;">
  <Transport><Tempo value="120"/></Transport>
</Project>
"""
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.xml", project_xml)

    with pytest.raises(ValueError, match="unsafe XML"):
        read_dawproject_archive(archive_path)


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
