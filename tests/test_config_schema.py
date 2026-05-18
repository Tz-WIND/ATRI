import pytest

from core.config_schema import DEFAULT_CONFIG, ConfigValidationError, normalize_config


def test_normalize_config_adds_defaults_and_coerces_scalar_values():
    config, changed = normalize_config(
        {
            "model": "gpt-test",
            "base_url": None,
            "max_tokens": "256",
            "temperature": "0.75",
            "onebot11": {
                "enabled": "false",
                "ws_reverse_port": "6200",
            },
        }
    )

    assert changed is True
    assert config["model"] == "gpt-test"
    assert config["base_url"] is None
    assert config["max_tokens"] == 256
    assert config["temperature"] == 0.75
    assert config["agent_mode"] == DEFAULT_CONFIG["agent_mode"]
    assert config["image_transcription"] == DEFAULT_CONFIG["image_transcription"]
    assert config["novelai"] == DEFAULT_CONFIG["novelai"]
    assert config["onebot11"]["enabled"] is False
    assert config["onebot11"]["ws_reverse_port"] == 6200
    assert config["workspace"] == DEFAULT_CONFIG["workspace"]
    assert "dashboard" in config


def test_normalize_config_does_not_share_nested_default_state():
    first, _ = normalize_config({})
    second, _ = normalize_config({})

    first["active_models"].append("changed")
    first["dashboard"]["username"] = "admin"

    assert second["active_models"] == []
    assert second["dashboard"]["username"] == ""


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"max_rounds": 0}, "max_rounds must be >= 1"),
        ({"dashboard": {"enabled": "yes"}}, "dashboard.enabled must be a boolean"),
        ({"active_models": "gpt-test"}, "active_models must be an array"),
        ({"vst3_plugin_paths": "D:/VST3"}, "vst3_plugin_paths must be an array"),
        ({"agent_mode": "execute"}, "agent_mode must be one of: plan, agent"),
        ([], "config root must be an object"),
    ],
)
def test_normalize_config_rejects_invalid_values(payload, message):
    with pytest.raises(ConfigValidationError, match=message):
        normalize_config(payload)


def test_normalize_config_migrates_legacy_dashboard_auth_token():
    config, changed = normalize_config(
        {
            "dashboard": {
                "enabled": True,
                "auth_token": "secret-token",
            }
        }
    )

    assert changed is True
    assert "auth_token" not in config["dashboard"]
    assert config["dashboard"]["password"].startswith("pbkdf2:")
    assert "secret-token" not in config["dashboard"]["password"]


def test_normalize_config_accepts_uppercase_agent_mode():
    config, changed = normalize_config({"agent_mode": "PLAN"})

    assert changed is True
    assert config["agent_mode"] == "plan"


def test_normalize_config_coerces_image_transcription_settings():
    config, changed = normalize_config(
        {
            "image_transcription": {
                "enabled": "true",
                "model": "vision-test",
                "max_tokens": "2048",
                "temperature": "0.2",
            }
        }
    )

    assert changed is True
    assert config["image_transcription"]["enabled"] is True
    assert config["image_transcription"]["model"] == "vision-test"
    assert config["image_transcription"]["max_tokens"] == 2048
    assert config["image_transcription"]["temperature"] == 0.2
    assert config["image_transcription"]["prompt"]


def test_normalize_config_adds_novelai_defaults_and_coerces_settings():
    config, changed = normalize_config(
        {
            "novelai": {
                "api_key": "nai-key",
                "base_url": "https://example.test",
                "model": "nai-test-model",
            }
        }
    )

    assert changed is True
    assert config["novelai"]["api_key"] == "nai-key"
    assert config["novelai"]["base_url"] == "https://example.test"
    assert config["novelai"]["model"] == "nai-test-model"


def test_normalize_config_removes_legacy_auth_token_when_dashboard_is_disabled():
    config, changed = normalize_config(
        {
            "dashboard": {
                "enabled": False,
                "auth_token": "unused-token",
            }
        }
    )

    assert changed is True
    assert "auth_token" not in config["dashboard"]
    assert config["dashboard"]["password"] == ""
