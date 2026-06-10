"""Automation-track mutations for Music Studio projects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.music import project_repository, track_lookup


def _project_model():
    from core.music import project_model

    return project_model


def _reject_legacy_time_signature_automation_target(target: Any) -> None:
    raw = target if isinstance(target, dict) else {}
    kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
    if kind == "time_signature_numerator":
        raise ValueError(_project_model().TIME_SIGNATURE_AUTOMATION_ERROR)


def automation_write(
    target: dict[str, Any],
    *,
    points: list[dict[str, Any]] | None = None,
    name: str = "",
    track_id: int | None = None,
    color: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create or replace a first-class project automation track."""
    music_project = _project_model()
    _reject_legacy_time_signature_automation_target(target)
    project = project_repository.load_project()
    normalized_target = music_project._normalize_automation_target(target)
    automation = music_project._normalize_automation_payload(
        {"points": points or []},
        target=normalized_target,
    )
    created = track_id is None

    if track_id is None:
        existing = [int(track["id"]) for track in project["tracks"]]
        track_id = max(existing, default=0) + 1
        track = music_project._new_automation_track(
            track_id,
            target=normalized_target,
            automation=automation,
            name=name,
            color=color,
        )
        project["tracks"].append(track)
    else:
        track = track_lookup.find_track(project, track_id)
        if track.get("type") != "automation":
            raise ValueError(f"track {track_id} is not an automation track")
        track["target"] = normalized_target
        track["automation"] = automation
        if name:
            track["name"] = str(name).strip() or track["name"]
        if color is not None:
            track["color"] = music_project._track_color(color, int(track["id"]) - 1)

    project = project_repository.save_project(project)
    saved_track = track_lookup.find_track(project, track_id)
    summary = {
        "track_id": int(saved_track["id"]),
        "created": created,
        "target": saved_track["target"],
        "points": len(saved_track["automation"]["points"]),
        "target_status": music_project._automation_target_status(
            project,
            saved_track["target"],
        ),
    }
    return project, summary


def automation_diff(
    track_id: int,
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply atomic edits to an existing automation track."""
    music_project = _project_model()
    project = project_repository.load_project()
    track = track_lookup.find_track(project, track_id)
    if track.get("type") != "automation":
        raise ValueError(f"track {track_id} is not an automation track")

    target = music_project._normalize_automation_target(track.get("target"))
    automation = music_project._normalize_automation_payload(track.get("automation"), target=target)
    points = list(automation["points"])
    changed = {"added": 0, "updated": 0, "deleted": 0}

    for raw_op in operations:
        if not isinstance(raw_op, dict):
            continue
        op = dict(raw_op)
        op_type = str(op.get("op") or op.get("type") or "").strip().lower()
        if op_type in {"add_point", "add"}:
            point = music_project._normalize_automation_point(op, target=target)
            points = music_project._upsert_automation_point(points, point)
            changed["added"] += 1
        elif op_type in {"update_point", "update"}:
            point = music_project._normalize_automation_point(op, target=target)
            points, updated = music_project._update_automation_point(points, point)
            changed["updated"] += updated
        elif op_type in {"delete_point", "delete"}:
            points, deleted = music_project._delete_automation_point(points, op)
            changed["deleted"] += deleted
        elif op_type in {"replace_range", "replace"}:
            start = music_project._non_negative_float(op.get("start"), 0.0)
            end = music_project._non_negative_float(op.get("end"), start)
            lo, hi = min(start, end), max(start, end)
            kept = [point for point in points if not lo - 1e-6 <= point["beat"] <= hi + 1e-6]
            changed["deleted"] += len(points) - len(kept)
            points = kept
            for raw_point in op.get("points") or []:
                if not isinstance(raw_point, dict):
                    continue
                point = music_project._normalize_automation_point(raw_point, target=target)
                points = music_project._upsert_automation_point(points, point)
                changed["added"] += 1
        else:
            raise ValueError(f"unsupported automation diff operation: {op_type}")

    automation["points"] = music_project._normalize_automation_points(points, target=target)
    track["target"] = target
    track["automation"] = automation
    project = project_repository.save_project(project)
    summary = {
        "track_id": int(track["id"]),
        "operations": len(operations),
        **changed,
    }
    return project, summary


def automation_retarget(
    track_id: int,
    target: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    music_project = _project_model()
    _reject_legacy_time_signature_automation_target(target)
    project = project_repository.load_project()
    track = track_lookup.find_track(project, track_id)
    if track.get("type") != "automation":
        raise ValueError(f"track {track_id} is not an automation track")
    normalized_target = music_project._normalize_automation_target(target)
    track["target"] = normalized_target
    track["automation"] = music_project._normalize_automation_payload(
        track.get("automation"),
        target=normalized_target,
    )
    project = project_repository.save_project(project)
    saved_track = track_lookup.find_track(project, track_id)
    summary = {
        "track_id": int(saved_track["id"]),
        "target": saved_track["target"],
        "target_status": music_project._automation_target_status(
            project,
            saved_track["target"],
        ),
    }
    return project, summary


def automation_query(
    *,
    track_id: int | None = None,
    include_points: bool = False,
) -> dict[str, Any]:
    music_project = _project_model()
    project = project_repository.load_project()
    automation_tracks = [
        track
        for track in project.get("tracks", [])
        if isinstance(track, dict)
        and track.get("type") == "automation"
        and (track_id is None or int(track.get("id", -1)) == int(track_id))
    ]
    rows = [
        music_project._automation_track_summary(project, track, include_points=include_points)
        for track in automation_tracks
    ]
    return {
        "automation_track_count": len(rows),
        "tracks": rows,
    }


def automation_learned_parameters_query() -> dict[str, Any]:
    project = project_repository.load_project()
    return {
        "learned_parameter_count": len(project.get("automation_learned_parameters", [])),
        "items": deepcopy(project.get("automation_learned_parameters", [])),
    }


def automation_learned_parameter_upsert(
    parameter: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    music_project = _project_model()
    project = project_repository.load_project()
    learned = music_project._normalize_learned_parameter(parameter)
    items = list(project.get("automation_learned_parameters", []))
    existing_index = next(
        (index for index, item in enumerate(items) if item.get("id") == learned["id"]),
        None,
    )
    if existing_index is None:
        items.append(learned)
        saved_item = learned
        created = True
    else:
        previous = items[existing_index]
        saved_item = {
            **learned,
            "name": str(previous.get("name") or learned["name"]),
            "created_at": str(previous.get("created_at") or learned["created_at"]),
        }
        items[existing_index] = saved_item
        created = False
    project["automation_learned_parameters"] = items
    project = project_repository.save_project(project)
    saved = next(
        item
        for item in project.get("automation_learned_parameters", [])
        if item["id"] == saved_item["id"]
    )
    return project, {**deepcopy(saved), "created": created}


def automation_learned_parameter_rename(
    parameter_id: str,
    name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    project = project_repository.load_project()
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("learned parameter name is required")
    items = list(project.get("automation_learned_parameters", []))
    for item in items:
        if item.get("id") == parameter_id:
            item["name"] = clean_name
            project["automation_learned_parameters"] = items
            project = project_repository.save_project(project)
            saved = next(
                saved_item
                for saved_item in project.get("automation_learned_parameters", [])
                if saved_item["id"] == parameter_id
            )
            return project, deepcopy(saved)
    raise ValueError(f"learned parameter {parameter_id} not found")
