"""Arrangement clip mutations for Music Studio projects."""

from __future__ import annotations

from typing import Any, cast

from core.music import project_repository


def _project_model():
    from core.music import project_model

    return project_model


def clip_diff(operations: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply atomic arrangement clip edits across project tracks."""
    music_project = _project_model()
    project = project_repository.load_project()
    changed = {"added": 0, "updated": 0, "deleted": 0}

    for raw_op in operations:
        if not isinstance(raw_op, dict):
            continue
        op = dict(raw_op)
        op_type = str(op.get("op") or op.get("type") or "").strip().lower()
        if op_type in {"add_clip", "add"}:
            target_track = music_project._target_track_for_clip_op(project, op)
            raw_clip = op.get("clip") if isinstance(op.get("clip"), dict) else op
            clip = music_project._normalize_clip(
                cast(dict[str, Any], raw_clip),
                track_color=str(target_track.get("color") or music_project.DEFAULT_TRACK_COLORS[0]),
            )
            if music_project._find_clip_record(project, str(clip["id"])) is not None:
                raise ValueError(f"clip {clip['id']} already exists")
            target_clips = target_track.get("clips")
            if not isinstance(target_clips, list):
                target_clips = []
            target_track["clips"] = [*target_clips, clip]
            changed["added"] += 1
        elif op_type in {"update_clip", "move_clip", "resize_clip", "update"}:
            clip_id = music_project._clip_id_from_op(op)
            record = music_project._find_clip_record(project, clip_id)
            if record is None:
                raise ValueError(f"clip {clip_id} not found")
            source_track = record["track"]
            existing_clip = record["clip"]
            target_track = music_project._target_track_for_clip_op(
                project,
                op,
                default_track=source_track,
            )
            patch = op.get("clip") if isinstance(op.get("clip"), dict) else {}
            merged = {
                **existing_clip,
                **cast(dict[str, Any], patch),
                **{
                    key: op[key]
                    for key in (
                        "name",
                        "type",
                        "start",
                        "duration",
                        "duration_seconds",
                        "color",
                        "source",
                        "path",
                        "source_offset",
                        "offset",
                        "gain",
                        "waveform",
                        "notes",
                        "events",
                    )
                    if key in op
                },
                "id": clip_id,
            }
            updated_clip = music_project._normalize_clip(
                merged,
                track_color=str(
                    target_track.get("color")
                    or existing_clip.get("color")
                    or music_project.DEFAULT_TRACK_COLORS[0]
                ),
            )
            music_project._remove_clip_from_track(source_track, clip_id)
            target_clips = target_track.get("clips")
            if not isinstance(target_clips, list):
                target_clips = []
            target_track["clips"] = [*target_clips, updated_clip]
            changed["updated"] += 1
        elif op_type in {"delete_clip", "delete"}:
            clip_id = music_project._clip_id_from_op(op)
            record = music_project._find_clip_record(project, clip_id)
            if record is None:
                continue
            music_project._remove_clip_from_track(record["track"], clip_id)
            changed["deleted"] += 1
        else:
            raise ValueError(f"unsupported clip diff operation: {op_type}")

    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        clips = track.get("clips")
        if isinstance(clips, list):
            track["clips"] = sorted(
                [clip for clip in clips if isinstance(clip, dict)],
                key=lambda clip: (
                    float(clip.get("start", 0.0) or 0.0),
                    str(clip.get("type") or ""),
                    str(clip.get("name") or ""),
                ),
            )

    project = project_repository.save_project(project)
    summary = {
        "operations": len(operations),
        **changed,
        "clip_count": sum(len(track.get("clips", [])) for track in project.get("tracks", [])),
    }
    return project, summary
