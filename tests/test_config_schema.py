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
EXPECTED_GRAPH_KNOWLEDGE_DEFAULT = {
    "enabled": False,
    "uri": "neo4j://localhost:7687",
    "username": "neo4j",
    "password": "",
    "database": "neo4j",
    "extraction_model": "",
    "extraction_provider": "",
    "extraction_enabled": True,
    "extraction_sources": ["documents", "chat"],
    "retrieval_enabled": True,
    "semantic_parameter_tuning_enabled": True,
    "retrieval_depth": 3,
    "max_facts": 8,
    "expansion_candidate_limit": 40,
    "multi_hop_expansion_cache_mode": "persistent",
    "multi_hop_expansion_cache_preload_seed_limit": 64,
    "multi_hop_expansion_cache_path_limit": 1000,
    "multi_hop_expansion_cache_preload_path_limit": 200,
    "ranking_policy": "hybrid",
    "retrieval_timeout_seconds": 15.0,
    "extraction_timeout_seconds": 120.0,
    "queue_max_size": 1000,
}
EXPECTED_DEEP_RESEARCH_DEFAULT = {
    "max_gap_rounds": 8,
    "max_research_tool_calls": 100,
    "max_web_fetches": 40,
    "max_parallel_subagents": 3,
    "timeout_seconds": 900.0,
    "synthesis_reserve_seconds": 60.0,
    "allow_report_export": True,
    "report_directory": "research",
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
    assert config["agent_timeout_seconds"] == DEFAULT_CONFIG["agent_timeout_seconds"]
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
    assert config["knowledge"]["embedding_cache_max_size"] == 20000
    assert config["knowledge"]["graph"] == EXPECTED_GRAPH_KNOWLEDGE_DEFAULT
    assert config["deep_research"] == EXPECTED_DEEP_RESEARCH_DEFAULT
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
    assert config["trusted_directories"] == []
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


def test_normalize_config_coerces_agent_timeout_seconds():
    config, changed = normalize_config({"agent_timeout_seconds": "45.5"})

    assert changed is True
    assert config["agent_timeout_seconds"] == 45.5


def test_normalize_config_coerces_graph_knowledge_settings():
    config, changed = normalize_config(
        {
            "knowledge": {
                "enabled": "true",
                "top_k": "9",
                "embedding_cache_max_size": "64",
                "graph": {
                    "enabled": "true",
                    "uri": "bolt://localhost:7687",
                    "username": "neo4j",
                    "password": "secret",
                    "database": "atri",
                    "extraction_model": "gpt-4o-mini",
                    "extraction_provider": "OpenAI",
                    "extraction_enabled": "false",
                    "extraction_sources": ["documents"],
                    "retrieval_enabled": "true",
                    "semantic_parameter_tuning_enabled": "false",
                    "retrieval_depth": "7",
                    "max_facts": "12",
                    "expansion_candidate_limit": "64",
                    "multi_hop_expansion_cache_mode": "MEMORY",
                    "multi_hop_expansion_cache_preload_seed_limit": "512",
                    "multi_hop_expansion_cache_path_limit": "2000",
                    "multi_hop_expansion_cache_preload_path_limit": "800",
                    "ranking_policy": "RELEVANCE",
                    "retrieval_timeout_seconds": "2.5",
                    "extraction_timeout_seconds": "30",
                    "queue_max_size": "50",
                },
            }
        }
    )

    assert changed is True
    assert config["knowledge"]["enabled"] is True
    assert config["knowledge"]["top_k"] == 9
    assert config["knowledge"]["embedding_cache_max_size"] == 64
    assert config["knowledge"]["graph"] == {
        "enabled": True,
        "uri": "bolt://localhost:7687",
        "username": "neo4j",
        "password": "secret",
        "database": "atri",
        "extraction_model": "gpt-4o-mini",
        "extraction_provider": "OpenAI",
        "extraction_enabled": False,
        "extraction_sources": ["documents"],
        "retrieval_enabled": True,
        "semantic_parameter_tuning_enabled": False,
        "retrieval_depth": 7,
        "max_facts": 12,
        "expansion_candidate_limit": 64,
        "multi_hop_expansion_cache_mode": "memory",
        "multi_hop_expansion_cache_preload_seed_limit": 512,
        "multi_hop_expansion_cache_path_limit": 2000,
        "multi_hop_expansion_cache_preload_path_limit": 800,
        "ranking_policy": "relevance",
        "retrieval_timeout_seconds": 2.5,
        "extraction_timeout_seconds": 30.0,
        "queue_max_size": 50,
    }


def test_normalize_config_maps_legacy_persistent_multihop_cache_flag():
    disabled_config, disabled_changed = normalize_config(
        {
            "knowledge": {
                "graph": {
                    "persistent_multi_hop_expansion_cache_enabled": False,
                }
            }
        }
    )
    enabled_config, enabled_changed = normalize_config(
        {
            "knowledge": {
                "graph": {
                    "persistent_multi_hop_expansion_cache_enabled": True,
                }
            }
        }
    )

    assert disabled_changed is True
    assert disabled_config["knowledge"]["graph"]["multi_hop_expansion_cache_mode"] == "memory"
    assert enabled_changed is True
    assert enabled_config["knowledge"]["graph"]["multi_hop_expansion_cache_mode"] == "persistent"


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
        (
            {"agent_mode": "execute"},
            "agent_mode must be one of: plan, agent, deepresearch",
        ),
        ({"agent_timeout_seconds": 0}, "agent_timeout_seconds must be >= 0.001"),
        (
            {"knowledge": {"graph": {"retrieval_depth": 8}}},
            "knowledge.graph.retrieval_depth must be <= 7",
        ),
        (
            {"knowledge": {"graph": {"ranking_policy": "random"}}},
            "knowledge.graph.ranking_policy must be one of: hybrid, relevance, latest",
        ),
        (
            {"knowledge": {"graph": {"multi_hop_expansion_cache_mode": "disk"}}},
            "knowledge.graph.multi_hop_expansion_cache_mode must be one of: "
            "off, memory, persistent",
        ),
        (
            {"knowledge": {"graph": {"expansion_candidate_limit": 0}}},
            "knowledge.graph.expansion_candidate_limit must be >= 1",
        ),
        (
            {"knowledge": {"graph": {"multi_hop_expansion_cache_preload_seed_limit": -1}}},
            "knowledge.graph.multi_hop_expansion_cache_preload_seed_limit must be >= 0",
        ),
        (
            {"knowledge": {"graph": {"multi_hop_expansion_cache_preload_seed_limit": 2049}}},
            "knowledge.graph.multi_hop_expansion_cache_preload_seed_limit must be <= 2048",
        ),
        (
            {"knowledge": {"graph": {"multi_hop_expansion_cache_path_limit": 0}}},
            "knowledge.graph.multi_hop_expansion_cache_path_limit must be >= 1",
        ),
        (
            {"knowledge": {"graph": {"multi_hop_expansion_cache_path_limit": 10001}}},
            "knowledge.graph.multi_hop_expansion_cache_path_limit must be <= 10000",
        ),
        (
            {"knowledge": {"graph": {"multi_hop_expansion_cache_preload_path_limit": 0}}},
            "knowledge.graph.multi_hop_expansion_cache_preload_path_limit must be >= 1",
        ),
        (
            {"knowledge": {"graph": {"multi_hop_expansion_cache_preload_path_limit": 50001}}},
            "knowledge.graph.multi_hop_expansion_cache_preload_path_limit must be <= 50000",
        ),
        (
            {"knowledge": {"embedding_cache_max_size": -1}},
            "knowledge.embedding_cache_max_size must be >= 0",
        ),
        ([], "config root must be an object"),
        (
            {"deep_research": {"max_parallel_subagents": 4}},
            "deep_research.max_parallel_subagents must be <= 3",
        ),
        (
            {
                "deep_research": {
                    "timeout_seconds": 60,
                    "synthesis_reserve_seconds": 60,
                }
            },
            "deep_research.synthesis_reserve_seconds must be less than timeout_seconds",
        ),
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


def test_normalize_config_defaults_onebot11_reverse_ws_to_localhost():
    config, _changed = normalize_config({})

    assert config["onebot11"]["ws_reverse_host"] == "127.0.0.1"


def test_normalize_config_rejects_public_onebot11_without_token_or_whitelist():
    with pytest.raises(
        ConfigValidationError,
        match="onebot11 remote reverse WebSocket requires ws_reverse_token or whitelist",
    ):
        normalize_config(
            {
                "onebot11": {
                    "enabled": True,
                    "ws_reverse_host": "0.0.0.0",  # noqa: S104
                    "ws_reverse_token": "",
                    "whitelist": {
                        "private_user_ids": [],
                        "group_ids": [],
                    },
                }
            },
            migrate_legacy_onebot11_public_bind=False,
        )


@pytest.mark.parametrize(
    "whitelist",
    [
        {"private_user_ids": ["1001"], "group_ids": []},
        {"private_user_ids": [], "group_ids": ["42"]},
    ],
)
def test_normalize_config_rejects_public_onebot11_without_token_and_complete_whitelist(
    whitelist,
):
    with pytest.raises(
        ConfigValidationError,
        match="onebot11 remote reverse WebSocket requires ws_reverse_token or whitelist",
    ):
        normalize_config(
            {
                "onebot11": {
                    "enabled": True,
                    "ws_reverse_host": "0.0.0.0",  # noqa: S104
                    "ws_reverse_token": "",
                    "whitelist": whitelist,
                }
            },
            migrate_legacy_onebot11_public_bind=False,
        )


def test_normalize_config_migrates_legacy_public_onebot11_default_to_localhost():
    config, changed = normalize_config(
        {
            "onebot11": {
                "enabled": True,
                "ws_reverse_host": "0.0.0.0",  # noqa: S104
                "ws_reverse_token": "",
                "whitelist": {
                    "private_user_ids": [],
                    "group_ids": [],
                },
            }
        }
    )

    assert changed is True
    assert config["onebot11"]["ws_reverse_host"] == "127.0.0.1"


@pytest.mark.parametrize(
    "onebot11",
    [
        {"ws_reverse_host": "0.0.0.0", "ws_reverse_token": "secret-token"},  # noqa: S104
        {
            "ws_reverse_host": "0.0.0.0",  # noqa: S104
            "whitelist": {"private_user_ids": ["1001"], "group_ids": ["42"]},
        },
        {"ws_reverse_host": "127.0.0.1"},
        {"ws_reverse_host": "localhost"},
        {"ws_reverse_host": "::1"},
    ],
)
def test_normalize_config_allows_protected_or_local_onebot11(onebot11):
    config, _changed = normalize_config({"onebot11": onebot11})

    assert config["onebot11"]["ws_reverse_host"] == onebot11["ws_reverse_host"]


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
