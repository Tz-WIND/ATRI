"""Project-level piano lane mutations for meter and harmony events."""

from __future__ import annotations

from typing import Any

from core.music import project_repository


def _project_model():
    from core.music import project_model

    return project_model


def piano_lane_write(
    lane: str,
    events: list[dict[str, Any]],
    *,
    mode: str = "replace",
    start: float | None = None,
    end: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replace or append project-level piano meter and harmony lane events."""
    music_project = _project_model()
    lane_id = music_project._normalize_piano_lane_id(lane)
    if mode not in {"replace", "append"}:
        raise ValueError("mode must be 'replace' or 'append'")

    project = project_repository.load_project()
    field = music_project._piano_lane_event_field(lane_id)
    current_events = music_project._normalize_piano_lane_events(lane_id, project.get(field))
    incoming_events = music_project._normalize_piano_lane_events(lane_id, events)
    removed = 0

    if mode == "replace":
        event_range = music_project._normalize_piano_lane_range(start, end)
        if event_range is None:
            removed = len(current_events)
            next_events = incoming_events
        else:
            range_start, range_end = event_range
            kept_events = []
            for event in current_events:
                if music_project._piano_lane_beat_in_range(
                    float(event["beat"]),
                    range_start,
                    range_end,
                ):
                    removed += 1
                else:
                    kept_events.append(event)
            next_events = [*kept_events, *incoming_events]
    else:
        next_events = [*current_events, *incoming_events]

    project[field] = music_project._normalize_piano_lane_events(lane_id, next_events)
    music_project._ensure_piano_subtrack_order(project, lane_id)
    project = project_repository.save_project(project)
    return project, music_project._piano_lane_write_summary(
        project,
        lane_id,
        mode,
        len(incoming_events),
        removed,
    )


def piano_lane_diff(
    lane: str,
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply atomic edits to project-level piano meter and harmony lane events."""
    music_project = _project_model()
    lane_id = music_project._normalize_piano_lane_id(lane)
    project = project_repository.load_project()
    field = music_project._piano_lane_event_field(lane_id)
    events = music_project._normalize_piano_lane_events(lane_id, project.get(field))
    changed = {"added": 0, "updated": 0, "deleted": 0}

    for op in operations:
        op_type = str(op.get("op") or op.get("type") or "").strip().lower()
        if op_type in {"add_event", "add"}:
            added = music_project._event_from_piano_lane_op(lane_id, op)
            events, did_add = music_project._upsert_piano_lane_event(events, added)
            changed["added" if did_add else "updated"] += 1
        elif op_type in {"update_event", "update"}:
            events, did_update = music_project._update_piano_lane_event(lane_id, events, op)
            changed["updated" if did_update else "added"] += 1
        elif op_type in {"delete_event", "delete"}:
            events, deleted = music_project._delete_piano_lane_event(events, op)
            changed["deleted"] += deleted
        elif op_type == "replace_range":
            events, replaced = music_project._replace_piano_lane_event_range(lane_id, events, op)
            changed["deleted"] += replaced["deleted"]
            changed["added"] += replaced["added"]
        else:
            raise ValueError(f"unsupported piano lane diff operation: {op_type}")

    project[field] = music_project._normalize_piano_lane_events(lane_id, events)
    music_project._ensure_piano_subtrack_order(project, lane_id)
    project = project_repository.save_project(project)
    return project, music_project._piano_lane_diff_summary(
        project,
        lane_id,
        len(operations),
        changed,
    )
