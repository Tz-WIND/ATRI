"""Process-local DAW bridge host-context cache."""

from __future__ import annotations

import time
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from core.music_project import find_track
from core.platform.daw_agent import normalize_daw_host_context
from dashboard.studio import export_options
from dashboard.studio.host_projection import is_automation_track

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


def export_context_for_payload(payload: dict[str, Any], consumer: str) -> dict[str, Any]:
    if consumer != "bridge":
        return {}

    context = bridge_host_context_for_instance(bridge_export_instance_id(payload))
    raw_context = payload.get("host_context")
    if isinstance(raw_context, dict):
        try:
            explicit_context = normalize_daw_host_context(raw_context, strict=True)
        except ValueError as exc:
            raise export_options.StudioExportError(str(exc), 400) from exc
        context = {**context, **explicit_context}
    return context


def beat_range_for_context(
    payload: dict[str, Any],
    host_context: dict[str, Any],
) -> tuple[tuple[float, float] | None, str]:
    explicit = export_options.export_midi_beat_range(payload)
    if explicit is not None:
        return explicit, "explicit"

    selection = host_context.get("selection")
    if isinstance(selection, dict):
        selection_range = range_from_value(selection.get("range_beats"))
        if selection_range:
            return selection_range, "selection"

    if host_context.get("loop_active") is True:
        loop_range = range_from_value(host_context.get("loop_range_beats"))
        if loop_range:
            return loop_range, "loop"

    return None, "project"


def range_from_value(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        start = max(0.0, float(value[0]))
        end = float(value[1])
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return start, end


def midi_scope_for_payload(
    project: dict[str, Any],
    payload: dict[str, Any],
    target: str,
    consumer: str,
    host_context: dict[str, Any],
) -> tuple[str, list[int] | None]:
    explicit_track_ids = isinstance(payload.get("track_ids"), list) and bool(
        payload.get("track_ids")
    )
    target_was_explicit = "target" in payload or "scope" in payload
    if consumer == "bridge" and not explicit_track_ids:
        selected_track_ids = selection_track_ids(project, host_context)
        if selected_track_ids and (not target_was_explicit or target == "selected_tracks"):
            return "selected_tracks", selected_track_ids
    return target, midi_track_ids_for_payload(project, payload, target)


def selection_track_ids(
    project: dict[str, Any],
    host_context: dict[str, Any],
) -> list[int] | None:
    selection = host_context.get("selection")
    if not isinstance(selection, dict):
        return None
    selected_project_track_ids = project_track_ids(project, selection.get("project_track_ids"))
    if selected_project_track_ids:
        return selected_project_track_ids
    return project_track_ids_from_host_ids(project, selection.get("host_track_ids"))


def project_track_ids(project: dict[str, Any], raw_ids: Any) -> list[int] | None:
    if not isinstance(raw_ids, list) or not raw_ids:
        return None
    track_ids: list[int] = []
    for raw_id in raw_ids:
        try:
            track = find_track(project, int(raw_id))
        except (TypeError, ValueError):
            continue
        if is_automation_track(track):
            continue
        track_ids.append(int(track["id"]))
    return list(dict.fromkeys(track_ids)) or None


def project_track_ids_from_host_ids(
    project: dict[str, Any],
    raw_host_ids: Any,
) -> list[int] | None:
    if not isinstance(raw_host_ids, list) or not raw_host_ids:
        return None
    wanted = {str(item) for item in raw_host_ids}
    track_ids: list[int] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict) or is_automation_track(track):
            continue
        host_id = track.get("host_track_id")
        if host_id is None:
            continue
        if str(host_id) in wanted:
            try:
                track_ids.append(int(track["id"]))
            except (TypeError, ValueError):
                continue
    return list(dict.fromkeys(track_ids)) or None


def selection_summary(
    payload: dict[str, Any],
    host_context: dict[str, Any],
    *,
    track_ids: list[int] | None,
    beat_range: tuple[float, float] | None,
    range_source: str,
) -> dict[str, Any] | None:
    raw_summary = payload.get("selection_summary")
    summary = deepcopy(raw_summary) if isinstance(raw_summary, dict) else {}
    selection = host_context.get("selection")
    if isinstance(selection, dict):
        summary.update(deepcopy(selection))
    if beat_range and range_source in {"selection", "explicit"}:
        summary.setdefault("range_beats", [float(beat_range[0]), float(beat_range[1])])
    if track_ids:
        summary.setdefault("project_track_ids", [int(track_id) for track_id in track_ids])
    return summary or None


def beat_to_seconds(
    project: dict[str, Any],
    beat: float,
    host_context: dict[str, Any],
) -> float:
    """Convert musical beats to seconds using a single constant tempo."""
    tempo = context_tempo(host_context) or project_tempo(project)
    return max(0.0, float(beat)) * 60.0 / tempo


def seconds_range_from_beats(
    project: dict[str, Any],
    host_context: dict[str, Any],
    beat_range: tuple[float, float],
) -> tuple[float, float]:
    return (
        beat_to_seconds(project, beat_range[0], host_context),
        beat_to_seconds(project, beat_range[1], host_context),
    )


def context_tempo(host_context: dict[str, Any]) -> float | None:
    try:
        tempo = float(host_context.get("tempo_bpm") or 0.0)
    except (TypeError, ValueError):
        return None
    return tempo if tempo > 0 else None


def project_tempo(project: dict[str, Any]) -> float:
    try:
        tempo = float(project.get("tempo", 120.0) or 120.0)
    except (TypeError, ValueError):
        tempo = 120.0
    return max(1.0, tempo)


def created_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def midi_track_ids_for_payload(
    project: dict[str, Any],
    payload: dict[str, Any],
    target: str,
) -> list[int] | None:
    if target == "entire_project":
        return None
    raw_track_ids = payload.get("track_ids")
    if not isinstance(raw_track_ids, list) or not raw_track_ids:
        raise export_options.StudioExportError("track_ids is required for selected_tracks export")
    track_ids: list[int] = []
    for raw_track_id in raw_track_ids:
        try:
            track = find_track(project, int(raw_track_id))
        except (TypeError, ValueError) as exc:
            raise export_options.StudioExportError(f"track not found: {raw_track_id}", 404) from exc
        if is_automation_track(track):
            raise export_options.StudioExportError(f"track is not exportable: {raw_track_id}", 400)
        track_ids.append(int(track["id"]))
    return track_ids


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
