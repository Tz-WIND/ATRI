import io
from typing import Any

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

    async def send_command(self, cmd, params=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "get_status":
            return {"tracks": [{"id": 1}, {"id": 2}]}
        return {"type": "ack", "cmd": cmd}


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


async def test_load_track_slots_does_not_load_builtin_for_empty_bus():
    host = FakeStudioHost()

    responses = await music._load_track_slots(
        host,
        2,
        {"type": "bus", "instrument": "Bus", "plugin_slots": []},
    )

    assert responses == []
    assert host.commands == []


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
        {
            "target": {"kind": "time_signature_numerator"},
            "points": [
                {"beat": 0.0, "value": 4.0, "curve": "linear"},
                {"beat": 8.0, "value": 7.0, "curve": "linear"},
            ],
            "muted": False,
        },
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

    project = {
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

    project = {
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
