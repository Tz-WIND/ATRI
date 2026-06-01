import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from core.agent.session import SessionStore
from core.platform.message import MessageEvent, MessageType, Sender
from dashboard import music as music_routes
from dashboard.routes import _helpers
from dashboard.server import Dashboard

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle


class _FakeHost:
    def set_audio_callback(self, callback):
        self.callback = callback


class _FakeDawAgent:
    def create_event(
        self,
        message,
        project_session_id,
        *,
        instance_id="",
        workspace="atri_studio",
        host_context=None,
        images=None,
        model="",
        model_provider="",
    ):
        event = MessageEvent(
            message_str=message,
            message_type=MessageType.FRIEND_MESSAGE,
            sender=Sender(user_id="daw_user", nickname="DAW"),
            session_id=project_session_id,
            self_id="atri",
            platform_name="daw_agent",
        )
        future = asyncio.get_event_loop().create_future()
        future.set_result({"text": "pong", "chain": None})
        return event, future


class _FakeLifecycle:
    def __init__(self, tmp_path):
        self.config = {
            "dashboard": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 6185,
                "username": "admin",
                "password": _helpers.hash_password("secret"),
            },
            "workspace": str(tmp_path),
            "audio_host": {},
            "mcp_servers": {},
            "model": "chat-current",
            "model_provider": "",
            "active_models": [],
            "embedding_model": "",
            "embedding_provider": "",
            "active_embedding_models": [],
            "rerank_model": "",
            "rerank_provider": "",
            "active_rerank_models": [],
            "providers": {},
            "max_tokens": 4096,
            "temperature": 0.0,
            "max_context_tokens": 128000,
            "max_rounds": 50,
        }
        self.process_stage = None
        self.daw_agent = _FakeDawAgent()
        self.webchat = None
        self.onebot11 = None
        self.start_time = 0

    def save_config(self):
        pass


def _dashboard(monkeypatch, tmp_path):
    monkeypatch.setattr(_helpers, "_PBKDF2_ITERATIONS", 1)
    monkeypatch.setattr("core.host.configure_host_manager", lambda **kwargs: _FakeHost())
    monkeypatch.setattr(music_routes, "init_music", lambda lifecycle: None)
    dashboard = Dashboard(cast("Lifecycle", _FakeLifecycle(tmp_path)))
    dashboard_any = cast(Any, dashboard)
    dashboard_any.broadcasts = []

    async def broadcast(payload):
        dashboard_any.broadcasts.append(payload)

    dashboard_any.broadcast = broadcast
    return dashboard


@pytest.mark.asyncio
async def test_dashboard_auth_still_blocks_regular_api(monkeypatch, tmp_path):
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    response = await client.get("/api/workspace")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_local_bridge_status_bypasses_dashboard_login(monkeypatch, tmp_path):
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    response = await client.get(
        "/api/music/studio/bridge/status",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["bridge"]["local_only"] is True


@pytest.mark.asyncio
async def test_local_bridge_context_bypasses_dashboard_login(monkeypatch, tmp_path):
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    response = await client.post(
        "/api/music/studio/bridge/context",
        json={
            "instance_id": "bridge-auth",
            "host": "REAPER",
            "host_context": {"tempo_bpm": 128},
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["ok"] is True
    assert payload["context"] == {"host": "REAPER", "tempo_bpm": 128}


@pytest.mark.asyncio
async def test_bridge_context_rejects_empty_payload(monkeypatch, tmp_path):
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    response = await client.post(
        "/api/music/studio/bridge/context",
        json={"instance_id": "bridge-empty-context", "host": " ", "host_context": {}},
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert response.status_code == 400
    payload = await response.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "bridge context must include at least one supported field"
    assert music_routes.bridge_host_context_for_instance("bridge-empty-context") == {}


@pytest.mark.asyncio
async def test_local_daw_agent_chat_bypasses_dashboard_login(monkeypatch, tmp_path):
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    response = await client.post(
        "/api/daw-agent/chat",
        json={
            "message": "ping",
            "project_session_id": "atri-session",
            "instance_id": "bridge-probe",
            "workspace": "atri_studio",
            "host_context": {"host": "Studio One", "workspace": "atri_studio"},
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert response.status_code == 200
    assert await response.get_json() == {
        "response": "pong",
        "chain": None,
        "session_id": "daw_agent:friend:atri-session",
    }


@pytest.mark.asyncio
async def test_local_bridge_status_and_model_select_bypass_dashboard_login(monkeypatch, tmp_path):
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()
    scope = {"scope_base": {"client": ("127.0.0.1", 54000)}}

    status_response = await client.get("/api/status", **scope)
    select_response = await client.post(
        "/api/provider/select",
        json={"provider": "OpenAI", "model": "chat-current"},
        **scope,
    )

    assert status_response.status_code == 200
    assert select_response.status_code == 200
    assert await select_response.get_json() == {"ok": True}


@pytest.mark.asyncio
async def test_local_daw_agent_session_history_bypasses_dashboard_login(monkeypatch, tmp_path):
    dashboard = _dashboard(monkeypatch, tmp_path)
    store = SessionStore(tmp_path / "sessions")
    messages = [{"role": "user", "content": "restore this project chat"}]
    store.save(messages, "chat-current", "daw_agent:friend:atri-session")
    dashboard.lifecycle.process_stage = SimpleNamespace(session_store=store, todo_store=None)
    client = dashboard.app.test_client()

    response = await client.get(
        "/api/sessions/daw_agent%3Afriend%3Aatri-session",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert response.status_code == 200
    assert await response.get_json() == {
        "messages": messages,
        "model": "chat-current",
        "runtime_turns": [],
        "runtime_items": [],
        "todo_snapshot": None,
    }


@pytest.mark.asyncio
async def test_local_daw_agent_music_project_bypasses_dashboard_login(monkeypatch, tmp_path):
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    response = await client.get(
        "/api/music/studio/project",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["project"]["title"]


@pytest.mark.asyncio
async def test_local_daw_agent_music_export_bypasses_dashboard_login(monkeypatch, tmp_path):
    monkeypatch.setattr(
        music_routes,
        "load_project",
        lambda: {
            "title": "Bridge MIDI",
            "tempo": 120,
            "time_signature": [4, 4],
            "tracks": [
                {
                    "id": 1,
                    "type": "instrument",
                    "name": "Lead",
                    "notes": [{"pitch": 60, "start": 0, "duration": 1, "velocity": 96}],
                    "midi_events": [],
                }
            ],
        },
    )
    monkeypatch.setattr(music_routes, "_audio_export_dir", lambda: tmp_path / "exports")
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    response = await client.post(
        "/api/music/studio/export",
        json={"format": "midi", "target": "entire_project", "consumer": "bridge"},
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["ok"] is True
    assert payload["export"]["format"] == "midi"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/api/agent-mode", None),
        ("post", "/api/chat/cancel", {"session_id": "daw_agent:friend:atri-session"}),
        ("post", "/api/daw-agent/chat", {"message": "ping"}),
        ("get", "/api/sessions/daw_agent%3Afriend%3Aatri-session", None),
        ("get", "/api/music/studio/project", None),
        ("post", "/api/music/studio/export", {"format": "midi"}),
        ("get", "/api/music/studio/bridge/status", None),
        ("post", "/api/music/studio/bridge/export", {"format": "midi"}),
        ("get", "/api/music/studio/bridge/export/latest", None),
        ("post", "/api/music/studio/bridge/context", {"host_context": {"tempo_bpm": 128}}),
    ],
)
async def test_non_loopback_local_bridge_routes_require_dashboard_login(
    monkeypatch,
    tmp_path,
    method,
    path,
    json,
):
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()
    request_method = getattr(client, method)

    kwargs = {"scope_base": {"client": ("192.168.1.44", 54000)}}
    if json is not None:
        kwargs["json"] = json
    response = await request_method(path, **kwargs)

    assert response.status_code == 401
    assert await response.get_json() == {"error": "authentication required"}


@pytest.mark.asyncio
async def test_bridge_latest_export_tracks_daw_agent_midi_export(monkeypatch, tmp_path):
    monkeypatch.setattr(
        music_routes,
        "load_project",
        lambda: {
            "title": "Bridge MIDI",
            "tempo": 120,
            "time_signature": [4, 4],
            "tracks": [
                {
                    "id": 1,
                    "type": "instrument",
                    "name": "Lead",
                    "notes": [{"pitch": 60, "start": 0, "duration": 1, "velocity": 96}],
                    "midi_events": [],
                }
            ],
        },
    )
    monkeypatch.setattr(music_routes, "_audio_export_dir", lambda: tmp_path / "exports")
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    export_response = await client.post(
        "/api/music/studio/export",
        json={
            "format": "midi",
            "target": "selected_tracks",
            "track_ids": [1],
            "consumer": "bridge",
            "start_beat": 0,
            "end_beat": 1,
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )
    latest_response = await client.get(
        "/api/music/studio/bridge/export/latest",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert export_response.status_code == 200
    assert latest_response.status_code == 200
    latest = await latest_response.get_json()
    exported = await export_response.get_json()
    assert latest["ok"] is True
    assert latest["bridge"]["local_only"] is True
    assert latest["export"]["path"] == exported["export"]["path"]
    assert latest["export"]["format"] == "midi"
    assert latest["export"]["beat_range"] == [0.0, 1.0]
    assert latest["export"]["bridge_preview"] == {
        "kind": "midi_region",
        "track_id": 1,
        "track_name": "Lead",
        "beat_range": [0.0, 1.0],
        "range_source": "explicit",
        "filename": exported["export"]["filename"],
        "track_count": 1,
        "selection": {"project_track_ids": [1], "range_beats": [0.0, 1.0]},
        "note_count": 1,
        "pitch_range": [60, 60],
        "tracks": [
            {
                "track_id": 1,
                "track_name": "Lead",
                "note_count": 1,
                "pitch_range": [60, 60],
            },
        ],
    }


@pytest.mark.asyncio
async def test_bridge_latest_export_preserves_preview_for_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(
        music_routes,
        "load_project",
        lambda: {
            "title": "Bridge MIDI",
            "tempo": 120,
            "time_signature": [4, 4],
            "tracks": [
                {
                    "id": 1,
                    "type": "instrument",
                    "name": "Lead",
                    "notes": [
                        {"pitch": 60, "start": 0, "duration": 0.5, "velocity": 96},
                        {"pitch": 67, "start": 0.5, "duration": 0.5, "velocity": 96},
                    ],
                    "midi_events": [],
                }
            ],
        },
    )
    monkeypatch.setattr(music_routes, "_audio_export_dir", lambda: tmp_path / "exports")
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    export_response = await client.post(
        "/api/music/studio/export",
        json={
            "format": "midi",
            "target": "selected_tracks",
            "track_ids": [1],
            "consumer": "bridge",
            "instance_id": "bridge-preview",
            "start_beat": 0,
            "end_beat": 1,
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )
    latest_response = await client.get(
        "/api/music/studio/bridge/export/latest?instance_id=bridge-preview",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert export_response.status_code == 200
    assert latest_response.status_code == 200
    latest = await latest_response.get_json()
    assert latest["export"]["bridge_scope"] == {"instance_id": "bridge-preview"}
    assert latest["export"]["bridge_preview"] == {
        "kind": "midi_region",
        "track_id": 1,
        "track_name": "Lead",
        "beat_range": [0.0, 1.0],
        "range_source": "explicit",
        "filename": latest["export"]["filename"],
        "track_count": 1,
        "selection": {"project_track_ids": [1], "range_beats": [0.0, 1.0]},
        "note_count": 2,
        "pitch_range": [60, 67],
        "tracks": [
            {
                "track_id": 1,
                "track_name": "Lead",
                "note_count": 2,
                "pitch_range": [60, 67],
            },
        ],
    }


@pytest.mark.asyncio
async def test_bridge_latest_export_preview_includes_multiple_tracks(monkeypatch, tmp_path):
    monkeypatch.setattr(
        music_routes,
        "load_project",
        lambda: {
            "title": "Bridge MIDI",
            "tempo": 120,
            "time_signature": [4, 4],
            "length_beats": 4,
            "tracks": [
                {
                    "id": 1,
                    "name": "Lead",
                    "type": "instrument",
                    "notes": [
                        {"pitch": 60, "start": 0, "duration": 0.5, "velocity": 96},
                        {"pitch": 67, "start": 0.5, "duration": 0.5, "velocity": 96},
                    ],
                    "midi_events": [],
                },
                {
                    "id": 2,
                    "name": "Bass",
                    "type": "instrument",
                    "notes": [
                        {"pitch": 36, "start": 0, "duration": 1, "velocity": 96},
                    ],
                    "midi_events": [],
                },
            ],
        },
    )
    monkeypatch.setattr(music_routes, "_audio_export_dir", lambda: tmp_path / "exports")
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    export_response = await client.post(
        "/api/music/studio/export",
        json={
            "format": "midi",
            "target": "selected_tracks",
            "track_ids": [1, 2],
            "consumer": "bridge",
            "start_beat": 0,
            "end_beat": 1,
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )
    latest_response = await client.get(
        "/api/music/studio/bridge/export/latest",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert export_response.status_code == 200
    assert latest_response.status_code == 200
    latest = await latest_response.get_json()
    assert latest["export"]["bridge_preview"] == {
        "kind": "midi_region",
        "track_id": 1,
        "track_name": "Lead",
        "beat_range": [0.0, 1.0],
        "range_source": "explicit",
        "filename": latest["export"]["filename"],
        "track_count": 2,
        "selection": {"project_track_ids": [1, 2], "range_beats": [0.0, 1.0]},
        "note_count": 3,
        "pitch_range": [36, 67],
        "tracks": [
            {
                "track_id": 1,
                "track_name": "Lead",
                "note_count": 2,
                "pitch_range": [60, 67],
            },
            {
                "track_id": 2,
                "track_name": "Bass",
                "note_count": 1,
                "pitch_range": [36, 36],
            },
        ],
    }


@pytest.mark.asyncio
async def test_bridge_export_derives_midi_scope_from_published_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(
        music_routes,
        "load_project",
        lambda: {
            "title": "Bridge Selection",
            "tempo": 120,
            "time_signature": [4, 4],
            "tracks": [
                {
                    "id": 1,
                    "host_track_id": 0,
                    "type": "instrument",
                    "name": "Lead",
                    "notes": [
                        {"pitch": 60, "start": 1, "duration": 0.5, "velocity": 96},
                        {"pitch": 64, "start": 5, "duration": 1.0, "velocity": 96},
                    ],
                    "midi_events": [],
                },
                {
                    "id": 2,
                    "host_track_id": 8,
                    "type": "instrument",
                    "name": "Bass",
                    "notes": [{"pitch": 36, "start": 5, "duration": 1.0, "velocity": 96}],
                    "midi_events": [],
                },
            ],
        },
    )
    monkeypatch.setattr(music_routes, "_audio_export_dir", lambda: tmp_path / "exports")
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    context_response = await client.post(
        "/api/music/studio/bridge/context",
        json={
            "instance_id": "bridge-selection",
            "host_context": {
                "selection": {
                    "range_beats": [4, 8],
                    "host_track_ids": [0],
                },
                "loop_active": True,
                "loop_range_beats": [0, 12],
            },
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )
    export_response = await client.post(
        "/api/music/studio/bridge/export",
        json={"format": "midi", "instance_id": "bridge-selection"},
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert context_response.status_code == 200
    assert export_response.status_code == 200
    payload = await export_response.get_json()
    assert payload["export"]["target"] == "selected_tracks"
    assert payload["export"]["track_ids"] == [1]
    assert payload["export"]["beat_range"] == [4.0, 8.0]
    assert payload["export"]["bridge_export"]["range_source"] == "selection"
    assert payload["export"]["bridge_preview"]["range_source"] == "selection"


@pytest.mark.asyncio
async def test_bridge_export_derives_midi_range_from_active_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(
        music_routes,
        "load_project",
        lambda: {
            "title": "Bridge Loop",
            "tempo": 120,
            "time_signature": [4, 4],
            "tracks": [
                {
                    "id": 1,
                    "type": "instrument",
                    "name": "Lead",
                    "notes": [
                        {"pitch": 60, "start": 1, "duration": 0.5, "velocity": 96},
                        {"pitch": 64, "start": 5, "duration": 1.0, "velocity": 96},
                    ],
                    "midi_events": [],
                }
            ],
        },
    )
    monkeypatch.setattr(music_routes, "_audio_export_dir", lambda: tmp_path / "exports")
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    await client.post(
        "/api/music/studio/bridge/context",
        json={
            "instance_id": "bridge-loop",
            "host_context": {"loop_active": True, "loop_range_beats": [4, 8]},
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )
    export_response = await client.post(
        "/api/music/studio/bridge/export",
        json={"format": "midi", "instance_id": "bridge-loop"},
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert export_response.status_code == 200
    payload = await export_response.get_json()
    assert payload["export"]["beat_range"] == [4.0, 8.0]
    assert payload["export"]["bridge_export"]["range_source"] == "loop"


@pytest.mark.asyncio
async def test_bridge_latest_export_ignores_missing_primary_file(monkeypatch, tmp_path):
    monkeypatch.setattr(music_routes, "_audio_export_dir", lambda: tmp_path / "exports")
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    missing_path = export_dir / "missing.mid"

    music_routes._write_latest_bridge_export(
        {
            "format": "midi",
            "path": str(missing_path),
            "bridge_scope": {"instance_id": "bridge-missing"},
        },
        instance_id="bridge-missing",
    )

    response = await client.get(
        "/api/music/studio/bridge/export/latest?instance_id=bridge-missing",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert response.status_code == 200
    assert (await response.get_json())["export"] is None
