"""Process-local DAW bridge host-context cache."""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from core.platform.daw_agent import normalize_daw_host_context

BRIDGE_DEFAULT_CONTEXT_KEY = "__default__"
BRIDGE_MAX_CONTEXT_INSTANCES = 128
BRIDGE_CONTEXT_TTL_SECONDS = 10.0

# Local VST3 bridge context is process-local. Multi-worker Quart deployments
# will not share this cache; bridge mode should run single-worker unless this
# moves to shared storage.
_bridge_host_contexts: dict[str, dict[str, Any]] = {}


def bridge_export_instance_id(payload: Any) -> str | None:
    if not hasattr(payload, "get"):
        return None
    raw = payload.get("instance_id") or payload.get("bridge_instance_id")
    host_context = payload.get("host_context")
    if raw in (None, "") and isinstance(host_context, dict):
        raw = host_context.get("instance_id") or host_context.get("bridge_instance_id")
    instance_id = str(raw or "").strip()
    return instance_id or None


def record_bridge_host_context(payload: dict[str, Any]) -> dict[str, Any]:
    context = _normalize_bridge_host_context_payload(payload)
    key = _bridge_context_key(bridge_export_instance_id(payload))
    now = time.monotonic()
    _prune_expired_bridge_host_contexts(now=now)
    _bridge_host_contexts[key] = {"context": deepcopy(context), "updated_at": now}
    _trim_bridge_host_contexts()
    return deepcopy(context)


def bridge_host_context_for_instance(instance_id: str | None) -> dict[str, Any]:
    key = _bridge_context_key(instance_id)
    entry = _bridge_host_contexts.get(key)
    if not isinstance(entry, dict):
        return {}
    context = entry.get("context")
    updated_at = entry.get("updated_at")
    if not isinstance(context, dict) or not isinstance(updated_at, int | float):
        _bridge_host_contexts.pop(key, None)
        return {}
    if _bridge_context_is_expired(float(updated_at), now=time.monotonic()):
        _bridge_host_contexts.pop(key, None)
        return {}
    return deepcopy(context) if isinstance(context, dict) else {}


def _normalize_bridge_host_context_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("bridge context payload must be an object")

    host_context = normalize_daw_host_context(payload.get("host_context"), strict=True)
    host = str(payload.get("host") or "").strip()
    if host:
        host_context.update(normalize_daw_host_context({"host": host}, strict=True))
    if not host_context:
        raise ValueError("bridge context must include at least one supported field")
    return host_context


def _bridge_context_key(instance_id: str | None) -> str:
    instance_id = str(instance_id or "").strip()
    return instance_id or BRIDGE_DEFAULT_CONTEXT_KEY


def _trim_bridge_host_contexts() -> None:
    while len(_bridge_host_contexts) > BRIDGE_MAX_CONTEXT_INSTANCES:
        oldest_key = min(
            _bridge_host_contexts,
            key=lambda key: _bridge_context_updated_at(_bridge_host_contexts.get(key)),
        )
        _bridge_host_contexts.pop(oldest_key, None)


def _bridge_context_updated_at(entry: object) -> float:
    if not isinstance(entry, dict):
        return 0.0
    updated_at = entry.get("updated_at")
    if not isinstance(updated_at, int | float):
        return 0.0
    return float(updated_at)


def _prune_expired_bridge_host_contexts(*, now: float) -> None:
    for key, entry in list(_bridge_host_contexts.items()):
        updated_at = entry.get("updated_at") if isinstance(entry, dict) else None
        if not isinstance(updated_at, int | float) or _bridge_context_is_expired(
            float(updated_at),
            now=now,
        ):
            _bridge_host_contexts.pop(key, None)


def _bridge_context_is_expired(updated_at: float, *, now: float) -> bool:
    value = BRIDGE_CONTEXT_TTL_SECONDS
    try:
        ttl_seconds = float(value)
    except (TypeError, ValueError):
        ttl_seconds = 10.0
    return now - updated_at >= ttl_seconds
