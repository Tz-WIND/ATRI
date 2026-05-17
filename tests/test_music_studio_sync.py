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


async def test_sync_project_to_host_removes_stale_host_tracks(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
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
