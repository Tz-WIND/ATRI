import pytest

from dashboard.routes import _helpers, chat, models


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
    providers = {
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
