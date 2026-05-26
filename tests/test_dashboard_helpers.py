import io
import json
import zipfile
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from core.tools import novelai_image
from core.tools.novelai_image import NovelAIImageTool
from dashboard import music as music_routes
from dashboard.routes import _helpers, chat, management, models
from dashboard.server import DASHBOARD_MAX_CONTENT_LENGTH, Dashboard

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle

EXPECTED_CHAT_MODEL_CONFIG_DEFAULT = {
    "max_tokens": 4096,
    "temperature": 0.0,
    "max_context_tokens": 128000,
    "max_rounds": 50,
}
EXPECTED_EMBEDDING_MODEL_CONFIG_DEFAULT = {
    "dimensions": 1536,
    "batch_size": 64,
    "encoding_format": "float",
}
EXPECTED_RERANK_MODEL_CONFIG_DEFAULT = {
    "top_n": 5,
    "score_threshold": 0.0,
    "max_input_tokens": 8192,
}


class _FakeDashboardLifecycle:
    def __init__(self, tmp_path, password_hash: str):
        self.config = {
            "dashboard": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 6185,
                "username": "admin",
                "password": password_hash,
            },
            "workspace": str(tmp_path),
            "audio_host": {},
            "mcp_servers": {},
            "model": "chat-current",
            "model_provider": "OpenAI",
            "active_models": [{"model": "chat-current", "provider": "OpenAI"}],
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
        self.onebot11 = None
        self.webchat = None
        self.start_time = 0
        self.saved = 0

    def save_config(self):
        self.saved += 1


class _FakeDashboardHost:
    def set_audio_callback(self, callback):
        self.callback = callback


class _FakeStreamingDashboardHost:
    def __init__(self, *, running: bool = True):
        self.is_running = running
        self.sample_rate = 48000
        self.buffer_size = 256
        self.audio_engine = "default"
        self.bit_depth = "f32"
        self.binary_path = None
        self.commands: list[tuple[str, dict[str, Any]]] = []

    def set_audio_callback(self, callback):
        self.callback = callback

    async def start(self):
        self.is_running = True

    async def send_command(self, cmd, params=None):
        self.commands.append((cmd, params or {}))
        return {"type": "ack", "cmd": cmd, "status": "ok"}


class _FailingAudioWebSocket:
    def __init__(self):
        self.send_calls = 0
        self.close_calls = 0

    async def send(self, _message):
        self.send_calls += 1
        raise RuntimeError("send failed")

    async def close(self, _code=1000):
        self.close_calls += 1


def _set_test_dashboard_password_cost(monkeypatch):
    monkeypatch.setattr(_helpers, "_PBKDF2_ITERATIONS", 1)


def _dashboard_for_auth_tests(monkeypatch, tmp_path) -> Dashboard:
    _set_test_dashboard_password_cost(monkeypatch)
    monkeypatch.setattr(
        "core.host.configure_host_manager",
        lambda **kwargs: _FakeDashboardHost(),
    )
    monkeypatch.setattr(music_routes, "init_music", lambda lifecycle: None)
    lifecycle = _FakeDashboardLifecycle(tmp_path, _helpers.hash_password("secret"))
    return Dashboard(
        cast("Lifecycle", lifecycle),
    )


def _auth_cookie_from_response(response) -> str:
    cookie = SimpleCookie()
    cookie.load(response.headers["Set-Cookie"])
    return cookie[_helpers.AUTH_COOKIE].value


def test_dashboard_upload_limit_is_finite_and_large_enough_for_audio_imports():
    assert DASHBOARD_MAX_CONTENT_LENGTH == 512 * 1024 * 1024
    assert DASHBOARD_MAX_CONTENT_LENGTH is not None


def test_dashboard_audio_waveform_form_accepts_structured_metrics():
    waveform = music_routes._audio_waveform_from_form(
        json.dumps(
            [
                {"min": -0.4, "max": 0.7, "rms": 0.25, "peak": 0.7},
                {"min": 0.6, "max": -0.2, "rms": 0.2},
                0.5,
            ]
        )
    )

    assert waveform == [
        {"min": -0.4, "max": 0.7, "rms": 0.25, "peak": 0.7},
        {"min": -0.2, "max": 0.6, "rms": 0.2, "peak": 0.6},
        0.5,
    ]


@pytest.mark.asyncio
async def test_dashboard_audio_client_registration_enables_host_streaming(monkeypatch, tmp_path):
    _set_test_dashboard_password_cost(monkeypatch)
    host = _FakeStreamingDashboardHost()
    monkeypatch.setattr("core.host.configure_host_manager", lambda **kwargs: host)
    monkeypatch.setattr("core.host.get_host_manager", lambda: host)
    monkeypatch.setattr(music_routes, "init_music", lambda lifecycle: None)
    dashboard = Dashboard(
        cast("Lifecycle", _FakeDashboardLifecycle(tmp_path, _helpers.hash_password("secret"))),
    )

    ws_obj = object()
    await dashboard.register_audio_client(ws_obj)

    assert ws_obj in dashboard._audio_clients
    assert host.commands == [("set_streaming", {"enabled": True})]


@pytest.mark.asyncio
async def test_dashboard_audio_streaming_commands_follow_client_count_edges(monkeypatch, tmp_path):
    _set_test_dashboard_password_cost(monkeypatch)
    host = _FakeStreamingDashboardHost()
    monkeypatch.setattr("core.host.configure_host_manager", lambda **kwargs: host)
    monkeypatch.setattr("core.host.get_host_manager", lambda: host)
    monkeypatch.setattr(music_routes, "init_music", lambda lifecycle: None)
    dashboard = Dashboard(
        cast("Lifecycle", _FakeDashboardLifecycle(tmp_path, _helpers.hash_password("secret"))),
    )
    first = object()
    second = object()

    await dashboard.register_audio_client(first)
    await dashboard.register_audio_client(second)
    await dashboard.discard_audio_client(first)
    await dashboard.discard_audio_client(second)

    assert dashboard._audio_clients == set()
    assert host.commands == [
        ("set_streaming", {"enabled": True}),
        ("set_streaming", {"enabled": False}),
    ]


def test_audio_websocket_route_uses_dashboard_audio_client_lifecycle():
    text = Path("dashboard/routes/websocket.py").read_text(encoding="utf-8")

    assert "await dashboard.register_audio_client(ws_obj)" in text
    assert "await dashboard.discard_audio_client(ws_obj)" in text


@pytest.mark.asyncio
async def test_dashboard_audio_send_failure_does_not_stop_handler_owned_streaming(
    monkeypatch, tmp_path
):
    _set_test_dashboard_password_cost(monkeypatch)
    host = _FakeStreamingDashboardHost()
    monkeypatch.setattr("core.host.configure_host_manager", lambda **kwargs: host)
    monkeypatch.setattr("core.host.get_host_manager", lambda: host)
    monkeypatch.setattr(music_routes, "init_music", lambda lifecycle: None)
    dashboard = Dashboard(
        cast("Lifecycle", _FakeDashboardLifecycle(tmp_path, _helpers.hash_password("secret"))),
    )
    ws_obj = _FailingAudioWebSocket()

    await dashboard.register_audio_client(ws_obj)
    await dashboard.broadcast_audio(b"\x00" * 8, nframes=1, channels=2)
    await dashboard.broadcast_audio(b"\x00" * 8, nframes=1, channels=2)

    assert ws_obj in dashboard._audio_clients
    assert ws_obj in dashboard._audio_send_failed_clients
    assert ws_obj.send_calls == 1
    assert ws_obj.close_calls == 1
    assert host.commands == [("set_streaming", {"enabled": True})]


@pytest.mark.asyncio
async def test_dashboard_replays_audio_streaming_when_host_starts_with_connected_client(
    monkeypatch, tmp_path
):
    _set_test_dashboard_password_cost(monkeypatch)
    host = _FakeStreamingDashboardHost(running=False)
    monkeypatch.setattr("core.host.configure_host_manager", lambda **kwargs: host)
    monkeypatch.setattr("core.host.get_host_manager", lambda: host)
    lifecycle = _FakeDashboardLifecycle(tmp_path, _helpers.hash_password("secret"))
    dashboard = Dashboard(cast("Lifecycle", lifecycle))
    lifecycle.dashboard = dashboard
    token = dashboard._create_auth_session()
    ws_obj = object()

    await dashboard.register_audio_client(ws_obj)
    response = await dashboard.app.test_client().post(
        "/api/music/studio/host/start",
        json={"sync": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = await response.get_json()

    assert response.status_code == 200
    assert payload["host"]["running"] is True
    assert host.commands == [("set_streaming", {"enabled": True})]


def test_dashboard_password_hash_verify_and_legacy_plaintext(monkeypatch):
    monkeypatch.setattr(_helpers, "_PBKDF2_ITERATIONS", 1)

    stored = _helpers.hash_password("secret")

    assert stored.startswith("pbkdf2:")
    assert _helpers.verify_password(stored, "secret") is True
    assert _helpers.verify_password(stored, "wrong") is False
    assert _helpers.verify_password("legacy-secret", "legacy-secret") is True
    assert _helpers.verify_password("", "secret") is False
    assert _helpers.verify_password("pbkdf2:malformed", "secret") is False


def test_dashboard_rate_limit_tracks_failures(monkeypatch):
    _helpers._rate_limit_buckets.clear()
    monkeypatch.setattr(_helpers.time, "time", lambda: 1000.0)

    for _ in range(_helpers._RATE_LIMIT_MAX_FAILURES):
        assert _helpers.check_rate_limit("127.0.0.1") is False
        _helpers.record_failure("127.0.0.1")

    assert _helpers.check_rate_limit("127.0.0.1") is True

    monkeypatch.setattr(_helpers.time, "time", lambda: 1000.0 + _helpers._RATE_LIMIT_WINDOW + 1)
    assert _helpers.check_rate_limit("127.0.0.1") is False


def test_dashboard_masks_provider_api_keys_without_mutating_input():
    providers: dict[str, Any] = {
        "openai": {"api_key": "sk-test", "base_url": "https://example.test"},
        "local": {"api_key": ""},
        "bad": "not-a-dict",
    }

    masked = models._mask_providers(providers)

    assert masked == {
        "openai": {"api_key": "***", "base_url": "https://example.test"},
        "local": {"api_key": ""},
    }
    assert providers["openai"]["api_key"] == "sk-test"


def test_dashboard_masks_and_merges_image_transcription_config():
    existing = {
        "enabled": False,
        "model": "old-vision",
        "api_key": "sk-existing",
        "base_url": "https://old.example/v1",
        "api_format": "openai",
        "prompt": "old prompt",
        "max_tokens": 512,
        "temperature": 0.1,
    }

    assert models._mask_image_transcription(existing)["api_key"] == "***"

    merged = models._merge_image_transcription_config(
        existing,
        {
            "enabled": True,
            "model": "new-vision",
            "api_key": "***",
            "max_tokens": "1024",
            "temperature": "0.25",
        },
    )

    assert merged["enabled"] is True
    assert merged["model"] == "new-vision"
    assert merged["api_key"] == "sk-existing"
    assert merged["max_tokens"] == 1024
    assert merged["temperature"] == 0.25


def test_dashboard_masks_and_merges_novelai_config():
    existing = {
        "api_key": "nai-existing",
        "base_url": "https://image.novelai.net",
        "model": "nai-old",
    }

    assert novelai_image.mask_novelai_config(existing)["api_key"] == "***"

    merged = novelai_image.merge_novelai_config(
        existing,
        {
            "api_key": "***",
            "base_url": "https://image.example.test",
            "model": "nai-new",
        },
    )

    assert merged["api_key"] == "nai-existing"
    assert merged["base_url"] == "https://image.example.test"
    assert merged["model"] == "nai-new"


@pytest.mark.asyncio
async def test_dashboard_status_includes_embedding_and_rerank_model_pools(monkeypatch, tmp_path):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    dashboard.lifecycle.config["active_embedding_models"] = [
        {
            "model": "text-embedding-3-large",
            "provider": "OpenAI",
            "config": dict(EXPECTED_EMBEDDING_MODEL_CONFIG_DEFAULT),
        }
    ]
    dashboard.lifecycle.config["embedding_model"] = "text-embedding-3-large"
    dashboard.lifecycle.config["embedding_provider"] = "OpenAI"
    dashboard.lifecycle.config["active_rerank_models"] = [
        {
            "model": "bge-reranker-v2",
            "provider": "Local",
            "config": dict(EXPECTED_RERANK_MODEL_CONFIG_DEFAULT),
        }
    ]
    dashboard.lifecycle.config["rerank_model"] = "bge-reranker-v2"
    dashboard.lifecycle.config["rerank_provider"] = "Local"

    response = await dashboard.app.test_client().get(
        "/api/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = await response.get_json()

    assert response.status_code == 200
    assert payload["active_embedding_models"] == [
        {
            "model": "text-embedding-3-large",
            "provider": "OpenAI",
            "config": dict(EXPECTED_EMBEDDING_MODEL_CONFIG_DEFAULT),
        }
    ]
    assert payload["embedding_model"] == "text-embedding-3-large"
    assert payload["embedding_provider"] == "OpenAI"
    assert payload["active_rerank_models"] == [
        {
            "model": "bge-reranker-v2",
            "provider": "Local",
            "config": dict(EXPECTED_RERANK_MODEL_CONFIG_DEFAULT),
        }
    ]
    assert payload["rerank_model"] == "bge-reranker-v2"
    assert payload["rerank_provider"] == "Local"


@pytest.mark.asyncio
async def test_dashboard_provider_pool_routes_manage_embedding_and_rerank_models(
    monkeypatch, tmp_path
):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    client = dashboard.app.test_client()

    embed_response = await client.post(
        "/api/provider/pool/activate",
        json={
            "pool": "embedding",
            "provider": "OpenAI",
            "model": "text-embedding-3-large",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    rerank_response = await client.post(
        "/api/provider/pool/activate",
        json={
            "pool": "rerank",
            "provider": "Local",
            "model": "bge-reranker-v2",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    duplicate_response = await client.post(
        "/api/provider/pool/activate",
        json={
            "pool": "embedding",
            "provider": "OpenAI",
            "model": "text-embedding-3-large",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    remove_response = await client.post(
        "/api/provider/pool/deactivate",
        json={
            "pool": "embedding",
            "provider": "OpenAI",
            "model": "text-embedding-3-large",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert embed_response.status_code == 200
    assert rerank_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert remove_response.status_code == 200
    assert dashboard.lifecycle.config["embedding_model"] == ""
    assert dashboard.lifecycle.config["embedding_provider"] == ""
    assert dashboard.lifecycle.config["active_embedding_models"] == []
    assert dashboard.lifecycle.config["rerank_model"] == "bge-reranker-v2"
    assert dashboard.lifecycle.config["rerank_provider"] == "Local"
    assert dashboard.lifecycle.config["active_rerank_models"] == [
        {
            "model": "bge-reranker-v2",
            "provider": "Local",
            "config": dict(EXPECTED_RERANK_MODEL_CONFIG_DEFAULT),
        }
    ]
    assert cast(_FakeDashboardLifecycle, dashboard.lifecycle).saved == 4


@pytest.mark.asyncio
async def test_dashboard_pool_select_switches_embedding_and_rerank_active_models(
    monkeypatch, tmp_path
):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    dashboard.lifecycle.config["active_embedding_models"] = [
        {
            "model": "embed-a",
            "provider": "OpenAI",
            "config": dict(EXPECTED_EMBEDDING_MODEL_CONFIG_DEFAULT),
        },
        {
            "model": "embed-b",
            "provider": "Local",
            "config": dict(EXPECTED_EMBEDDING_MODEL_CONFIG_DEFAULT),
        },
    ]
    dashboard.lifecycle.config["active_rerank_models"] = [
        {
            "model": "rerank-a",
            "provider": "OpenAI",
            "config": dict(EXPECTED_RERANK_MODEL_CONFIG_DEFAULT),
        },
        {
            "model": "rerank-b",
            "provider": "Local",
            "config": dict(EXPECTED_RERANK_MODEL_CONFIG_DEFAULT),
        },
    ]
    client = dashboard.app.test_client()

    embed_response = await client.post(
        "/api/provider/pool/select",
        json={"pool": "embedding", "provider": "Local", "model": "embed-b"},
        headers={"Authorization": f"Bearer {token}"},
    )
    rerank_response = await client.post(
        "/api/provider/pool/select",
        json={"pool": "rerank", "provider": "OpenAI", "model": "rerank-a"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert embed_response.status_code == 200
    assert rerank_response.status_code == 200
    assert dashboard.lifecycle.config["embedding_model"] == "embed-b"
    assert dashboard.lifecycle.config["embedding_provider"] == "Local"
    assert dashboard.lifecycle.config["rerank_model"] == "rerank-a"
    assert dashboard.lifecycle.config["rerank_provider"] == "OpenAI"


@pytest.mark.asyncio
async def test_dashboard_model_config_route_saves_pool_specific_config(monkeypatch, tmp_path):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    dashboard.lifecycle.config["active_models"] = [
        {
            "model": "chat-current",
            "provider": "OpenAI",
            "config": dict(EXPECTED_CHAT_MODEL_CONFIG_DEFAULT),
        }
    ]
    dashboard.lifecycle.config["active_embedding_models"] = [
        {
            "model": "embed-a",
            "provider": "OpenAI",
            "config": dict(EXPECTED_EMBEDDING_MODEL_CONFIG_DEFAULT),
        }
    ]
    dashboard.lifecycle.config["active_rerank_models"] = [
        {
            "model": "rerank-a",
            "provider": "OpenAI",
            "config": dict(EXPECTED_RERANK_MODEL_CONFIG_DEFAULT),
        }
    ]
    client = dashboard.app.test_client()

    chat_response = await client.post(
        "/api/provider/pool/config",
        json={
            "pool": "chat",
            "provider": "OpenAI",
            "model": "chat-current",
            "config": {
                "max_tokens": "12000",
                "temperature": "0.4",
                "max_context_tokens": "256000",
                "max_rounds": "25",
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    embedding_response = await client.post(
        "/api/provider/pool/config",
        json={
            "pool": "embedding",
            "provider": "OpenAI",
            "model": "embed-a",
            "config": {"dimensions": "768", "batch_size": "24", "encoding_format": "base64"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    rerank_response = await client.post(
        "/api/provider/pool/config",
        json={
            "pool": "rerank",
            "provider": "OpenAI",
            "model": "rerank-a",
            "config": {"top_n": "20", "score_threshold": "0.15", "max_input_tokens": "4096"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert chat_response.status_code == 200
    assert embedding_response.status_code == 200
    assert rerank_response.status_code == 200
    assert dashboard.lifecycle.config["active_models"][0]["config"] == {
        "max_tokens": 12000,
        "temperature": 0.4,
        "max_context_tokens": 256000,
        "max_rounds": 25,
    }
    assert dashboard.lifecycle.config["active_embedding_models"][0]["config"] == {
        "dimensions": 768,
        "batch_size": 24,
        "encoding_format": "base64",
    }
    assert dashboard.lifecycle.config["active_rerank_models"][0]["config"] == {
        "top_n": 20,
        "score_threshold": 0.15,
        "max_input_tokens": 4096,
    }


@pytest.mark.asyncio
async def test_dashboard_delete_provider_removes_embedding_and_rerank_pool_entries(
    monkeypatch, tmp_path
):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()
    dashboard.lifecycle.config["providers"] = {
        "OpenAI": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "api_format": "openai",
            "models": ["gpt-4o", "text-embedding-3-small"],
        },
        "Local": {
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "api_format": "openai",
            "models": ["bge-reranker-v2"],
        },
    }
    dashboard.lifecycle.config["active_embedding_models"] = [
        {"model": "text-embedding-3-small", "provider": "OpenAI"},
        {"model": "local-embed", "provider": "Local"},
    ]
    dashboard.lifecycle.config["active_rerank_models"] = [
        {"model": "jina-reranker", "provider": "OpenAI"},
        {"model": "bge-reranker-v2", "provider": "Local"},
    ]

    response = await dashboard.app.test_client().post(
        "/api/provider/delete",
        json={"name": "OpenAI"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert dashboard.lifecycle.config["active_embedding_models"] == [
        {"model": "local-embed", "provider": "Local"}
    ]
    assert dashboard.lifecycle.config["active_rerank_models"] == [
        {"model": "bge-reranker-v2", "provider": "Local"}
    ]


def test_adapter_payload_includes_recent_group_message_settings():
    payload = management._adapter_payload(
        {
            "enabled": True,
            "ws_reverse_host": "localhost",
            "ws_reverse_port": 6199,
            "ws_reverse_token": "secret",
            "admin_user_ids": ["9001"],
            "group_recent_messages": {
                "enabled": False,
                "max_messages": 4,
            },
            "whitelist": {
                "private_user_ids": ["1001"],
                "group_ids": ["42", "43"],
            },
        },
        status="running",
    )

    assert payload == {
        "enabled": True,
        "ws_reverse_host": "localhost",
        "ws_reverse_port": 6199,
        "ws_reverse_token": "***",
        "admin_user_ids": ["9001"],
        "group_recent_messages": {
            "enabled": False,
            "max_messages": 4,
        },
        "whitelist": {
            "private_user_ids": ["1001"],
            "group_ids": ["42", "43"],
        },
        "status": "running",
    }


def test_adapter_payload_defaults_onebot_host_to_localhost():
    payload = management._adapter_payload({}, status="disabled")

    assert payload["ws_reverse_host"] == "127.0.0.1"


def test_apply_adapter_config_updates_onebot_access_lists_without_touching_token():
    existing_value = "secret"
    existing = {
        "enabled": True,
        "ws_reverse_token": existing_value,
        "admin_user_ids": ["old-admin"],
        "group_recent_messages": {
            "enabled": True,
            "max_messages": 10,
        },
        "whitelist": {
            "private_user_ids": ["old"],
            "group_ids": ["old-group"],
        },
    }

    management._apply_adapter_config(
        existing,
        {
            "ws_reverse_token": "***",
            "admin_user_ids": ["9001", 9002, ""],
            "group_recent_messages": {
                "enabled": False,
                "max_messages": "3",
            },
            "whitelist": {
                "private_user_ids": ["1001", 1002, "  "],
                "group_ids": ["42", 43, ""],
            },
        },
    )

    assert existing["ws_reverse_token"] == existing_value
    assert existing["admin_user_ids"] == ["9001", "9002"]
    assert existing["group_recent_messages"] == {
        "enabled": False,
        "max_messages": 3,
    }
    assert existing["whitelist"] == {
        "private_user_ids": ["1001", "1002"],
        "group_ids": ["42", "43"],
    }


def test_apply_adapter_config_preserves_existing_token_when_empty_without_explicit_clear():
    existing_value = "saved-token"
    existing = {"ws_reverse_token": existing_value}

    management._apply_adapter_config(existing, {"ws_reverse_token": ""})

    assert existing["ws_reverse_token"] == existing_value


def test_apply_adapter_config_clears_token_when_explicitly_requested():
    existing_value = "saved-token"
    existing = {"ws_reverse_token": existing_value}

    management._apply_adapter_config(existing, {"clear_ws_reverse_token": True})

    assert "ws_reverse_token" not in existing


@pytest.mark.asyncio
async def test_dashboard_rejects_public_onebot11_without_token_or_whitelist(monkeypatch, tmp_path):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    token = dashboard._create_auth_session()

    response = await dashboard.app.test_client().post(
        "/api/adapter",
        json={
            "enabled": True,
            "ws_reverse_host": "0.0.0.0",  # noqa: S104
            "ws_reverse_token": "",
            "whitelist": {
                "private_user_ids": [],
                "group_ids": [],
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    payload = await response.get_json()

    assert response.status_code == 400
    assert "requires ws_reverse_token or whitelist" in payload["error"]
    assert "onebot11" not in dashboard.lifecycle.config
    assert dashboard.lifecycle.saved == 0


@pytest.mark.asyncio
async def test_dashboard_preserves_existing_token_when_adapter_save_sends_empty_token(
    monkeypatch,
    tmp_path,
):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    existing_value = "saved-token"
    dashboard.lifecycle.config["onebot11"] = {
        "enabled": True,
        "ws_reverse_host": "0.0.0.0",  # noqa: S104
        "ws_reverse_port": 6199,
        "ws_reverse_token": existing_value,
        "whitelist": {
            "private_user_ids": [],
            "group_ids": [],
        },
    }
    token = dashboard._create_auth_session()

    response = await dashboard.app.test_client().post(
        "/api/adapter",
        json={
            "enabled": True,
            "ws_reverse_host": "0.0.0.0",  # noqa: S104
            "ws_reverse_port": 6199,
            "ws_reverse_token": "",
            "whitelist": {
                "private_user_ids": [],
                "group_ids": [],
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert dashboard.lifecycle.config["onebot11"]["ws_reverse_token"] == existing_value
    assert dashboard.lifecycle.saved == 1


def test_dashboard_builds_novelai_generation_payload():
    payload, meta = novelai_image.build_novelai_payload(
        {
            "prompt": "1girl, cinematic light",
            "negative_prompt": "low quality",
            "width": 999,
            "height": 1024,
            "steps": "32",
            "scale": "6.5",
            "seed": "123",
            "n_samples": 2,
            "image_format": "webp",
        },
        {"model": "nai-diffusion-4-5-full"},
    )

    assert payload["action"] == "generate"
    assert payload["input"] == "1girl, cinematic light"
    assert payload["model"] == "nai-diffusion-4-5-full"
    assert payload["parameters"]["width"] == 1024
    assert payload["parameters"]["height"] == 1024
    assert payload["parameters"]["steps"] == 32
    assert payload["parameters"]["scale"] == 6.5
    assert payload["parameters"]["seed"] == 123
    assert payload["parameters"]["n_samples"] == 2
    assert payload["parameters"]["image_format"] == "webp"
    assert payload["parameters"]["v4_prompt"]["caption"]["base_caption"] == (
        "1girl, cinematic light"
    )
    assert meta["seed"] == 123


def test_novelai_image_tool_returns_chat_image_batch(monkeypatch, tmp_path):
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("image_0.png", b"fake-png")

    novelai_image.set_novelai_config(
        {
            "api_key": "nai-test",
            "base_url": "https://image.novelai.net",
            "model": "nai-diffusion-4-5-full",
        }
    )
    monkeypatch.setattr(
        novelai_image,
        "_post_novelai_request",
        lambda payload, cfg: archive_bytes.getvalue(),
    )

    result = NovelAIImageTool(str(tmp_path)).execute(prompt="cat wizard", seed=42)

    assert "Generated 1 NovelAI image(s) for the chat reply." in result
    images = novelai_image.pop_generated_images_from_result(result)
    assert images == [
        {
            "url": "data:image/png;base64,ZmFrZS1wbmc=",
            "file": "base64://ZmFrZS1wbmc=",
            "mime_type": "image/png",
            "size": 8,
            "name": "novelai-42-1.png",
        }
    ]
    assert list(tmp_path.iterdir()) == []


def test_novelai_authorization_header_adds_bearer_prefix():
    assert novelai_image._authorization_value("nai-token") == "Bearer nai-token"
    assert novelai_image._authorization_value("Bearer nai-token") == "Bearer nai-token"


def test_dashboard_resolve_workspace_path_blocks_escape(tmp_path):
    ws, target = _helpers.resolve_workspace_path(str(tmp_path), "nested/file.txt")

    assert ws == tmp_path.resolve()
    assert target == (tmp_path / "nested" / "file.txt").resolve()
    with pytest.raises(PermissionError, match="path outside workspace"):
        _helpers.resolve_workspace_path(str(tmp_path), "../outside.txt")


def test_dashboard_normalize_chat_images_accepts_valid_data_urls():
    images = chat._normalize_chat_images(
        [
            {
                "dataUrl": "data:image/png;base64,aGVsbG8=",
                "name": "../screen.png",
            }
        ]
    )

    assert images == [
        {
            "url": "data:image/png;base64,aGVsbG8=",
            "file": "screen.png",
            "mime_type": "image/png",
            "size": 5,
        }
    ]


def test_dashboard_normalize_chat_images_rejects_invalid_payloads(monkeypatch):
    with pytest.raises(ValueError, match="images must be a list"):
        chat._normalize_chat_images({"bad": True})
    with pytest.raises(ValueError, match="at most"):
        chat._normalize_chat_images([{}] * (chat._MAX_CHAT_IMAGES + 1))
    with pytest.raises(ValueError, match="image type"):
        chat._normalize_chat_images([{"dataUrl": "data:text/plain;base64,aGVsbG8="}])

    monkeypatch.setattr(chat, "_MAX_CHAT_IMAGE_BYTES", 2)
    with pytest.raises(ValueError, match="smaller"):
        chat._normalize_chat_images([{"dataUrl": "data:image/png;base64,aGVsbG8="}])


def test_dashboard_chat_images_still_reject_svg_uploads():
    with pytest.raises(ValueError, match="image type"):
        chat._normalize_chat_images([{"dataUrl": "data:image/svg+xml;base64,PHN2Zy8+"}])


def test_dashboard_csp_allows_chat_image_previews():
    assert "img-src 'self' data: blob:" in _helpers.DASHBOARD_CSP


def test_dashboard_cookie_value_parses_valid_cookie_headers():
    assert _helpers.cookie_value("a=1; atri_dashboard_session=abc", "atri_dashboard_session") == (
        "abc"
    )
    assert _helpers.cookie_value("", "atri_dashboard_session") == ""
    assert _helpers.cookie_value("a=1", "missing") == ""


@pytest.mark.asyncio
async def test_dashboard_login_creates_distinct_server_sessions(monkeypatch, tmp_path):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    first = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret"},
    )
    second = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret"},
    )
    first_token = _auth_cookie_from_response(first)
    second_token = _auth_cookie_from_response(second)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first_token != second_token
    assert (
        await client.get(
            "/api/workspace",
            headers={"Authorization": f"Bearer {first_token}"},
        )
    ).status_code == 200
    assert (
        await client.get(
            "/api/workspace",
            headers={"Authorization": f"Bearer {second_token}"},
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_dashboard_logout_revokes_presented_session_token(monkeypatch, tmp_path):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    client = dashboard.app.test_client()
    login = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret"},
    )
    token = _auth_cookie_from_response(login)

    assert (
        await client.get(
            "/api/workspace",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 200

    logout = await client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert logout.status_code == 200
    assert (
        await client.get(
            "/api/workspace",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_dashboard_auth_reset_invalidates_existing_sessions(monkeypatch, tmp_path):
    dashboard = _dashboard_for_auth_tests(monkeypatch, tmp_path)
    user_token = dashboard._create_auth_session()

    assert dashboard._session_ok(user_token) is True

    dashboard.lifecycle.config["dashboard"]["password"] = _helpers.hash_password("new-secret")
    dashboard._sync_auth_from_config()

    assert dashboard._session_ok(user_token) is False


def test_dashboard_model_url_candidates_normalize_common_provider_urls():
    assert models._model_url_candidates("") == ["https://api.openai.com/v1/models"]
    assert models._model_url_candidates("api.example.test/v1/chat/completions") == [
        "https://api.example.test/v1/models"
    ]
    assert models._model_url_candidates("https://api.example.test/messages") == [
        "https://api.example.test/models",
        "https://api.example.test/v1/models",
    ]
    assert models._model_url_candidates("https://api.example.test/v1/models") == [
        "https://api.example.test/v1/models"
    ]


def test_dashboard_model_fetch_headers_and_candidates():
    assert models._headers_for_model_fetch("anthropic", "key") == {
        "anthropic-version": "2023-06-01",
        "x-api-key": "key",
    }
    assert models._headers_for_model_fetch("openai", "key") == {"Authorization": "Bearer key"}

    candidates = models._model_fetch_candidates(
        "api.deepseek.test/anthropic/messages",
        "anthropic",
        "key",
    )

    assert (
        "https://api.deepseek.test/anthropic/models",
        {"anthropic-version": "2023-06-01", "x-api-key": "key"},
        "anthropic",
    ) in candidates
    assert (
        "https://api.deepseek.test/models",
        {"Authorization": "Bearer key"},
        "openai",
    ) in candidates


def test_dashboard_extract_model_ids_and_parse_int():
    assert models._extract_model_ids(
        {
            "data": [
                {"id": "b"},
                {"name": "a"},
                {"model": "c"},
                "a",
                {"missing": "x"},
            ]
        }
    ) == ["a", "b", "c"]
    assert models._extract_model_ids({"models": ["x"]}) == ["x"]
    assert models._extract_model_ids("bad") == []
    assert _helpers.parse_int("42", 1) == 42
    assert _helpers.parse_int("bad", 1) == 1
