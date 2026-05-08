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
