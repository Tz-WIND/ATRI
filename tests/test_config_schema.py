import pytest

from core.config_schema import DEFAULT_CONFIG, ConfigValidationError, normalize_config

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
    assert config["model_provider"] == ""
    assert config["embedding_model"] == ""
    assert config["embedding_provider"] == ""
    assert config["rerank_model"] == ""
    assert config["rerank_provider"] == ""
    assert config["active_embedding_models"] == []
    assert config["active_rerank_models"] == []
    assert config["image_transcription"] == DEFAULT_CONFIG["image_transcription"]
    assert config["novelai"] == DEFAULT_CONFIG["novelai"]
    assert config["onebot11"]["enabled"] is False
    assert config["onebot11"]["ws_reverse_port"] == 6200
    assert config["onebot11"]["admin_user_ids"] == []
    assert config["onebot11"]["group_recent_messages"] == {
        "enabled": True,
        "max_messages": 10,
    }
    assert config["onebot11"]["whitelist"] == {
        "private_user_ids": [],
        "group_ids": [],
    }
    assert config["workspace"] == DEFAULT_CONFIG["workspace"]
    assert "dashboard" in config


def test_normalize_config_does_not_share_nested_default_state():
    first, _ = normalize_config({})
    second, _ = normalize_config({})

    first["active_models"].append("changed")
    first["active_embedding_models"].append("embedding")
    first["active_rerank_models"].append("rerank")
    first["dashboard"]["username"] = "admin"

    assert second["active_models"] == []
    assert second["active_embedding_models"] == []
    assert second["active_rerank_models"] == []
    assert second["dashboard"]["username"] == ""


def test_normalize_config_adds_default_pool_config_to_model_entries():
    config, changed = normalize_config(
        {
            "max_tokens": 8192,
            "temperature": 0.3,
            "max_context_tokens": 200000,
            "max_rounds": 20,
            "active_models": [{"model": "chat-a", "provider": "OpenAI"}],
            "active_embedding_models": [{"model": "embed-a", "provider": "OpenAI"}],
            "active_rerank_models": [{"model": "rerank-a", "provider": "OpenAI"}],
        }
    )

    assert changed is True
    assert config["active_models"][0]["config"] == {
        **EXPECTED_CHAT_MODEL_CONFIG_DEFAULT,
        "max_tokens": 8192,
        "temperature": 0.3,
        "max_context_tokens": 200000,
        "max_rounds": 20,
    }
    assert config["active_embedding_models"][0]["config"] == EXPECTED_EMBEDDING_MODEL_CONFIG_DEFAULT
    assert config["active_rerank_models"][0]["config"] == EXPECTED_RERANK_MODEL_CONFIG_DEFAULT


def test_normalize_config_preserves_model_entry_config_over_defaults():
    config, changed = normalize_config(
        {
            "active_models": [
                {
                    "model": "chat-a",
                    "provider": "OpenAI",
                    "config": {"temperature": "0.8", "max_tokens": "1024"},
                }
            ],
            "active_embedding_models": [
                {
                    "model": "embed-a",
                    "provider": "OpenAI",
                    "config": {"dimensions": "768", "batch_size": "16"},
                }
            ],
            "active_rerank_models": [
                {
                    "model": "rerank-a",
                    "provider": "OpenAI",
                    "config": {"top_n": "12", "score_threshold": "0.2"},
                }
            ],
        }
    )

    assert changed is True
    assert config["active_models"][0]["config"]["temperature"] == 0.8
    assert config["active_models"][0]["config"]["max_tokens"] == 1024
    assert config["active_models"][0]["config"]["max_context_tokens"] == 128000
    assert config["active_embedding_models"][0]["config"]["dimensions"] == 768
    assert config["active_embedding_models"][0]["config"]["batch_size"] == 16
    assert config["active_embedding_models"][0]["config"]["encoding_format"] == "float"
    assert config["active_rerank_models"][0]["config"]["top_n"] == 12
    assert config["active_rerank_models"][0]["config"]["score_threshold"] == 0.2
    assert config["active_rerank_models"][0]["config"]["max_input_tokens"] == 8192


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"max_rounds": 0}, "max_rounds must be >= 1"),
        ({"dashboard": {"enabled": "yes"}}, "dashboard.enabled must be a boolean"),
        ({"active_models": "gpt-test"}, "active_models must be an array"),
        ({"active_embedding_models": "embed-test"}, "active_embedding_models must be an array"),
        ({"active_rerank_models": "rerank-test"}, "active_rerank_models must be an array"),
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


def test_normalize_config_coerces_onebot11_recent_group_message_settings():
    config, changed = normalize_config(
        {
            "onebot11": {
                "group_recent_messages": {
                    "enabled": "false",
                    "max_messages": "3",
                }
            }
        }
    )

    assert changed is True
    assert config["onebot11"]["group_recent_messages"] == {
        "enabled": False,
        "max_messages": 3,
    }


def test_normalize_config_keeps_onebot11_whitelist_settings():
    config, changed = normalize_config(
        {
            "onebot11": {
                "whitelist": {
                    "private_user_ids": ["1001", "1002"],
                    "group_ids": ["42"],
                }
            }
        }
    )

    assert changed is True
    assert config["onebot11"]["whitelist"] == {
        "private_user_ids": ["1001", "1002"],
        "group_ids": ["42"],
    }


def test_normalize_config_keeps_onebot11_admin_settings():
    config, changed = normalize_config({"onebot11": {"admin_user_ids": ["1001", "1002"]}})

    assert changed is True
    assert config["onebot11"]["admin_user_ids"] == ["1001", "1002"]


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
