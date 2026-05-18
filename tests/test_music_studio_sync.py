import io
from typing import Any

from quart import Quart
from werkzeug.datastructures import FileStorage

from dashboard import music


class FakeStudioHost:
    def __init__(self):
        self.is_running = True
        self.commands = []

    async def send_command(self, cmd, params=None):
        params = params or {}
        self.commands.append((cmd, params))
        if cmd == "get_status":
            return {"tracks": [{"id": 1}, {"id": 2}]}
        return {"type": "ack", "cmd": cmd}


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
