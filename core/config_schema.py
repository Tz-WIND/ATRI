"""Config schema, defaults, and normalization for ATRI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_IMAGE_TRANSCRIPTION_PROMPT = (
    "Transcribe and describe the attached image for a downstream coding agent. "
    "Include visible text, UI state, errors, file paths, code snippets, diagrams, "
    "and any details needed to answer the user's request. Be concise and factual."
)


class ConfigValidationError(ValueError):
    """Raised when config.yaml contains an invalid value."""


CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "model": {"type": "string", "default": "gpt-4o"},
        "api_key": {"type": "string", "default": ""},
        "base_url": {"type": ["string", "null"], "default": None},
        "api_format": {"type": "string", "default": "openai"},
        "active_models": {"type": "array", "default": []},
        "providers": {"type": "object", "default": {}},
        "max_tokens": {"type": "integer", "default": 4096, "minimum": 1},
        "temperature": {"type": "number", "default": 0.0},
        "max_context_tokens": {"type": "integer", "default": 128000, "minimum": 1},
        "max_rounds": {"type": "integer", "default": 50, "minimum": 1},
        "workspace": {"type": "string", "default": "./workspace"},
        "sessions_dir": {"type": "string", "default": "data/sessions"},
        "runtime_dir": {"type": "string", "default": "data/runtime"},
        "wake_words": {"type": "array", "default": ["atri"]},
        "extra_instructions": {"type": "string", "default": ""},
        "persona": {"type": "string", "default": ""},
        "agent_mode": {"type": "string", "default": "agent", "enum": ["plan", "agent"]},
        "image_transcription": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": False},
                "model": {"type": "string", "default": ""},
                "api_key": {"type": "string", "default": ""},
                "base_url": {"type": ["string", "null"], "default": None},
                "api_format": {"type": "string", "default": "openai"},
                "prompt": {"type": "string", "default": DEFAULT_IMAGE_TRANSCRIPTION_PROMPT},
                "max_tokens": {"type": "integer", "default": 1024, "minimum": 1},
                "temperature": {"type": "number", "default": 0.0},
            },
        },
        "onebot11": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": True},
                "ws_reverse_host": {"type": "string", "default": "0.0.0.0"},  # noqa: S104
                "ws_reverse_port": {"type": "integer", "default": 6199, "minimum": 1},
                "ws_reverse_token": {"type": "string", "default": ""},
                "blocked_users": {"type": "array", "default": []},
            },
        },
        "dashboard": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": True},
                "host": {"type": "string", "default": "127.0.0.1"},
                "port": {"type": "integer", "default": 6185, "minimum": 1},
                "username": {"type": "string", "default": ""},
                "password": {"type": "string", "default": ""},
            },
        },
        "audio_host": {
            "type": "object",
            "properties": {
                "binary_path": {"type": "string", "default": ""},
                "sample_rate": {"type": "integer", "default": 48000, "minimum": 1},
                "buffer_size": {"type": "integer", "default": 256, "minimum": 1},
                "auto_start": {"type": "boolean", "default": True},
                "audio_engine": {
                    "type": "string",
                    "default": "default",
                },
                "bit_depth": {
                    "type": "string",
                    "default": "f32",
                    "enum": ["i16", "i24", "f32"],
                },
            },
        },
        "plugins_dir": {"type": "string", "default": "plugins"},
        "vst3_plugin_paths": {"type": "array", "default": []},
        "vst2_plugin_paths": {"type": "array", "default": []},
        "music_directories": {"type": "array", "default": []},
        "mcp_servers": {"type": "object", "default": {}},
        "skills_root": {"type": "string", "default": "skills"},
        "skill_search_roots": {"type": "array", "default": []},
        "skills": {"type": "object", "default": {}},
        "tavily_api_key": {"type": "string", "default": ""},
    },
}


def _defaults_from_schema(schema: dict[str, Any]) -> Any:
    if "default" in schema:
        return deepcopy(schema["default"])
    if schema.get("type") == "object":
        return {
            key: _defaults_from_schema(child) for key, child in schema.get("properties", {}).items()
        }
    if schema.get("type") == "array":
        return []
    return None


DEFAULT_CONFIG = _defaults_from_schema(CONFIG_SCHEMA)


def _merge_config(default: dict, override: dict) -> dict:
    """Recursively merge config dictionaries without sharing nested defaults."""
    merged = {}
    for key, value in default.items():
        merged[key] = deepcopy(value)

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _has_missing_schema_keys(config: dict[str, Any], schema: dict[str, Any]) -> bool:
    for key, child_schema in schema.get("properties", {}).items():
        if key not in config:
            return True
        if (
            child_schema.get("type") == "object"
            and isinstance(config.get(key), dict)
            and _has_missing_schema_keys(config[key], child_schema)
        ):
            return True
    return False


def _type_names(schema_type: str | list[str]) -> list[str]:
    return schema_type if isinstance(schema_type, list) else [schema_type]


def _coerce_value(value: Any, schema: dict[str, Any], path: str) -> tuple[Any, bool]:
    allowed = _type_names(schema.get("type", "object"))

    if value is None and "null" in allowed:
        return value, False

    if "object" in allowed:
        if not isinstance(value, dict):
            raise ConfigValidationError(f"{path} must be an object")
        return _validate_object(value, schema, path)

    if "array" in allowed:
        if not isinstance(value, list):
            raise ConfigValidationError(f"{path} must be an array")
        return value, False

    if "boolean" in allowed:
        if isinstance(value, bool):
            return value, False
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true", True
        raise ConfigValidationError(f"{path} must be a boolean")

    if "integer" in allowed:
        if isinstance(value, bool):
            raise ConfigValidationError(f"{path} must be an integer")
        if isinstance(value, int):
            coerced: int | float = value
        elif isinstance(value, str) and value.strip():
            try:
                coerced = int(value)
            except ValueError as e:
                raise ConfigValidationError(f"{path} must be an integer") from e
        else:
            raise ConfigValidationError(f"{path} must be an integer")
        minimum = schema.get("minimum")
        if minimum is not None and coerced < minimum:
            raise ConfigValidationError(f"{path} must be >= {minimum}")
        return coerced, coerced != value

    if "number" in allowed:
        if isinstance(value, bool):
            raise ConfigValidationError(f"{path} must be a number")
        if isinstance(value, (int, float)):
            coerced = float(value)
        elif isinstance(value, str) and value.strip():
            try:
                coerced = float(value)
            except ValueError as e:
                raise ConfigValidationError(f"{path} must be a number") from e
        else:
            raise ConfigValidationError(f"{path} must be a number")
        return coerced, coerced != value

    if "string" in allowed:
        if isinstance(value, str):
            enum = schema.get("enum")
            if enum is not None and value not in enum:
                lowered = value.lower()
                if lowered in enum:
                    return lowered, True
                allowed_values = ", ".join(str(item) for item in enum)
                raise ConfigValidationError(f"{path} must be one of: {allowed_values}")
            return value, False
        raise ConfigValidationError(f"{path} must be a string")

    return value, False


def _validate_object(
    config: dict[str, Any], schema: dict[str, Any], path: str
) -> tuple[dict[str, Any], bool]:
    changed = False
    for key, child_schema in schema.get("properties", {}).items():
        child_path = f"{path}.{key}" if path else key
        if key not in config:
            config[key] = _defaults_from_schema(child_schema)
            changed = True
            continue
        config[key], child_changed = _coerce_value(config[key], child_schema, child_path)
        changed = changed or child_changed
    return config, changed


def _migrate_dashboard_auth(config: dict[str, Any]) -> bool:
    """Migrate legacy auth_token to hashed password format."""
    dashboard = config.setdefault("dashboard", {})
    changed = False
    legacy_token = dashboard.pop("auth_token", "")
    if legacy_token:
        changed = True

    if not dashboard.get("enabled", True):
        return changed

    if legacy_token and not dashboard.get("password"):
        # Hash immediately so plaintext never hits disk
        import os
        from hashlib import pbkdf2_hmac

        salt = os.urandom(16)
        dk = pbkdf2_hmac("sha256", legacy_token.encode(), salt, 600_000)
        dashboard["password"] = f"pbkdf2:{salt.hex()}${dk.hex()}"
        changed = True

    return changed


def normalize_config(user_config: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Return a validated config and whether it should be written back to disk."""
    if user_config is None:
        user_config = {}
    if not isinstance(user_config, dict):
        raise ConfigValidationError("config root must be an object")

    changed = _has_missing_schema_keys(user_config, CONFIG_SCHEMA)
    config = _merge_config(DEFAULT_CONFIG, user_config)
    config, validate_changed = _validate_object(config, CONFIG_SCHEMA, "")
    changed = changed or validate_changed
    changed = _migrate_dashboard_auth(config) or changed
    return config, changed
