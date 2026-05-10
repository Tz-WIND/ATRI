"""Model, settings, and provider routes."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from quart import jsonify, request

if TYPE_CHECKING:
    from dashboard.server import Dashboard

_PROCESS_STAGE_SETTING_KEYS = {
    "model",
    "api_key",
    "base_url",
    "api_format",
    "active_models",
    "providers",
    "max_tokens",
    "max_context_tokens",
    "max_rounds",
    "temperature",
    "extra_instructions",
    "persona",
    "agent_mode",
    "image_transcription",
    "skills_root",
    "skill_search_roots",
    "tavily_api_key",
    "mcp_servers",
}


def _mask_providers(providers: dict) -> dict:
    result = {}
    for name, cfg in (providers or {}).items():
        if not isinstance(cfg, dict):
            continue
        result[name] = {**cfg, "api_key": "***" if cfg.get("api_key") else ""}
    return result


def _mask_image_transcription(cfg: dict | None) -> dict:
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "model": cfg.get("model", ""),
        "api_key": "***" if cfg.get("api_key") else "",
        "base_url": cfg.get("base_url") or "",
        "api_format": cfg.get("api_format", "openai"),
        "prompt": cfg.get("prompt", ""),
        "max_tokens": cfg.get("max_tokens", 1024),
        "temperature": cfg.get("temperature", 0.0),
    }


def _merge_image_transcription_config(existing: dict | None, incoming: object) -> dict:
    if not isinstance(incoming, dict):
        raise ValueError("image_transcription must be an object")
    merged = dict(existing or {})
    for key in ("model", "base_url", "api_format", "prompt"):
        if key in incoming:
            merged[key] = str(incoming.get(key) or "")
    if "enabled" in incoming:
        merged["enabled"] = bool(incoming["enabled"])
    if "api_key" in incoming and incoming["api_key"] != "***":
        merged["api_key"] = str(incoming.get("api_key") or "")
    if "max_tokens" in incoming:
        merged["max_tokens"] = max(1, int(incoming["max_tokens"]))
    if "temperature" in incoming:
        merged["temperature"] = float(incoming["temperature"])
    return merged


# ── Model-list fetching helpers ──


def _model_url_candidates(base_url: str) -> list[str]:
    """Build likely model-list URLs from an OpenAI-compatible or Anthropic base URL."""
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return ["https://api.openai.com/v1/models"]
    if "://" not in cleaned:
        cleaned = "https://" + cleaned

    parsed = urlsplit(cleaned)
    path = parsed.path.rstrip("/")
    lower_path = path.lower()

    if lower_path.endswith("/models"):
        return [cleaned]

    if lower_path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
        cleaned = urlunsplit(parsed._replace(path=path))
    elif lower_path.endswith("/messages"):
        path = path[: -len("/messages")]
        cleaned = urlunsplit(parsed._replace(path=path))

    candidates = [cleaned.rstrip("/") + "/models"]
    if "/v1" not in lower_path:
        candidates.append(cleaned.rstrip("/") + "/v1/models")
    return list(dict.fromkeys(candidates))


def _headers_for_model_fetch(api_format: str, api_key: str) -> dict:
    if api_format == "anthropic":
        headers = {"anthropic-version": "2023-06-01"}
        if api_key:
            headers["x-api-key"] = api_key
        return headers
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _model_fetch_candidates(
    base_url: str,
    api_format: str,
    api_key: str,
) -> list[tuple[str, dict, str]]:
    """Build model-list attempts as (url, headers, pagination_format)."""
    candidates: list[tuple[str, dict, str]] = []

    def add(url: str, headers: dict, fetch_format: str):
        key = (url, fetch_format)
        if not any((u, f) == key for u, _, f in candidates):
            candidates.append((url, headers, fetch_format))

    for url in _model_url_candidates(base_url):
        add(url, _headers_for_model_fetch(api_format, api_key), api_format)

    if api_format == "anthropic":
        cleaned = (base_url or "").strip().rstrip("/")
        if cleaned and "://" not in cleaned:
            cleaned = "https://" + cleaned
        parsed = urlsplit(cleaned)
        parts = [part for part in parsed.path.split("/") if part]
        if "anthropic" in [part.lower() for part in parts]:
            stripped_parts = [part for part in parts if part.lower() != "anthropic"]
            stripped_path = "/" + "/".join(stripped_parts) if stripped_parts else ""
            stripped = urlunsplit(parsed._replace(path=stripped_path)).rstrip("/")
            openai_headers = _headers_for_model_fetch("openai", api_key)
            for url in _model_url_candidates(stripped):
                add(url, openai_headers, "openai")

    return candidates


def _extract_model_ids(body: Any) -> list[str]:
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        items = body.get("data") or body.get("models") or body.get("result") or []
    else:
        items = []

    models: list[str] = []
    for item in items:
        model_id = ""
        if isinstance(item, str):
            model_id = item
        elif isinstance(item, dict):
            for key in ("id", "name", "model"):
                value = item.get(key)
                if isinstance(value, str):
                    model_id = value
                    break
        if model_id:
            models.append(model_id)
    return sorted(set(models))


async def _fetch_model_ids(client: Any, url: str, headers: dict, api_format: str) -> list[str]:
    models: list[str] = []
    params: dict | None = {"limit": 100} if api_format == "anthropic" else None

    for _ in range(20):
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        body = resp.json()
        models.extend(_extract_model_ids(body))

        if api_format != "anthropic" or not isinstance(body, dict) or not body.get("has_more"):
            break
        after_id = body.get("last_id")
        if not isinstance(after_id, str) or not after_id.strip():
            break
        params = {"limit": 100, "after_id": after_id}

    return sorted(set(models))


# ── Route registration ──


def register(dashboard: Dashboard) -> None:
    app = dashboard.app

    @app.route("/api/status")
    async def api_status():
        lc = dashboard.lifecycle
        return jsonify(
            {
                "status": "running",
                "uptime": int(time.time() - lc.start_time) if lc.start_time else 0,
                "model": lc.config.get("model", ""),
                "active_models": lc.config.get("active_models", []),
                "workspace": lc.config.get("workspace", ""),
                "api_format": lc.config.get("api_format", "openai"),
                "agent_mode": (
                    lc.process_stage.agent_mode
                    if lc.process_stage
                    else lc.config.get("agent_mode", "agent")
                ),
                "onebot11_status": lc.onebot11.status.value if lc.onebot11 else "disabled",
                "webchat_status": lc.webchat.status.value if lc.webchat else "disabled",
                "session_count": lc.process_stage.agent_count if lc.process_stage else 0,
                "mcp_server_count": len(lc.config.get("mcp_servers", {})),
                "skill_count": len(lc.config.get("skills", {})),
            }
        )

    @app.route("/api/settings", methods=["GET"])
    async def get_settings():
        c = dashboard.lifecycle.config
        return jsonify(
            {
                "model": c.get("model", ""),
                "api_key": "***" if c.get("api_key") else "",
                "base_url": c.get("base_url") or "",
                "api_format": c.get("api_format", "openai"),
                "max_tokens": c.get("max_tokens", 4096),
                "temperature": c.get("temperature", 0.0),
                "max_context_tokens": c.get("max_context_tokens", 128000),
                "max_rounds": c.get("max_rounds", 50),
                "wake_words": c.get("wake_words", []),
                "extra_instructions": c.get("extra_instructions", ""),
                "persona": c.get("persona", ""),
                "agent_mode": c.get("agent_mode", "agent"),
                "image_transcription": _mask_image_transcription(c.get("image_transcription", {})),
                "skills_root": c.get("skills_root", "skills"),
                "skill_search_roots": c.get("skill_search_roots", []),
                "providers": _mask_providers(c.get("providers", {})),
                "tavily_api_key": "***" if c.get("tavily_api_key") else "",
            }
        )

    @app.route("/api/settings", methods=["POST"])
    async def update_settings():
        data = await request.get_json()
        lc = dashboard.lifecycle
        if "agent_mode" in data:
            from core.agent.mode import normalize_agent_mode

            try:
                data["agent_mode"] = normalize_agent_mode(data["agent_mode"])
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
        for key in [
            "model",
            "base_url",
            "api_format",
            "extra_instructions",
            "persona",
            "agent_mode",
        ]:
            if key in data:
                lc.config[key] = data[key]
        if "skills_root" in data:
            lc.config["skills_root"] = data["skills_root"]
        if "skill_search_roots" in data:
            lc.config["skill_search_roots"] = data["skill_search_roots"]
        if "api_key" in data and data["api_key"] != "***":
            lc.config["api_key"] = data["api_key"]
        for key in ["max_tokens", "max_context_tokens", "max_rounds"]:
            if key in data:
                lc.config[key] = int(data[key])
        if "temperature" in data:
            lc.config["temperature"] = float(data["temperature"])
        if "wake_words" in data:
            lc.config["wake_words"] = data["wake_words"]
        if "tavily_api_key" in data and data["tavily_api_key"] != "***":
            lc.config["tavily_api_key"] = data["tavily_api_key"]
        if "image_transcription" in data:
            try:
                lc.config["image_transcription"] = _merge_image_transcription_config(
                    lc.config.get("image_transcription", {}),
                    data["image_transcription"],
                )
                data["image_transcription"] = lc.config["image_transcription"]
            except (TypeError, ValueError) as e:
                return jsonify({"error": str(e)}), 400
        if lc.process_stage:
            lc.process_stage.update_config(
                **{k: v for k, v in data.items() if k in _PROCESS_STAGE_SETTING_KEYS}
            )
        lc.save_config()
        return jsonify({"ok": True})

    # ── Model Providers ──

    @app.route("/api/provider/list", methods=["GET"])
    async def list_providers():
        return jsonify(_mask_providers(dashboard.lifecycle.config.get("providers", {})))

    @app.route("/api/provider/save", methods=["POST"])
    async def save_provider():
        data = await request.get_json()
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        providers = dashboard.lifecycle.config.setdefault("providers", {})
        existing = providers.get(name, {})
        providers[name] = {
            "base_url": data.get("base_url", ""),
            "api_key": data["api_key"]
            if data.get("api_key") and data["api_key"] != "***"
            else existing.get("api_key", ""),
            "api_format": data.get("api_format", "openai"),
            "models": existing.get("models", []),
        }
        if dashboard.lifecycle.process_stage:
            dashboard.lifecycle.process_stage.update_config(
                providers=providers,
                active_models=dashboard.lifecycle.config.get("active_models", []),
            )
        dashboard.lifecycle.save_config()
        return jsonify({"ok": True})

    @app.route("/api/provider/delete", methods=["POST"])
    async def delete_provider():
        data = await request.get_json()
        name = data.get("name", "")
        lc = dashboard.lifecycle
        providers = lc.config.setdefault("providers", {})
        removed_provider = providers.pop(name, None)
        active_models = lc.config.setdefault("active_models", [])
        removed_entries = [
            m for m in active_models if isinstance(m, dict) and m.get("provider", "") == name
        ]
        lc.config["active_models"] = [
            m for m in active_models if not (isinstance(m, dict) and m.get("provider", "") == name)
        ]
        current_model = lc.config.get("model", "")
        current_was_removed = any(m.get("model", "") == current_model for m in removed_entries)
        current_still_active = any(
            dashboard._active_model_entry_available(m) and m.get("model", "") == current_model
            for m in lc.config.get("active_models", [])
        )
        if current_was_removed and (
            not current_still_active or dashboard._current_uses_provider_config(removed_provider)
        ):
            dashboard._select_first_active_model_or_clear()
        else:
            dashboard._push_model_config()
        dashboard.lifecycle.save_config()
        return jsonify({"ok": True})

    @app.route("/api/provider/models", methods=["POST"])
    async def get_provider_models():
        """Fetch available models from a provider's API."""
        data = await request.get_json()
        name = data.get("name", "")
        providers = dashboard.lifecycle.config.get("providers", {})
        if name not in providers:
            return jsonify({"error": "provider not found"}), 404
        cfg = providers[name]
        api_key = cfg.get("api_key", "")
        if data.get("api_key") and data["api_key"] != "***":
            api_key = data["api_key"]
        base_url = data.get("base_url", cfg.get("base_url", ""))
        api_format = data.get("api_format", cfg.get("api_format", "openai"))
        try:
            import httpx as _httpx

            last_error = None
            default_base_url = "https://api.anthropic.com/v1" if api_format == "anthropic" else ""
            effective_base_url = base_url or default_base_url
            async with _httpx.AsyncClient(timeout=15) as client:
                for url, headers, fetch_format in _model_fetch_candidates(
                    effective_base_url,
                    api_format,
                    api_key,
                ):
                    try:
                        models = await _fetch_model_ids(
                            client,
                            url,
                            headers,
                            fetch_format,
                        )
                        break
                    except Exception as e:
                        last_error = e
                else:
                    raise last_error or RuntimeError("failed to fetch models")

            providers[name]["models"] = models
            providers[name]["base_url"] = effective_base_url
            providers[name]["api_format"] = api_format
            if data.get("api_key") and data["api_key"] != "***":
                providers[name]["api_key"] = api_key
            if dashboard.lifecycle.process_stage:
                dashboard.lifecycle.process_stage.update_config(
                    providers=providers,
                    active_models=dashboard.lifecycle.config.get("active_models", []),
                )
            dashboard.lifecycle.save_config()
            return jsonify({"models": models})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/provider/activate", methods=["POST"])
    async def activate_model():
        """Add a model to the active models list and optionally select it."""
        data = await request.get_json()
        provider_name = data.get("provider", "")
        model = data.get("model", "")
        if not model:
            return jsonify({"error": "model required"}), 400
        lc = dashboard.lifecycle
        active_models = lc.config.setdefault("active_models", [])
        entry = {"model": model, "provider": provider_name}
        if not any(m["model"] == model and m["provider"] == provider_name for m in active_models):
            active_models.append(entry)
        dashboard._apply_model(provider_name, model)
        lc.save_config()
        return jsonify({"ok": True})

    @app.route("/api/provider/deactivate", methods=["POST"])
    async def deactivate_model():
        """Remove a model from the active models list."""
        data = await request.get_json()
        provider_name = data.get("provider", "")
        model = data.get("model", "")
        lc = dashboard.lifecycle
        active_models = lc.config.setdefault("active_models", [])
        removed_entries = [
            m
            for m in active_models
            if (
                isinstance(m, dict)
                and m.get("model", "") == model
                and m.get("provider", "") == provider_name
            )
        ]
        lc.config["active_models"] = [
            m
            for m in active_models
            if not (
                isinstance(m, dict)
                and m.get("model", "") == model
                and m.get("provider", "") == provider_name
            )
        ]
        current_model = lc.config.get("model", "")
        current_still_active = any(
            dashboard._active_model_entry_available(m) and m.get("model", "") == current_model
            for m in lc.config.get("active_models", [])
        )
        provider_cfg = lc.config.get("providers", {}).get(provider_name)
        if (
            removed_entries
            and current_model == model
            and (not current_still_active or dashboard._current_uses_provider_config(provider_cfg))
        ):
            dashboard._select_first_active_model_or_clear()
        else:
            dashboard._push_model_config()
        lc.save_config()
        return jsonify({"ok": True})

    @app.route("/api/provider/select", methods=["POST"])
    async def select_model():
        """Switch to a specific active model for chatting."""
        data = await request.get_json()
        provider_name = data.get("provider", "")
        model = data.get("model", "")
        if not model:
            return jsonify({"error": "model required"}), 400
        dashboard._apply_model(provider_name, model)
        dashboard.lifecycle.save_config()
        return jsonify({"ok": True})
