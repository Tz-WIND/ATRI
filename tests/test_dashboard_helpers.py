import io
import zipfile
from typing import Any

import pytest

from core.tools import novelai_image
from core.tools.novelai_image import NovelAIImageTool
from dashboard.routes import _helpers, chat, management, models


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


def test_dashboard_csp_allows_chat_image_previews():
    assert "img-src 'self' data: blob:" in _helpers.DASHBOARD_CSP


def test_dashboard_cookie_value_parses_valid_cookie_headers():
    assert _helpers.cookie_value("a=1; atri_dashboard_session=abc", "atri_dashboard_session") == (
        "abc"
    )
    assert _helpers.cookie_value("", "atri_dashboard_session") == ""
    assert _helpers.cookie_value("a=1", "missing") == ""


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
