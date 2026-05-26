import io
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from anyio import Path as AsyncPath
from quart import Quart
from werkzeug.datastructures import FileStorage

from dashboard import music


class FakeStudioHost:
    def __init__(self):
        self.is_running = True
        self.commands = []
        self.sample_rate = 48000
        self.buffer_size = 256
        self.audio_engine = "test"
        self.bit_depth = "f32"
        self.binary_path = ""

    async def send_command(self, cmd, params=None, *, response_timeout=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "get_status":
            return {"tracks": [{"id": 1}, {"id": 2}]}
        return {"type": "ack", "cmd": cmd}


class ExportStudioHost(FakeStudioHost):
    def __init__(self):
        super().__init__()
        self.host_track_ids = [10, 20, 99]

    async def send_command(self, cmd, params=None, *, response_timeout=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "get_status":
            return {"tracks": [{"id": track_id} for track_id in self.host_track_ids]}
        if cmd == "bounce":
            output_path = Path(params["path"])
            await AsyncPath(output_path.parent).mkdir(parents=True, exist_ok=True)
            await AsyncPath(output_path).write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt data")
            return {
                "type": "ack",
                "cmd": "bounce",
                "data": {
                    "path": str(output_path),
                    "format": "wav",
                    "sample_rate": params.get("sample_rate", 48000),
                    "bit_depth": params.get("bit_depth", "f32"),
                    "frames": 128,
                    "channels": 2,
                },
            }
        return {"type": "ack", "cmd": cmd}


def test_ffmpeg_path_falls_back_to_imageio_ffmpeg_package(monkeypatch):
    monkeypatch.setattr(music.shutil, "which", lambda _name: None)
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: "bundled-ffmpeg"),
    )

    assert music._ffmpeg_path() == "bundled-ffmpeg"


class ParameterStudioHost(FakeStudioHost):
    async def send_command(self, cmd, params=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "get_status":
            return {"tracks": [{"id": 4}]}
        if cmd == "list_plugin_parameters":
            return {
                "type": "ack",
                "cmd": "list_plugin_parameters",
                "data": {
                    "track_id": params["track_id"],
                    "slot_index": params["slot_index"],
                    "parameters": [
                        {
                            "index": 0,
                            "param_id": 100,
                            "name": "Cutoff",
                            "units": "Hz",
                            "value": 0.42,
                            "automatable": True,
                        }
                    ],
                    "parameter_count": 1,
                },
            }
        return {"type": "ack", "cmd": cmd}


class StatefulParameterStudioHost(ParameterStudioHost):
    async def send_command(self, cmd, params=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "get_status":
            return {"tracks": [{"id": 4}]}
        if cmd == "set_plugin_parameter":
            return {"type": "ack", "cmd": cmd}
        if cmd == "get_plugin_state":
            return {
                "type": "ack",
                "cmd": cmd,
                "data": {"state_b64": "AAECAw=="},
            }
        return {"type": "ack", "cmd": cmd}


class CapturedParameterStudioHost(ParameterStudioHost):
    async def send_command(self, cmd, params=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "poll_captured_plugin_parameters":
            return {
                "type": "ack",
                "cmd": "poll_captured_plugin_parameters",
                "data": {
                    "parameters": [
                        {
                            "track_id": 4,
                            "slot_index": 0,
                            "param_index": 2,
                            "param_id": 900,
                            "name": "Resonance",
                            "units": "%",
                            "value": 0.77,
                            "automatable": True,
                            "plugin_name": "Synth",
                        }
                    ]
                },
            }
        return await super().send_command(cmd, params)


class AddTrackWithoutDataHost:
    def __init__(self):
        self.is_running = True
        self.commands = []

    async def send_command(self, cmd, params=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "get_status":
            return {"tracks": []}
        if cmd == "add_track":
            return {"type": "ack", "cmd": "add_track", "status": "ok", "data": None}
        return {"type": "ack", "cmd": cmd}


class FreshRoutingHost:
    def __init__(self):
        self.is_running = True
        self.commands = []
        self.next_track_id = 1
        self.route_kinds: dict[int, str] = {}

    async def send_command(self, cmd, params=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "get_status":
            return {"tracks": []}
        if cmd == "add_track":
            track_id = self.next_track_id
            self.next_track_id += 1
            self.route_kinds[track_id] = "track"
            return {"type": "ack", "cmd": "add_track", "data": {"track_id": track_id}}
        if cmd == "set_route_config":
            track_id = int(params["track_id"])
            kind = params.get("kind")
            if kind is not None:
                self.route_kinds[track_id] = kind
            output_track_id = params.get("output_track_id")
            if output_track_id is not None and self.route_kinds.get(int(output_track_id)) != "bus":
                return {"type": "error", "cmd": "set_route_config", "message": "target is not bus"}
        return {"type": "ack", "cmd": cmd}


class StoppedStudioHost:
    is_running = False


def _save_export_project() -> dict[str, Any]:
    return music.save_project(
        {
            "title": "Export Session",
            "tempo": 120,
            "time_signature": [4, 4],
            "length_beats": 16,
            "master_bus": {
                "host_track_id": 99,
                "name": "Master Bus",
                "plugin_slots": [],
            },
            "tracks": [
                {
                    "id": 1,
                    "host_track_id": 10,
                    "type": "instrument",
                    "name": "Lead",
                    "volume": 0.8,
                    "pan": 0,
                    "mute": False,
                    "solo": False,
                    "notes": [],
                    "midi_events": [],
                    "clips": [],
                    "plugin_slots": [
                        {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                    ],
                },
                {
                    "id": 2,
                    "host_track_id": 20,
                    "type": "audio",
                    "name": "Drums",
                    "volume": 0.9,
                    "pan": 0,
                    "mute": False,
                    "solo": False,
                    "notes": [],
                    "midi_events": [],
                    "clips": [],
                    "plugin_slots": [],
                },
            ],
        }
    )


async def test_load_track_slots_does_not_load_builtin_for_empty_bus():
    host = FakeStudioHost()

    responses = await music._load_track_slots(
        host,
        2,
        {"type": "bus", "instrument": "Bus", "plugin_slots": []},
    )

    assert responses == []
    assert host.commands == []


async def test_sync_project_to_host_persists_unsaved_project_when_host_is_stopped(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    project = music.load_project()
    project["title"] = "Unsaved Agent Edit"
    project["tracks"][0]["name"] = "Agent Lead"
    project["tracks"][0]["notes"] = [
        {"id": "agent_note", "pitch": 64, "start": 0, "duration": 1, "velocity": 100}
    ]
    monkeypatch.setattr(music, "_host_manager", lambda: StoppedStudioHost())

    sync = await music._sync_project_to_host(project, broadcast=True)

    saved = music.load_project()
    assert sync["host_running"] is False
    assert saved["title"] == "Unsaved Agent Edit"
    assert saved["tracks"][0]["name"] == "Agent Lead"
    assert saved["tracks"][0]["notes"][0]["id"] == "agent_note"


async def test_sync_project_to_host_persists_any_unsaved_project_difference(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    music.save_project(
        {
            "title": "Saved Session",
            "tempo": 120,
            "time_signature": [4, 4],
            "length_beats": 16,
            "master_bus": {
                "host_track_id": 99,
                "name": "Master Bus",
                "plugin_slots": [],
            },
            "tracks": [
                {
                    "id": 1,
                    "host_track_id": 1,
                    "type": "instrument",
                    "name": "Lead",
                    "notes": [],
                    "midi_events": [],
                    "clips": [],
                    "plugin_slots": [
                        {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                    ],
                }
            ],
        }
    )
    project = music.load_project()
    project["tracks"].append(
        {
            "id": 2,
            "host_track_id": 2,
            "type": "instrument",
            "name": "Empty Layer",
            "notes": [],
            "midi_events": [],
            "clips": [],
            "plugin_slots": [{"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}],
        }
    )

    class HostWithExistingTracks(FakeStudioHost):
        async def send_command(self, cmd, params=None):
            params = params or {}
            self.commands.append((cmd, params))
            if cmd == "get_status":
                return {"tracks": [{"id": 1}, {"id": 2}, {"id": 99}]}
            return {"type": "ack", "cmd": cmd}

    monkeypatch.setattr(music, "_host_manager", HostWithExistingTracks)

    sync = await music._sync_project_to_host(project, broadcast=False)

    saved = music.load_project()
    assert sync["host_running"] is True
    assert [track["name"] for track in saved["tracks"]] == ["Lead", "Empty Layer"]
    assert saved["tracks"][1]["notes"] == []


async def test_load_track_slots_loads_bus_insert_slots():
    host = FakeStudioHost()

    responses = await music._load_track_slots(
        host,
        2,
        {
            "type": "bus",
            "instrument": "Bus",
            "plugin_slots": [
                {
                    "id": "insert_1",
                    "type": "vst3",
                    "name": "Bus Compressor",
                    "path": "C:/VST3/BusCompressor.vst3",
                }
            ],
        },
    )

    assert responses == [{"type": "ack", "cmd": "load_vst3"}]
    assert host.commands == [
        (
            "load_vst3",
            {
                "track_id": 2,
                "slot_index": 1,
                "path": "C:/VST3/BusCompressor.vst3",
                "name": "Bus Compressor",
            },
        )
    ]


async def test_sync_project_to_host_removes_stale_host_tracks(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project: dict[str, Any] = {
        "title": "Delete Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "name": "Keep",
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
            }
        ],
    }

    await music._sync_project_to_host(project)

    assert ("remove_track", {"id": 2}) in host.commands
    assert all(cmd != "add_track" for cmd, _ in host.commands)


async def test_sync_project_to_host_handles_add_track_ack_without_data(monkeypatch):
    host = AddTrackWithoutDataHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project: dict[str, Any] = {
        "title": "Null Data Ack",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": None,
                "name": "Needs Host Id",
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
            }
        ],
    }

    sync = await music._sync_project_to_host(project)

    assert ("add_track", {"name": "Needs Host Id"}) in host.commands
    assert (
        "host_track_id" not in project["tracks"][0] or project["tracks"][0]["host_track_id"] is None
    )
    assert all(cmd != "set_midi" for cmd, _ in host.commands)
    assert sync["host_running"] is True


async def test_sync_project_to_host_sends_audio_track_channel_type(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
        "title": "Mono Audio",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "type": "audio",
                "channel_type": "mono",
                "name": "Mono Loop",
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [
                    {
                        "id": "clip_1",
                        "type": "audio",
                        "path": "data/music_workstation/audio/loop.wav",
                        "start": 0,
                        "duration": 4,
                    }
                ],
            }
        ],
    }

    await music._sync_project_to_host(project)

    set_audio = next(params for cmd, params in host.commands if cmd == "set_audio_clips")
    assert set_audio["clips"][0]["channel_type"] == "mono"


async def test_sync_project_to_host_skips_automation_routes_and_sends_lanes(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
        "title": "Automation Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "type": "instrument",
                "name": "Lead",
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [
                    {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                ],
            },
            {
                "id": 2,
                "host_track_id": None,
                "type": "automation",
                "name": "Lead Volume",
                "mute": False,
                "solo": False,
                "target": {"kind": "track_volume", "track_id": 1, "label": "Lead Volume"},
                "automation": {
                    "points": [{"beat": 0, "value": 0.4}, {"beat": 4, "value": 1.0}],
                },
                "clips": [],
                "notes": [],
                "midi_events": [],
            },
            {
                "id": 3,
                "host_track_id": None,
                "type": "automation",
                "name": "Tempo BPM",
                "mute": False,
                "solo": False,
                "target": {"kind": "tempo_bpm", "label": "Tempo BPM"},
                "automation": {
                    "points": [{"beat": 0, "value": 120}, {"beat": 4, "value": 132}],
                },
                "clips": [],
                "notes": [],
                "midi_events": [],
            },
            {
                "id": 4,
                "host_track_id": None,
                "type": "automation",
                "name": "Time Signature Numerator",
                "mute": False,
                "solo": False,
                "target": {
                    "kind": "time_signature_numerator",
                    "label": "Time Signature Numerator",
                },
                "automation": {
                    "points": [{"beat": 0, "value": 4}, {"beat": 8, "value": 7}],
                },
                "clips": [],
                "notes": [],
                "midi_events": [],
            },
        ],
    }

    await music._sync_project_to_host(project)

    assert ("add_track", {"name": "Lead Volume"}) not in host.commands
    set_automation = next(params for cmd, params in host.commands if cmd == "set_automation")
    assert set_automation["lanes"] == [
        {
            "target": {"kind": "track_volume", "track_id": 1},
            "points": [
                {"beat": 0.0, "value": 0.4, "curve": "linear"},
                {"beat": 4.0, "value": 1.0, "curve": "linear"},
            ],
            "muted": False,
        },
        {
            "target": {"kind": "tempo_bpm"},
            "points": [
                {"beat": 0.0, "value": 120.0, "curve": "linear"},
                {"beat": 4.0, "value": 132.0, "curve": "linear"},
            ],
            "muted": False,
        },
    ]


async def test_sync_project_to_host_samples_midi_controller_curve_segments(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
        "title": "MIDI Curve Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "type": "instrument",
                "name": "Lead",
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [
                    {
                        "type": "control_change",
                        "start": 0.0,
                        "channel": 0,
                        "controller": 11,
                        "value": 0,
                        "curve_amount": 0.25,
                    },
                    {
                        "type": "control_change",
                        "start": 0.125,
                        "channel": 0,
                        "controller": 11,
                        "value": 127,
                    },
                ],
                "clips": [],
                "plugin_slots": [
                    {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                ],
            }
        ],
    }

    await music._sync_project_to_host(project)

    set_midi = next(params for cmd, params in host.commands if cmd == "set_midi")
    cc_events = [
        event
        for event in set_midi["events"]
        if event["type"] == "control_change" and event["controller"] == 11
    ]
    assert [event["start"] for event in cc_events] == [
        0.0,
        0.015625,
        0.03125,
        0.046875,
        0.0625,
        0.078125,
        0.09375,
        0.109375,
        0.125,
    ]
    assert [event["value"] for event in cc_events] == [0, 30, 56, 77, 95, 109, 119, 125, 127]
    assert all("curve_amount" not in event for event in cc_events)


async def test_sync_project_to_host_samples_automation_curve_segments(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
        "title": "Automation Curve Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "type": "instrument",
                "name": "Lead",
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [
                    {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                ],
            },
            {
                "id": 2,
                "host_track_id": None,
                "type": "automation",
                "name": "Lead Volume",
                "target": {"kind": "track_volume", "track_id": 1},
                "automation": {
                    "value_min": 0.0,
                    "value_max": 1.0,
                    "points": [
                        {"beat": 0.0, "value": 0.0, "curve": "linear", "curve_amount": 0.25},
                        {"beat": 0.125, "value": 1.0, "curve": "linear"},
                    ],
                },
                "mute": False,
                "notes": [],
                "midi_events": [],
            },
        ],
    }

    await music._sync_project_to_host(project)

    set_automation = next(params for cmd, params in host.commands if cmd == "set_automation")
    points = set_automation["lanes"][0]["points"]
    assert points == [
        {"beat": 0.0, "value": 0.0, "curve": "linear"},
        {"beat": 0.015625, "value": 0.234375, "curve": "linear"},
        {"beat": 0.03125, "value": 0.4375, "curve": "linear"},
        {"beat": 0.046875, "value": 0.609375, "curve": "linear"},
        {"beat": 0.0625, "value": 0.75, "curve": "linear"},
        {"beat": 0.078125, "value": 0.859375, "curve": "linear"},
        {"beat": 0.09375, "value": 0.9375, "curve": "linear"},
        {"beat": 0.109375, "value": 0.984375, "curve": "linear"},
        {"beat": 0.125, "value": 1.0, "curve": "linear"},
    ]


async def test_sync_project_to_host_sends_route_kind_and_output_bus(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
        "title": "Bus Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "type": "instrument",
                "name": "Kick",
                "output_bus_id": 2,
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [
                    {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                ],
            },
            {
                "id": 2,
                "host_track_id": 2,
                "type": "bus",
                "name": "Drum Bus",
                "output_bus_id": None,
                "volume": 0.9,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [],
            },
        ],
    }

    sync = await music._sync_project_to_host(project)

    configs = [params for cmd, params in host.commands if cmd == "set_route_config"]
    assert configs == [
        {"track_id": 1, "kind": "track", "output_track_id": None},
        {"track_id": 2, "kind": "bus", "output_track_id": None},
        {"track_id": 1, "kind": None, "output_track_id": 2},
        {"track_id": 2, "kind": None, "output_track_id": None},
    ]
    assert sync["routing"] == {"routes": 2, "skipped": []}


async def test_sync_project_to_host_sends_route_send_targets(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
        "title": "Send Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "type": "instrument",
                "name": "Lead",
                "output_bus_id": None,
                "sends": [
                    {"id": "lead-fx", "target_bus_id": 2, "level": 0.5, "enabled": True},
                    {"id": "missing", "target_bus_id": 99, "level": 1.0, "enabled": True},
                ],
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [
                    {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                ],
            },
            {
                "id": 2,
                "host_track_id": 2,
                "type": "bus",
                "name": "FX Bus",
                "output_bus_id": None,
                "sends": [],
                "volume": 0.9,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [],
            },
        ],
    }

    sync = await music._sync_project_to_host(project)

    sends = [params for cmd, params in host.commands if cmd == "set_route_sends"]
    assert sends == [
        {
            "track_id": 1,
            "sends": [{"target_track_id": 2, "level": 0.5, "enabled": True}],
        },
        {"track_id": 2, "sends": []},
    ]
    assert sync["routing"]["skipped"] == [
        {
            "track_id": 1,
            "send_id": "missing",
            "target_bus_id": 99,
            "reason": "send target bus is not synced",
        }
    ]


async def test_sync_project_to_host_resolves_track_to_later_bus_on_fresh_host(monkeypatch):
    host = FreshRoutingHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project: dict[str, Any] = {
        "title": "Fresh Bus Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": None,
                "type": "instrument",
                "name": "Lead",
                "output_bus_id": 2,
                "sends": [{"id": "lead-fx", "target_bus_id": 2, "level": 0.25}],
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [
                    {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                ],
            },
            {
                "id": 2,
                "host_track_id": None,
                "type": "bus",
                "name": "FX Bus",
                "output_bus_id": None,
                "sends": [],
                "volume": 1.0,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [],
            },
        ],
    }

    sync = await music._sync_project_to_host(project)

    assert sync["routing"] == {"routes": 2, "skipped": []}
    assert project["tracks"][0]["host_track_id"] == 1
    assert project["tracks"][1]["host_track_id"] == 2
    configs = [params for cmd, params in host.commands if cmd == "set_route_config"]
    assert configs == [
        {"track_id": 1, "kind": "track", "output_track_id": None},
        {"track_id": 2, "kind": "bus", "output_track_id": None},
        {"track_id": 1, "kind": None, "output_track_id": 2},
        {"track_id": 2, "kind": None, "output_track_id": None},
    ]
    sends = [params for cmd, params in host.commands if cmd == "set_route_sends"]
    assert sends == [
        {
            "track_id": 1,
            "sends": [{"target_track_id": 2, "level": 0.25, "enabled": True}],
        },
        {"track_id": 2, "sends": []},
    ]


async def test_sync_project_to_host_routes_top_level_tracks_through_master_bus(monkeypatch):
    host = FreshRoutingHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project: dict[str, Any] = {
        "title": "Master Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "master_bus": {
            "host_track_id": None,
            "name": "Master Bus",
            "volume": 0.7,
            "pan": -0.25,
            "mute": True,
            "solo": False,
            "plugin_slots": [],
        },
        "tracks": [
            {
                "id": 1,
                "host_track_id": None,
                "type": "instrument",
                "name": "Lead",
                "output_bus_id": None,
                "sends": [],
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [
                    {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                ],
            }
        ],
    }

    sync = await music._sync_project_to_host(project)

    assert project["tracks"][0]["host_track_id"] == 1
    assert project["master_bus"]["host_track_id"] == 2
    configs = [params for cmd, params in host.commands if cmd == "set_route_config"]
    assert configs == [
        {"track_id": 1, "kind": "track", "output_track_id": None},
        {"track_id": 2, "kind": "bus", "output_track_id": None},
        {"track_id": 1, "kind": None, "output_track_id": 2},
        {"track_id": 2, "kind": None, "output_track_id": None},
    ]
    assert ("set_volume", {"track_id": 2, "value": 0.7}) in host.commands
    assert ("set_pan", {"track_id": 2, "value": -0.25}) in host.commands
    assert ("set_mute", {"track_id": 2, "value": True}) in host.commands
    assert ("set_solo", {"track_id": 2, "value": False}) in host.commands
    assert sync["routing"] == {"routes": 2, "skipped": []}


async def test_sync_project_to_host_loads_existing_master_bus_insert_slots(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
        "title": "Master Insert",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "master_bus": {
            "host_track_id": 2,
            "name": "Master Bus",
            "volume": 1.0,
            "pan": 0.0,
            "mute": False,
            "solo": False,
            "plugin_slots": [
                {
                    "id": "insert_1",
                    "type": "vst3",
                    "name": "Limiter",
                    "path": "C:/VST3/Limiter.vst3",
                }
            ],
        },
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "type": "instrument",
                "name": "Lead",
                "output_bus_id": None,
                "sends": [],
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [
                    {"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}
                ],
            }
        ],
    }

    await music._sync_project_to_host(project)

    assert (
        "load_vst3",
        {
            "track_id": 2,
            "slot_index": 1,
            "path": "C:/VST3/Limiter.vst3",
            "name": "Limiter",
        },
    ) in host.commands


async def test_plugin_parameter_metadata_route_translates_project_track(monkeypatch):
    host = ParameterStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)
    monkeypatch.setattr(
        music,
        "load_project",
        lambda: {
            "tracks": [
                {
                    "id": 10,
                    "host_track_id": 4,
                    "type": "instrument",
                    "name": "Lead",
                    "plugin_slots": [{"id": "instrument", "type": "vst3", "name": "Synth"}],
                }
            ]
        },
    )

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.get("/api/music/studio/tracks/10/plugin/parameters?slot_id=instrument")
    body = await response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["project_track_id"] == 10
    assert body["host_track_id"] == 4
    assert body["slot_id"] == "instrument"
    assert body["parameters"][0]["name"] == "Cutoff"
    assert ("list_plugin_parameters", {"track_id": 4, "slot_index": 0}) in host.commands


async def test_studio_set_plugin_parameter_autosaves_captured_state(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    host = StatefulParameterStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)
    music.save_project(
        {
            "title": "Param Save",
            "tempo": 120,
            "time_signature": [4, 4],
            "length_beats": 16,
            "tracks": [
                {
                    "id": 10,
                    "host_track_id": 4,
                    "type": "instrument",
                    "name": "Lead",
                    "plugin_slots": [{"id": "instrument", "type": "vst3", "name": "Synth"}],
                }
            ],
        }
    )

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/plugin/parameter",
        json={"track_id": 10, "slot_id": "instrument", "param_index": 2, "value": 0.64},
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    saved_slot = music.load_project()["tracks"][0]["plugin_slots"][0]
    assert saved_slot["state_b64"] == "AAECAw=="
    assert (
        "get_plugin_state",
        {"track_id": 4, "slot_index": 0},
    ) in host.commands
    assert body["state"][0]["slot_id"] == "instrument"


async def test_captured_plugin_parameters_are_learned_without_creating_tracks(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    host = CapturedParameterStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)
    music.save_project(
        {
            "title": "Learn",
            "tempo": 120,
            "time_signature": [4, 4],
            "length_beats": 16,
            "tracks": [
                {
                    "id": 10,
                    "host_track_id": 4,
                    "type": "instrument",
                    "name": "Lead",
                    "plugin_slots": [{"id": "instrument", "type": "vst3", "name": "Synth"}],
                }
            ],
        }
    )

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.get("/api/music/studio/plugin/captured-parameters")
    body = await response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["captured"][0]["target"]["track_id"] == 10
    assert body["learned_parameters"][0]["name"] == "Lead / Instrument / Synth / Resonance"
    assert body["learned_parameters"][0]["target"]["param_id"] == 900
    assert [track["type"] for track in body["project"]["tracks"]] == ["instrument"]
    assert ("poll_captured_plugin_parameters", {}) in host.commands


async def test_studio_global_automation_write_creates_tempo_track_and_syncs(
    tmp_path,
    monkeypatch,
):
    sync_calls = []
    monkeypatch.chdir(tmp_path)
    music.load_project()

    async def fake_sync(project, *, broadcast=True):
        sync_calls.append({"project": project, "broadcast": broadcast})
        return {"host_running": False, "broadcast": broadcast}

    monkeypatch.setattr(music, "_sync_project_to_host", fake_sync)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/automation/global",
        json={
            "kind": "tempo_bpm",
            "points": [
                {"beat": 0, "value": 90},
                {"beat": 8, "value": 132},
            ],
            "name": "Tempo Map",
        },
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["summary"]["target"] == {"kind": "tempo_bpm", "label": "Tempo BPM"}
    assert body["summary"]["target_status"] == "valid"
    assert body["project"]["tracks"][-1]["name"] == "Tempo Map"
    points = body["project"]["tracks"][-1]["automation"]["points"]
    assert [(point["beat"], point["value"], point["curve"]) for point in points] == [
        (0.0, 90.0, "linear"),
        (8.0, 132.0, "linear"),
    ]
    assert body["sync"] == {"host_running": False, "broadcast": True}
    assert sync_calls and sync_calls[0]["broadcast"] is True


async def test_studio_global_automation_write_rejects_track_targets():
    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/automation/global",
        json={"kind": "track_volume", "points": [{"beat": 0, "value": 1}]},
    )
    body = await response.get_json()

    assert response.status_code == 400
    assert body["error"] == "kind must be tempo_bpm"


async def test_studio_global_automation_write_rejects_time_signature_numerator():
    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/automation/global",
        json={"kind": "time_signature_numerator", "points": [{"beat": 0, "value": 4}]},
    )
    body = await response.get_json()

    assert response.status_code == 400
    assert body["error"] == "kind must be tempo_bpm"


async def test_studio_global_automation_write_rejects_invalid_track_id():
    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/automation/global",
        json={"kind": "tempo_bpm", "track_id": "abc", "points": [{"beat": 0, "value": 120}]},
    )
    body = await response.get_json()

    assert response.status_code == 400
    assert body["error"] == "invalid track_id"


async def test_studio_automation_write_rejects_invalid_track_id():
    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/automation",
        json={
            "target": {"kind": "track_volume", "track_id": 1},
            "track_id": "abc",
            "points": [{"beat": 0, "value": 0.8}],
        },
    )
    body = await response.get_json()

    assert response.status_code == 400
    assert body["error"] == "invalid track_id"


async def test_studio_automation_write_rejects_time_signature_numerator():
    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/automation",
        json={
            "target": {"kind": "time_signature_numerator"},
            "points": [{"beat": 0, "value": 4}],
        },
    )
    body = await response.get_json()

    assert response.status_code == 400
    assert body["error"] == (
        "time_signature_numerator is not an automation target; "
        "use studio_piano_lane_write or studio_piano_lane_diff"
    )


async def test_studio_create_track_passes_requested_color(monkeypatch):
    captured = {}

    def fake_create_track(
        name,
        *,
        color=None,
        track_type="instrument",
        channel_type="multichannel",
    ):
        captured.update(
            {
                "name": name,
                "color": color,
                "track_type": track_type,
                "channel_type": channel_type,
            }
        )
        track = {
            "id": 3,
            "name": name,
            "color": color,
            "type": track_type,
            "channel_type": channel_type,
        }
        return {"tracks": [track]}, track

    async def fake_sync(project, *, broadcast=True):
        return {"host_running": False, "broadcast": broadcast}

    monkeypatch.setattr(music, "create_project_track", fake_create_track)
    monkeypatch.setattr(music, "_sync_project_to_host", fake_sync)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/tracks",
        json={
            "name": "Color Lead",
            "type": "audio",
            "channel_type": "mono",
            "color": "#ff4fa3",
        },
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert captured == {
        "name": "Color Lead",
        "color": "#ff4fa3",
        "track_type": "audio",
        "channel_type": "mono",
    }
    assert body["track"]["color"] == "#ff4fa3"


async def test_studio_create_track_applies_requested_routing(monkeypatch):
    captured_create = {}
    captured_update = {}

    def fake_create_track(
        name,
        *,
        color=None,
        track_type="instrument",
        channel_type="multichannel",
    ):
        captured_create.update(
            {
                "name": name,
                "color": color,
                "track_type": track_type,
                "channel_type": channel_type,
            }
        )
        track = {"id": 3, "name": name, "type": track_type, "sends": []}
        return {"tracks": [track]}, track

    def fake_update_track(track_id, updates):
        captured_update.update({"track_id": track_id, "updates": updates})
        track = {
            "id": track_id,
            "name": "Lead",
            "type": "instrument",
            "output_bus_id": updates["output_bus_id"],
            "sends": updates["sends"],
        }
        return {"tracks": [track]}, track

    async def fake_sync(project, *, broadcast=True):
        return {"host_running": False, "broadcast": broadcast}

    monkeypatch.setattr(music, "create_project_track", fake_create_track)
    monkeypatch.setattr(music, "update_project_track", fake_update_track)
    monkeypatch.setattr(music, "_sync_project_to_host", fake_sync)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/tracks",
        json={
            "name": "Lead",
            "output_bus_id": 2,
            "sends": [{"target_bus_id": 4, "level": 0.35, "enabled": True}],
        },
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert captured_create["name"] == "Lead"
    assert captured_update == {
        "track_id": 3,
        "updates": {
            "output_bus_id": 2,
            "sends": [{"target_bus_id": 4, "level": 0.35, "enabled": True}],
        },
    }
    assert body["track"]["output_bus_id"] == 2


async def test_raw_host_command_rejects_export_render_commands(tmp_path, monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    for cmd in ("bounce", "render_wav"):
        response = await client.post(
            "/api/music/studio/host/command",
            json={
                "cmd": cmd,
                "params": {
                    "path": str(tmp_path / f"{cmd}.wav"),
                    "format": "wav",
                    "start": 0,
                    "end": 1,
                },
            },
        )
        body = await response.get_json()

        assert response.status_code == 403
        assert body["error"] == "command is not allowed through the raw host endpoint"
        assert all(sent_cmd != cmd for sent_cmd, _ in host.commands)


async def test_render_host_wav_waits_without_short_host_timeout(tmp_path):
    timeout_not_passed = object()

    class CaptureBounceTimeoutHost:
        def __init__(self):
            self.timeout = timeout_not_passed

        async def send_command(
            self,
            cmd,
            params=None,
            *,
            response_timeout=timeout_not_passed,
        ):
            self.cmd = cmd
            self.params = params
            self.timeout = response_timeout
            return {"type": "ack", "cmd": cmd, "data": {"path": str(params["path"])}}

    host = CaptureBounceTimeoutHost()

    await music._render_host_wav(
        host,
        tmp_path / "long.wav",
        start=0.0,
        end=120.0,
        track_ids=None,
        sample_rate=48000,
        bit_depth="i24",
    )

    assert host.cmd == "bounce"
    assert host.timeout is None


async def test_studio_export_selected_tracks_mixdown_maps_to_host_track_ids(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    _save_export_project()
    host = ExportStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/export",
        json={
            "target": "selected_tracks",
            "track_ids": [2],
            "mode": "mixdown",
            "format": "wav",
            "sample_rate": 44100,
            "bit_depth": "i24",
        },
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["export"]["format"] == "wav"
    assert body["export"]["mode"] == "mixdown"
    assert body["export"]["download_url"].startswith("/api/music/studio/export/download/")
    bounce = next(params for cmd, params in host.commands if cmd == "bounce")
    assert bounce["track_ids"] == [20]
    assert bounce["sample_rate"] == 44100
    assert bounce["bit_depth"] == "i24"
    assert bounce["start"] == 0.0
    assert bounce["end"] == 8.0
    assert await AsyncPath(body["export"]["path"]).exists()


async def test_studio_export_stems_encodes_each_stem_from_host_wav_and_zips(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    _save_export_project()
    host = ExportStudioHost()
    encode_calls = []
    event_loop_thread = threading.get_ident()

    def fake_encode(source, target, *, format_name, bit_depth, bitrate):
        encode_calls.append(
            {
                "source": Path(source),
                "target": Path(target),
                "format": format_name,
                "bit_depth": bit_depth,
                "bitrate": bitrate,
                "thread": threading.get_ident(),
            }
        )
        Path(target).write_bytes(f"{format_name}:{Path(source).suffix}".encode())

    monkeypatch.setattr(music, "_host_manager", lambda: host)
    monkeypatch.setattr(music, "_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(music, "_run_ffmpeg_encode", fake_encode)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/export",
        json={
            "target": "selected_tracks",
            "track_ids": [1, 2],
            "mode": "stems",
            "format": "flac",
            "sample_rate": 48000,
            "bit_depth": "i24",
        },
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["export"]["filename"].endswith(".zip")
    assert [params["track_ids"] for cmd, params in host.commands if cmd == "bounce"] == [
        [10],
        [20],
    ]
    assert [call["format"] for call in encode_calls] == ["flac", "flac"]
    assert all(call["source"].suffix == ".wav" for call in encode_calls)
    assert all(call["target"].suffix == ".flac" for call in encode_calls)
    assert all(call["thread"] != event_loop_thread for call in encode_calls)

    zip_path = body["export"]["path"]
    assert await AsyncPath(zip_path).exists()
    with zipfile.ZipFile(zip_path) as archive:
        assert sorted(archive.namelist()) == ["Drums.flac", "Lead.flac"]
        assert archive.read("Lead.flac") == b"flac:.wav"
    assert all(not call["target"].exists() for call in encode_calls)


async def test_studio_export_mp3_requires_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _save_export_project()
    host = ExportStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)
    monkeypatch.setattr(music, "_ffmpeg_path", lambda: None)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/export",
        json={"mode": "mixdown", "format": "mp3", "sample_rate": 48000, "bitrate": "320k"},
    )
    body = await response.get_json()

    assert response.status_code == 409
    assert body["ok"] is False
    assert body["error"] == "ffmpeg is required for mp3 export"
    assert all(cmd != "bounce" for cmd, _ in host.commands)


async def test_studio_export_download_streams_file_without_reading_into_memory(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    export_path = music._audio_export_dir() / "large.wav"
    export_bytes = b"RIFF....WAVE"
    export_path.write_bytes(export_bytes)
    export_path_resolved = export_path.resolve()
    original_read_bytes = type(export_path).read_bytes

    def fail_if_export_file_is_buffered(self):
        if self.resolve() == export_path_resolved:
            raise AssertionError("export download must not buffer the whole file")
        return original_read_bytes(self)

    monkeypatch.setattr(type(export_path), "read_bytes", fail_if_export_file_is_buffered)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.get("/api/music/studio/export/download/large.wav")

    assert response.status_code == 200
    assert await response.get_data() == export_bytes
    assert response.headers["Content-Disposition"] == 'attachment; filename="large.wav"'


async def test_studio_import_audio_rejects_host_unsupported_extension_with_type_error():
    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/audio/import",
        files={
            "file": FileStorage(
                stream=io.BytesIO(b"not playable"),
                filename="loop.ogg",
                content_type="audio/ogg",
            )
        },
    )
    body = await response.get_json()

    assert response.status_code == 400
    assert body["type"] == "error"
    assert body["error_type"] == "type_error"
    assert body["error"] == "unsupported audio file type"


async def test_studio_import_audio_file_imports_workspace_file_with_ai_friendly_payload(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    source_dir = workspace / "samples"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "drum.wav"
    source_path.write_bytes(b"RIFF....WAVE")
    import_dir = tmp_path / "imports"
    captured: dict[str, Any] = {}

    def fake_import_audio_clip(path, **kwargs):
        captured["path"] = path
        captured["kwargs"] = kwargs
        clip = {"id": "clip_drum", "path": str(path), "name": kwargs["name"]}
        track = {"id": 11, "name": "Main Beat", "clips": [clip]}
        return {"tracks": [track]}, track, clip

    async def fake_sync(project, *, broadcast=True):
        captured["sync_broadcast"] = broadcast
        return {"host_running": False, "commands": [], "project": project}

    monkeypatch.setattr(music, "_lifecycle", SimpleNamespace(config={"workspace": str(workspace)}))
    monkeypatch.setattr(music, "_audio_import_dir", lambda: import_dir)
    monkeypatch.setattr(music, "import_audio_clip", fake_import_audio_clip)
    monkeypatch.setattr(music, "_sync_project_to_host", fake_sync)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/audio/import-file",
        json={
            "path": "samples/drum.wav",
            "name": "Main Beat",
            "start_beat": 8,
            "duration_seconds": 3.5,
            "waveform": [0.25, {"min": -0.5, "max": 0.75}],
        },
    )
    body = await response.get_json()

    assert response.status_code == 200
    assert captured["path"].parent == import_dir
    assert captured["path"].name.endswith("_drum.wav")
    assert captured["path"].read_bytes() == b"RIFF....WAVE"
    assert captured["kwargs"]["name"] == "Main Beat"
    assert captured["kwargs"]["start"] == 8.0
    assert captured["kwargs"]["duration_seconds"] == 3.5
    assert captured["kwargs"]["waveform"][0] == 0.25
    assert captured["sync_broadcast"] is False
    assert body["ok"] is True
    assert body["clip"]["id"] == "clip_drum"


async def test_studio_import_audio_file_rejects_unsupported_extension_with_type_error(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "loop.ogg").write_bytes(b"not playable")

    monkeypatch.setattr(music, "_lifecycle", SimpleNamespace(config={"workspace": str(workspace)}))

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/audio/import-file",
        json={"file_path": "loop.ogg"},
    )
    body = await response.get_json()

    assert response.status_code == 400
    assert body["type"] == "error"
    assert body["error_type"] == "type_error"
    assert body["error"] == "unsupported audio file type"


async def test_studio_import_audio_file_rejects_paths_outside_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFF....WAVE")

    monkeypatch.setattr(music, "_lifecycle", SimpleNamespace(config={"workspace": str(workspace)}))

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/audio/import-file",
        json={"path": str(outside)},
    )
    body = await response.get_json()

    assert response.status_code == 403
    assert body["error"] == "path outside workspace"


async def test_studio_import_audio_returns_type_error_when_host_rejects_clip(tmp_path, monkeypatch):
    def fake_import_audio_clip(path, **kwargs):
        track = {"id": 7, "name": "Bad Clip"}
        clip = {"id": "clip_bad", "path": str(path)}
        return {"tracks": [track]}, track, clip

    async def fake_sync(project, *, broadcast=True):
        return {
            "host_running": True,
            "commands": [
                {
                    "type": "error",
                    "cmd": "set_audio_clips",
                    "message": "failed to decode audio clip",
                }
            ],
        }

    monkeypatch.setattr(music, "_audio_import_dir", lambda: tmp_path)
    monkeypatch.setattr(music, "import_audio_clip", fake_import_audio_clip)
    monkeypatch.setattr(music, "_sync_project_to_host", fake_sync)

    app = Quart(__name__)
    app.register_blueprint(music.bp)
    client = app.test_client()

    response = await client.post(
        "/api/music/studio/audio/import",
        files={
            "file": FileStorage(
                stream=io.BytesIO(b"RIFF....WAVE"),
                filename="bad.wav",
                content_type="audio/wav",
            )
        },
    )
    body = await response.get_json()

    assert response.status_code == 400
    assert body["type"] == "error"
    assert body["error_type"] == "type_error"
    assert body["error"] == "failed to decode audio clip"
