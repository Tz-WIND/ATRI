"""Plugin slot, state, and native editor helpers for studio routes."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, cast

ProjectSync = Callable[..., Awaitable[dict[str, Any]]]


def track_slot(track: dict[str, Any], slot_id: str) -> dict[str, Any]:
    for slot in track.get("plugin_slots", []):
        if isinstance(slot, dict) and slot.get("id") == slot_id:
            return cast(dict[str, Any], slot)
    if slot_id == "instrument":
        return {
            "id": "instrument",
            "type": "builtin",
            "name": track.get("instrument") or "ATRI Basic Synth",
        }
    return {"id": slot_id, "type": "empty", "name": "Empty"}


def instrument_slot(track: dict[str, Any]) -> dict[str, Any]:
    return track_slot(track, "instrument")


def slot_index(slot_id: str) -> int:
    if slot_id == "instrument":
        return 0
    match = re.fullmatch(r"insert_(\d+)", slot_id)
    if match:
        return min(255, max(1, int(match.group(1))))
    return 255


async def load_track_slot(
    host: Any,
    host_track_id: int,
    slot: dict[str, Any],
) -> dict[str, Any]:
    slot_id = str(slot.get("id") or "instrument")
    index = slot_index(slot_id)
    slot_type = str(slot.get("type") or "empty")

    if slot.get("type") == "vst3" and slot.get("path"):
        response = cast(
            dict[str, Any],
            await host.send_command(
                "load_vst3",
                {
                    "track_id": int(host_track_id),
                    "slot_index": index,
                    "path": str(slot.get("path") or ""),
                    "name": str(slot.get("name") or "") or None,
                },
            ),
        )
        return await restore_slot_state(host, host_track_id, index, slot, response)
    if slot_type == "vst2":
        clear_response = await host.send_command(
            "clear_processor_slot",
            {"track_id": int(host_track_id), "slot_index": index},
        )
        return {
            "type": "error",
            "cmd": "load_vst2",
            "slot_id": slot_id,
            "slot_index": index,
            "message": "VST2 scan is available, but VST2 loading is not implemented yet",
            "clear": clear_response,
        }
    if slot_id == "instrument":
        response = cast(
            dict[str, Any],
            await host.send_command(
                "load_builtin_synth",
                {"track_id": int(host_track_id), "slot_index": index},
            ),
        )
        return await restore_slot_state(host, host_track_id, index, slot, response)
    return cast(
        dict[str, Any],
        await host.send_command(
            "clear_processor_slot",
            {"track_id": int(host_track_id), "slot_index": index},
        ),
    )


async def restore_slot_state(
    host: Any,
    host_track_id: int,
    slot_index: int,
    slot: dict[str, Any],
    load_response: dict[str, Any],
) -> dict[str, Any]:
    state_b64 = str(slot.get("state_b64") or "")
    if not state_b64:
        return load_response
    state_response = cast(
        dict[str, Any],
        await host.send_command(
            "set_plugin_state",
            {
                "track_id": int(host_track_id),
                "slot_index": int(slot_index),
                "state_b64": state_b64,
            },
        ),
    )
    return {**load_response, "state": state_response}


async def load_track_slots(
    host: Any,
    host_track_id: int,
    track: dict[str, Any],
) -> list[dict[str, Any]]:
    if track.get("type") == "audio":
        return []
    raw_slots = track.get("plugin_slots")
    if track.get("type") == "bus":
        slots = raw_slots if isinstance(raw_slots, list) else []
    else:
        slots = raw_slots if isinstance(raw_slots, list) and raw_slots else [instrument_slot(track)]
    commands = []
    for slot in slots:
        if isinstance(slot, dict):
            commands.append(await load_track_slot(host, host_track_id, slot))
    return commands


async def capture_plugin_states(
    project: dict[str, Any],
    *,
    host_manager: Callable[[], Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    host = host_manager()
    if not host.is_running:
        return project, []

    responses: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        if track.get("type") == "audio":
            continue
        host_track_id = track.get("host_track_id")
        if host_track_id is None:
            continue
        slots = track.get("plugin_slots")
        if not isinstance(slots, list):
            slots = []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if slot.get("type") in {"empty", "vst2"}:
                continue
            slot_id = str(slot.get("id") or "instrument")
            index = slot_index(slot_id)
            response = await host.send_command(
                "get_plugin_state",
                {"track_id": int(host_track_id), "slot_index": index},
            )
            responses.append(
                {
                    "track_id": track.get("id"),
                    "host_track_id": int(host_track_id),
                    "slot_id": slot_id,
                    "slot_index": index,
                    "response": response,
                }
            )
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            state_b64 = str(data.get("state_b64") or "")
            if state_b64:
                slot["state_b64"] = state_b64

    return project, responses


async def capture_and_save_plugin_states(
    project: dict[str, Any] | None = None,
    *,
    host_manager: Callable[[], Any],
    load_project: Callable[[], dict[str, Any]],
    save_project: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    project = project if isinstance(project, dict) else load_project()
    project, responses = await capture_plugin_states(project, host_manager=host_manager)
    if responses:
        project = save_project(project)
    return project, responses


async def open_plugin_editor_for_track(
    track_id: int,
    *,
    slot_id: str,
    load_project: Callable[[], dict[str, Any]],
    find_track: Callable[[dict[str, Any], Any], dict[str, Any]],
    host_manager: Callable[[], Any],
    host_snapshot: Callable[[], dict[str, Any]],
    sync_project_to_host: ProjectSync,
) -> tuple[dict[str, Any], int]:
    project = load_project()
    try:
        track = find_track(project, track_id)
    except ValueError as error:
        return {"ok": False, "error": str(error), "host": host_snapshot()}, 404

    host = host_manager()
    if not host.is_running:
        return {"ok": False, "error": "host process not running", "host": host_snapshot()}, 409

    sync = None
    if track.get("host_track_id") is None:
        sync = await sync_project_to_host(project, broadcast=True)
        project = sync.get("project", project)
        track = find_track(project, track_id)

    host_track_id = track.get("host_track_id")
    if host_track_id is None:
        return {
            "ok": False,
            "error": "track is not synced to the host",
            "host": host_snapshot(),
            "sync": sync,
        }, 409

    slot = track_slot(track, slot_id)
    if slot.get("type") in {"empty", "vst2"}:
        return {
            "ok": False,
            "error": "selected plugin slot does not have a native editor",
            "host": host_snapshot(),
            "plugin": slot,
            "sync": sync,
        }, 409

    index = slot_index(slot_id)
    response = await host.send_command(
        "open_plugin_editor",
        {"track_id": int(host_track_id), "slot_index": index},
    )
    ok = response.get("type") == "ack"
    status = 200 if ok else 409
    return {
        "ok": ok,
        "project_track_id": int(track_id),
        "host_track_id": int(host_track_id),
        "slot_id": slot_id,
        "slot_index": index,
        "plugin": slot,
        "response": response,
        "sync": sync,
        "host": host_snapshot(),
    }, status


def slot_id_from_index(slot_index: int) -> str:
    if slot_index <= 0:
        return "instrument"
    return f"insert_{slot_index}"


def captured_parameter_for_project(
    project: dict[str, Any],
    captured: dict[str, Any],
) -> dict[str, Any] | None:
    host_track_id_raw = captured.get("track_id")
    if host_track_id_raw is None or host_track_id_raw == "":
        return None
    try:
        host_track_id = int(str(host_track_id_raw))
    except (TypeError, ValueError):
        return None
    project_track = next(
        (
            track
            for track in project.get("tracks", [])
            if isinstance(track, dict)
            and track.get("host_track_id") is not None
            and int(track.get("host_track_id", -1)) == host_track_id
        ),
        None,
    )
    if not project_track:
        return None
    index = int(captured.get("slot_index", 0) or 0)
    slot_id = slot_id_from_index(index)
    slot = track_slot(project_track, slot_id)
    param_index = int(captured.get("param_index", captured.get("index", 0)) or 0)
    param_name = str(captured.get("name") or f"Parameter {param_index}")
    target: dict[str, Any] = {
        "kind": "plugin_parameter",
        "track_id": int(project_track["id"]),
        "slot_id": slot_id,
        "param_index": param_index,
        "label": param_name,
    }
    param_id = captured.get("param_id")
    if param_id is not None and param_id != "":
        target["param_id"] = int(str(param_id))
    return {
        "target": target,
        "source": {
            "track_name": str(project_track.get("name") or f"Track {project_track['id']}"),
            "slot_id": slot_id,
            "slot_label": "Instrument" if slot_id == "instrument" else f"Insert {index}",
            "plugin_name": str(captured.get("plugin_name") or slot.get("name") or "Plugin"),
            "param_name": param_name,
            "units": str(captured.get("units") or ""),
        },
        "value": float(captured.get("value", 0.0) or 0.0),
    }
