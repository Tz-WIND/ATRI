import asyncio
from types import SimpleNamespace

import pytest

from core.agent.session import SessionStore
from core.platform.message import MessageEvent, MessageType, Sender
from dashboard import music as music_routes
from dashboard.routes import _helpers
from dashboard.server import Dashboard


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
    dashboard = Dashboard(_FakeLifecycle(tmp_path))
    dashboard.broadcasts = []

    async def broadcast(payload):
        dashboard.broadcasts.append(payload)

    dashboard.broadcast = broadcast
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
