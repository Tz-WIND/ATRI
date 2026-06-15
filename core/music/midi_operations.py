"""High-level MIDI mutations and queries for Music Studio projects."""

from __future__ import annotations

from typing import Any, cast

from core.music import project_repository, track_lookup


def _project_model():
    from core.music import project_model

    return project_model


def midi_write(
    track_id: int,
    notes: list[dict[str, Any]],
    *,
    start: float | None = None,
    end: float | None = None,
    mode: str = "replace",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Overwrite or append MIDI notes on a track.

    Times are stored in beats to match the Rust sequencer.
    """
    music_project = _project_model()
    if mode not in {"replace", "append"}:
        raise ValueError("mode must be 'replace' or 'append'")

    state: dict[str, Any] = {}

    def mutate(project: dict[str, Any]) -> dict[str, Any]:
        nonlocal start, end

        track = track_lookup.find_track(project, track_id)
        clip = music_project._ensure_midi_clip(track)
        normalized_notes = [music_project._normalize_note(note) for note in notes]

        if start is None:
            start = min((note["start"] for note in normalized_notes), default=0.0)
        if end is None:
            end = max(
                (note["start"] + note["duration"] for note in normalized_notes),
                default=start,
            )
        start = max(0.0, float(start))
        end = max(start, float(end))

        removed = 0
        if mode == "replace":
            kept = []
            for note in clip["notes"]:
                overlaps = note["start"] <= end and (note["start"] + note["duration"]) > start
                if overlaps:
                    removed += 1
                else:
                    kept.append(note)
            clip["notes"] = kept

        clip["notes"].extend(normalized_notes)
        clip["notes"].sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
        music_project._update_midi_clip_duration(clip)
        state.update(
            {
                "track_id": int(track["id"]),
                "host_track_id": track.get("host_track_id"),
                "notes_added": len(normalized_notes),
                "notes_removed": removed,
            }
        )
        return project

    project = project_repository.update_project(mutate)
    synced_track = track_lookup.find_track(project, state["track_id"])
    summary = {
        "track_id": state["track_id"],
        "requested_track_id": track_id,
        "host_track_id": state["host_track_id"],
        "mode": mode,
        "range": [start, end],
        "notes_added": state["notes_added"],
        "notes_removed": state["notes_removed"],
        "track_note_count": len(synced_track["notes"]),
    }
    return project, summary


def midi_diff(
    track_id: int,
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    music_project = _project_model()
    changed: dict[str, Any] = {
        "added": 0,
        "deleted": 0,
        "updated": 0,
        "events_added": 0,
        "events_deleted": 0,
        "events_updated": 0,
        "curves_written": 0,
    }
    state: dict[str, Any] = {}

    def mutate(project: dict[str, Any]) -> dict[str, Any]:
        track = track_lookup.find_track(project, track_id)
        state["track_id"] = int(track["id"])
        state["host_track_id"] = track.get("host_track_id")

        for op in operations:
            op_type = str(op.get("op") or op.get("type") or "").strip().lower()
            if op_type == "add_note":
                raw_note = op.get("note")
                note_data = cast(dict[str, Any], raw_note) if isinstance(raw_note, dict) else op
                if isinstance(raw_note, dict) and "clip_id" in op and "clip_id" not in note_data:
                    note_data = {**note_data, "clip_id": op["clip_id"]}
                clip = music_project._target_clip_for_timeline_write(track, note_data, create=True)
                clip["notes"].append(
                    music_project._normalize_note(
                        music_project._note_payload_to_clip_local(note_data, clip)
                    )
                )
                changed["added"] += 1
            elif op_type == "delete_note":
                changed["deleted"] += music_project._delete_timeline_notes(track, op)
            elif op_type in {"update_note", "modify_note"}:
                note_ref = music_project._find_timeline_note(track, op)
                if note_ref is None:
                    continue
                clip = note_ref["clip"]
                note = note_ref["note"]
                payload = dict(note)
                for key in ("pitch", "duration", "velocity"):
                    if key in op:
                        payload[key] = op[key]
                if music_project._payload_has_start(op):
                    payload["start"] = music_project._payload_start_to_clip_local(op, clip)
                note.clear()
                note.update(music_project._normalize_note(payload))
                changed["updated"] += 1
            elif op_type in {"add_event", "add_midi_event"}:
                payload = music_project._event_payload_from_op(op)
                clip = music_project._target_clip_for_timeline_write(track, payload, create=True)
                clip["events"].append(
                    music_project._normalize_midi_event(
                        music_project._event_payload_to_clip_local(payload, clip)
                    )
                )
                changed["events_added"] += 1
            elif op_type in {"delete_event", "delete_midi_event"}:
                changed["events_deleted"] += music_project._delete_timeline_events(track, op)
            elif op_type in {
                "update_event",
                "modify_event",
                "update_midi_event",
                "modify_midi_event",
            }:
                event_ref = music_project._find_timeline_event(track, op)
                if event_ref is None:
                    continue
                clip = event_ref["clip"]
                event = event_ref["event"]
                payload = music_project._event_payload_from_op(op, include_identity=False)
                if "new_id" in op:
                    payload["id"] = op["new_id"]
                if music_project._payload_has_start(payload):
                    payload = music_project._event_payload_to_clip_local(payload, clip)
                updated_event = music_project._normalize_midi_event({**event, **payload})
                event.clear()
                event.update(updated_event)
                changed["events_updated"] += 1
            elif op_type in {
                "draw_event_curve",
                "set_event_curve",
                "replace_event_curve",
                "draw_controller_curve",
                "set_controller_curve",
                "cc_curve",
                "pitch_bend_curve",
                "aftertouch_curve",
                "channel_pressure_curve",
            }:
                clip = music_project._target_clip_for_timeline_write(track, op, create=True)
                added, deleted = music_project._apply_midi_event_curve(
                    clip,
                    music_project._curve_op_to_clip_local(op, clip),
                    op_type,
                )
                changed["events_added"] += added
                changed["events_deleted"] += deleted
                changed["curves_written"] += 1
            elif op_type in {"velocity_curve", "draw_velocity_curve", "set_velocity_curve"}:
                clip = music_project._target_clip_for_timeline_write(track, op, create=True)
                changed["updated"] += music_project._apply_velocity_curve(
                    clip,
                    music_project._curve_op_to_clip_local(op, clip),
                )
                changed["curves_written"] += 1
            else:
                raise ValueError(f"unsupported MIDI diff operation: {op_type}")

        for clip in music_project._track_midi_clips(track):
            clip["notes"].sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
            clip["events"].sort(key=music_project._midi_event_sort_key)
            music_project._update_midi_clip_duration(clip)
        return project

    project = project_repository.update_project(mutate)
    synced_track = track_lookup.find_track(project, state["track_id"])
    summary = {
        "track_id": state["track_id"],
        "requested_track_id": track_id,
        "host_track_id": state["host_track_id"],
        "operations": len(operations),
        **changed,
        "track_note_count": len(synced_track["notes"]),
        "track_midi_event_count": len(synced_track["midi_events"]),
    }
    return project, summary


def midi_batch_edit(
    operations: list[dict[str, Any]],
    *,
    track_id: int | None = None,
    selection: dict[str, Any] | None = None,
    all_tracks: bool = False,
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply AI-friendly batch edits to notes and controller lanes.

    This is intentionally higher-level than midi_diff: operations can describe
    musical intent such as velocity shapes, accents, humanization, and CC swells.
    """
    music_project = _project_model()
    changed: dict[str, Any] = {
        "operations": len(operations),
        "notes_updated": 0,
        "events_added": 0,
        "events_deleted": 0,
        "curves_written": 0,
        "dry_run": bool(dry_run),
        "details": [],
    }
    state: dict[str, Any] = {}

    def mutate(project: dict[str, Any]) -> dict[str, Any]:
        music_project._validate_midi_batch_write_scope(
            selection,
            track_id=track_id,
            all_tracks=all_tracks,
        )
        base_selection = music_project._normalize_selection(project, selection, track_id=track_id)
        if all_tracks or bool((selection or {}).get("all_tracks")):
            base_selection["all_tracks"] = True
        if not base_selection.get("all_tracks") and not base_selection.get("track_ids"):
            raise ValueError("midi_batch_edit write scope did not match any project tracks")
        state["base_selection"] = base_selection

        for raw_op in operations:
            if not isinstance(raw_op, dict):
                continue
            op = dict(raw_op)
            op_type = str(op.get("op") or op.get("type") or "").strip().lower()
            op_selection = music_project._normalize_selection(
                project,
                op.get("selection"),
                base=base_selection,
                op=op,
            )

            if op_type in {
                "velocity_set",
                "velocity_scale",
                "velocity_humanize",
                "velocity_accent",
                "velocity_shape",
                "velocity_ramp",
                "velocity_curve",
            }:
                updated = music_project._apply_batch_velocity_operation(
                    project,
                    op_selection,
                    op,
                    op_type,
                )
                changed["notes_updated"] += updated
                changed["details"].append({"op": op_type, "notes_updated": updated})
            elif op_type in {
                "cc_curve",
                "controller_curve",
                "draw_controller_curve",
                "expression_curve",
                "modulation_curve",
                "pitch_bend_curve",
                "aftertouch_curve",
                "channel_pressure_curve",
            }:
                added, deleted = music_project._apply_batch_event_curve_operation(
                    project,
                    op_selection,
                    op,
                    op_type,
                )
                changed["events_added"] += added
                changed["events_deleted"] += deleted
                changed["curves_written"] += 1
                changed["details"].append(
                    {
                        "op": op_type,
                        "events_added": added,
                        "events_deleted": deleted,
                    }
                )
            elif op_type in {"cc_clear", "controller_clear", "event_clear"}:
                deleted = music_project._apply_batch_event_clear(project, op_selection, op)
                changed["events_deleted"] += deleted
                changed["details"].append({"op": op_type, "events_deleted": deleted})
            else:
                raise ValueError(f"unsupported MIDI batch operation: {op_type}")

        for track in project.get("tracks", []):
            if not isinstance(track, dict):
                continue
            for clip in track.get("clips", []):
                if not isinstance(clip, dict) or clip.get("type") != "midi":
                    continue
                clip["notes"].sort(
                    key=lambda note: (note["start"], note["pitch"], note["duration"])
                )
                clip["events"].sort(key=music_project._midi_event_sort_key)
                music_project._update_midi_clip_duration(clip)
        return project

    if dry_run:
        project = project_repository.load_project()
        mutate(project)
        project = music_project.normalize_project(project)
    else:
        project = project_repository.update_project(mutate)
    summary = {
        **changed,
        "selection": music_project._selection_summary(state["base_selection"]),
        "project": music_project.project_summary(project),
    }
    return project, summary


def midi_query(
    *,
    track_id: int | None = None,
    selection: dict[str, Any] | None = None,
    include: list[str] | None = None,
) -> dict[str, Any]:
    """Return a compact project/selection summary for planning MIDI edits."""
    music_project = _project_model()
    project = project_repository.load_project()
    normalized_selection = music_project._normalize_selection(project, selection, track_id=track_id)
    include_set = {str(item).lower() for item in (include or [])}
    if not include_set:
        include_set = {"tracks", "clips", "notes", "velocity", "events", "controllers"}

    notes = music_project._selected_note_refs(project, normalized_selection)
    events = music_project._selected_event_refs(project, normalized_selection)
    tracks = music_project._selected_tracks(project, normalized_selection)
    response: dict[str, Any] = {
        "project": music_project.project_summary(project),
        "selection": music_project._selection_summary(normalized_selection),
        "selected": {
            "track_count": len(tracks),
            "note_count": len(notes),
            "midi_event_count": len(events),
        },
    }

    if "tracks" in include_set:
        response["tracks"] = [music_project._track_query_summary(track) for track in tracks]
    if "clips" in include_set:
        response["clips"] = [
            music_project._clip_query_summary(track, clip)
            for track, clip in music_project._selected_midi_clips(project, normalized_selection)
        ]
    if "notes" in include_set or "velocity" in include_set:
        response["notes"] = {
            "count": len(notes),
            "pitch": music_project._numeric_stats([ref["note"]["pitch"] for ref in notes]),
            "duration": music_project._numeric_stats([ref["note"]["duration"] for ref in notes]),
            "velocity": music_project._numeric_stats([ref["note"]["velocity"] for ref in notes]),
            "beat_range": music_project._beat_stats([ref["absolute_start"] for ref in notes]),
        }
    if "events" in include_set or "controllers" in include_set:
        response["events"] = {
            "count": len(events),
            "beat_range": music_project._beat_stats([ref["absolute_start"] for ref in events]),
            "lanes": music_project._event_lane_summaries(events),
        }
    return response


def midi_inspect(
    *,
    track_id: int | None = None,
    selection: dict[str, Any] | None = None,
    include: list[str] | None = None,
    limit: int = 120,
    offset: int = 0,
) -> dict[str, Any]:
    """Return detailed selected MIDI notes/events with bounded pagination."""
    music_project = _project_model()
    project = project_repository.load_project()
    normalized_selection = music_project._normalize_selection(project, selection, track_id=track_id)
    include_set = {str(item).lower() for item in (include or ["notes", "events"])}
    safe_limit = music_project._bounded_int(limit, 120, 1, 500)
    safe_offset = max(0, int(offset or 0))

    rows: list[dict[str, Any]] = []
    if "notes" in include_set:
        rows.extend(
            music_project._note_detail(ref)
            for ref in music_project._selected_note_refs(project, normalized_selection)
        )
    if "events" in include_set or "midi_events" in include_set:
        rows.extend(
            music_project._event_detail(ref)
            for ref in music_project._selected_event_refs(project, normalized_selection)
        )
    rows.sort(
        key=lambda row: (float(row.get("start", 0.0)), row.get("kind", ""), row.get("id", ""))
    )

    return {
        "selection": music_project._selection_summary(normalized_selection),
        "pagination": {
            "offset": safe_offset,
            "limit": safe_limit,
            "total": len(rows),
            "returned": len(rows[safe_offset : safe_offset + safe_limit]),
        },
        "items": rows[safe_offset : safe_offset + safe_limit],
    }
