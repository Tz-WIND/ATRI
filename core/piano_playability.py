"""Piano playability analysis for ATRI MIDI projects."""

from __future__ import annotations

from typing import Any

from core.music_project import load_project

HAND_SPAN_WARNING_SEMITONES = 15
HAND_SPAN_ERROR_SEMITONES = 19
HAND_DENSITY_WARNING_NOTES = 5
HAND_DENSITY_ERROR_NOTES = 6
MIDDLE_C = 60
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}
RAPID_REPOSITION_WINDOW_BEATS = 0.5
RAPID_REPOSITION_SEMITONES = 12
LOW_EXTREME_MAX_PITCH = 36
HIGH_EXTREME_MIN_PITCH = 96
EXTREME_REGISTER_DENSE_NOTES = 3


def piano_playability_check(
    *,
    track_id: int | None = None,
    selection: dict[str, Any] | None = None,
    strictness: str = "standard",
) -> dict[str, Any]:
    project = load_project()
    notes = _selected_notes(project, track_id=track_id, selection=selection)
    events = _selected_events(project, track_id=track_id, selection=selection)
    issues: list[dict[str, Any]] = []
    difficulty_notes: list[dict[str, Any]] = []
    suggestions = _sustain_pedal_suggestions(notes, events)

    clusters = _note_start_clusters(notes)
    for cluster in clusters:
        hands = _assign_cluster_hands(cluster)
        issues.extend(_extreme_register_density_issues(cluster))
        for hand, hand_notes in hands.items():
            if len(hand_notes) >= HAND_DENSITY_WARNING_NOTES:
                severity = "error" if len(hand_notes) >= HAND_DENSITY_ERROR_NOTES else "warning"
                issues.append(
                    {
                        "severity": severity,
                        "code": "hand_density",
                        "start": cluster[0]["start"],
                        "end": max(note["end"] for note in hand_notes),
                        "hand": hand,
                        "note_count": len(hand_notes),
                        "notes": [str(note["id"]) for note in hand_notes],
                        "message": _hand_density_message(hand, len(hand_notes)),
                        "suggestion": (
                            "Remove a note, roll the chord, or redistribute notes between hands."
                        ),
                    }
                )
            if len(hand_notes) < 2:
                continue
            pitches = [int(note["pitch"]) for note in hand_notes]
            span = max(pitches) - min(pitches)
            if span < HAND_SPAN_WARNING_SEMITONES:
                continue
            severity = "error" if span > HAND_SPAN_ERROR_SEMITONES else "warning"
            issues.append(
                {
                    "severity": severity,
                    "code": "hand_span",
                    "start": cluster[0]["start"],
                    "end": max(note["end"] for note in hand_notes),
                    "hand": hand,
                    "span_semitones": span,
                    "notes": [str(note["id"]) for note in hand_notes],
                    "message": _hand_span_message(hand, span),
                    "suggestion": (
                        "Keep if intended as advanced writing, or redistribute one note "
                        "to the other hand."
                    )
                    if severity == "warning"
                    else "Split, revoice, or redistribute this sonority between hands.",
                }
            )
    issues.extend(_left_over_right_crossing_issues(notes, clusters))
    difficulty_notes.extend(_rapid_reposition_notes(clusters))
    difficulty_notes.extend(_allowed_left_over_right_notes(notes, clusters))

    max_problem_severity = _max_problem_severity(issues)
    return {
        "summary": {
            "track_id": track_id,
            "selection": selection or {},
            "strictness": strictness,
            "issue_count": len(issues),
            "difficulty_note_count": len(difficulty_notes),
            "suggestion_count": len(suggestions),
            "max_problem_severity": max_problem_severity,
            "playability": _playability_label(max_problem_severity),
        },
        "issues": issues,
        "difficulty_notes": difficulty_notes,
        "suggestions": suggestions,
    }


def _selected_notes(
    project: dict[str, Any],
    *,
    track_id: int | None,
    selection: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    selected_range = _selection_range(selection)
    rows: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        if track_id is not None and int(track.get("id", -1)) != int(track_id):
            continue
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or clip.get("type") != "midi":
                continue
            clip_start = float(clip.get("start", 0.0) or 0.0)
            for note in clip.get("notes", []):
                if not isinstance(note, dict):
                    continue
                start = clip_start + float(note.get("start", 0.0) or 0.0)
                if selected_range and not (
                    selected_range[0] - 1e-6 <= start <= selected_range[1] + 1e-6
                ):
                    continue
                duration = float(note.get("duration", 0.0) or 0.0)
                rows.append(
                    {
                        "id": str(note.get("id") or ""),
                        "pitch": int(note.get("pitch", 60) or 60),
                        "start": round(start, 6),
                        "end": round(start + duration, 6),
                        "duration": duration,
                    }
                )
    return sorted(rows, key=lambda item: (item["start"], item["pitch"], item["id"]))


def _selected_events(
    project: dict[str, Any],
    *,
    track_id: int | None,
    selection: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    selected_range = _selection_range(selection)
    rows: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        if track_id is not None and int(track.get("id", -1)) != int(track_id):
            continue
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or clip.get("type") != "midi":
                continue
            clip_start = float(clip.get("start", 0.0) or 0.0)
            for event in clip.get("events", []):
                if not isinstance(event, dict):
                    continue
                start = clip_start + float(event.get("start", 0.0) or 0.0)
                if selected_range and not (
                    selected_range[0] - 1e-6 <= start <= selected_range[1] + 1e-6
                ):
                    continue
                rows.append(
                    {
                        "id": str(event.get("id") or ""),
                        "type": str(event.get("type") or ""),
                        "controller": int(event.get("controller", -1) or -1),
                        "value": int(event.get("value", event.get("pressure", 0)) or 0),
                        "start": round(start, 6),
                    }
                )
    return sorted(rows, key=lambda item: (item["start"], item["type"], item["id"]))


def _selection_range(selection: dict[str, Any] | None) -> tuple[float, float] | None:
    if not isinstance(selection, dict) or "range" not in selection:
        return None
    raw_range = selection.get("range")
    if not isinstance(raw_range, list | tuple) or len(raw_range) < 2:
        return None
    start = max(0.0, float(raw_range[0] or 0.0))
    end = max(start, float(raw_range[1] or start))
    return start, end


def _note_start_clusters(notes: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    for note in notes:
        if not clusters or abs(clusters[-1][0]["start"] - note["start"]) > 1e-6:
            clusters.append([note])
        else:
            clusters[-1].append(note)
    return clusters


def _assign_cluster_hands(cluster: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not cluster:
        return {"left": [], "right": []}
    if max(int(note["pitch"]) for note in cluster) <= MIDDLE_C:
        return {"left": cluster, "right": []}
    if min(int(note["pitch"]) for note in cluster) >= MIDDLE_C:
        return {"left": [], "right": cluster}
    return {
        "left": [note for note in cluster if int(note["pitch"]) < MIDDLE_C],
        "right": [note for note in cluster if int(note["pitch"]) >= MIDDLE_C],
    }


def _hand_span_message(hand: str, span: int) -> str:
    hand_label = "Left hand" if hand == "left" else "Right hand"
    if span > HAND_SPAN_ERROR_SEMITONES:
        return f"{hand_label} span is wider than a 12th."
    return f"{hand_label} span is between a 10th and a 12th."


def _hand_density_message(hand: str, note_count: int) -> str:
    hand_label = "Left hand" if hand == "left" else "Right hand"
    return f"{hand_label} has {note_count} simultaneous notes."


def _extreme_register_density_issues(cluster: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    register_groups = {
        "low": [note for note in cluster if int(note["pitch"]) <= LOW_EXTREME_MAX_PITCH],
        "high": [note for note in cluster if int(note["pitch"]) >= HIGH_EXTREME_MIN_PITCH],
    }
    for register, register_notes in register_groups.items():
        if len(register_notes) < EXTREME_REGISTER_DENSE_NOTES:
            continue
        issues.append(
            {
                "severity": "warning",
                "code": "extreme_register_density",
                "start": cluster[0]["start"],
                "end": max(note["end"] for note in register_notes),
                "register": register,
                "note_count": len(register_notes),
                "notes": [str(note["id"]) for note in register_notes],
                "message": (
                    "Dense low-register piano writing can become muddy."
                    if register == "low"
                    else "Dense high-register piano writing can become brittle."
                ),
                "suggestion": (
                    "Thin the voicing, spread attacks, or move some notes upward."
                    if register == "low"
                    else "Thin the voicing, spread attacks, or move some notes downward."
                ),
            }
        )
    return issues


def _rapid_reposition_notes(clusters: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    previous_by_hand: dict[str, dict[str, Any]] = {}
    notes: list[dict[str, Any]] = []
    for cluster in clusters:
        for hand, hand_notes in _assign_cluster_hands(cluster).items():
            if not hand_notes:
                continue
            current = {
                "start": cluster[0]["start"],
                "pitch": _hand_position_pitch(hand_notes),
            }
            previous = previous_by_hand.get(hand)
            if previous is not None:
                gap = float(current["start"]) - float(previous["start"])
                interval = abs(int(current["pitch"]) - int(previous["pitch"]))
                if (
                    0 < gap <= RAPID_REPOSITION_WINDOW_BEATS + 1e-6
                    and interval >= RAPID_REPOSITION_SEMITONES
                ):
                    notes.append(
                        {
                            "severity": "info",
                            "code": "rapid_reposition",
                            "start": current["start"],
                            "hand": hand,
                            "from_pitch": previous["pitch"],
                            "to_pitch": current["pitch"],
                            "interval_semitones": interval,
                            "message": (
                                "Large left-hand reposition in a short time window."
                                if hand == "left"
                                else "Large right-hand reposition in a short time window."
                            ),
                        }
                    )
            previous_by_hand[hand] = current
    return notes


def _allowed_left_over_right_notes(
    notes: list[dict[str, Any]],
    clusters: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    allowed: list[dict[str, Any]] = []
    for cluster in clusters:
        if len(cluster) != 1:
            continue
        crossing_note = cluster[0]
        start = float(crossing_note["start"])
        pitch = int(crossing_note["pitch"])
        if pitch < MIDDLE_C:
            continue

        active_right_notes = [
            note
            for note in notes
            if float(note["start"]) < start < float(note["end"]) and int(note["pitch"]) >= MIDDLE_C
        ]
        if len(active_right_notes) < 2:
            continue
        if pitch <= max(int(note["pitch"]) for note in active_right_notes):
            continue

        allowed.append(
            {
                "severity": "info",
                "code": "left_hand_over_right_allowed",
                "start": start,
                "hand": "left",
                "message": "Left hand crosses above a blocked right-hand position.",
                "notes": [str(crossing_note["id"])],
            }
        )
    return allowed


def _left_over_right_crossing_issues(
    notes: list[dict[str, Any]],
    clusters: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for cluster in clusters:
        if len(cluster) != 1:
            continue
        crossing_note = cluster[0]
        start = float(crossing_note["start"])
        pitch = int(crossing_note["pitch"])
        if pitch < MIDDLE_C:
            continue

        active_right_notes = _active_right_notes(notes, start)
        if not active_right_notes:
            continue
        if pitch <= max(int(note["pitch"]) for note in active_right_notes):
            continue
        if _right_hand_blocked(active_right_notes):
            continue

        issues.append(
            {
                "severity": "warning",
                "code": "hand_crossing",
                "start": start,
                "end": crossing_note["end"],
                "hand": "left",
                "notes": [str(crossing_note["id"])],
                "message": "Left hand crosses above the right hand while the right hand can move.",
                "suggestion": "Assign the crossing note to the right hand or adjust the voicing.",
            }
        )
    return issues


def _active_right_notes(notes: list[dict[str, Any]], beat: float) -> list[dict[str, Any]]:
    return [
        note
        for note in notes
        if float(note["start"]) < beat < float(note["end"]) and int(note["pitch"]) >= MIDDLE_C
    ]


def _right_hand_blocked(active_right_notes: list[dict[str, Any]]) -> bool:
    return len(active_right_notes) >= 2


def _hand_position_pitch(notes: list[dict[str, Any]]) -> int:
    pitches = sorted(int(note["pitch"]) for note in notes)
    return pitches[len(pitches) // 2]


def _sustain_pedal_suggestions(
    notes: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    phrase = _connected_arpeggio_phrase(notes)
    if phrase is None:
        return []
    start, end = phrase
    if _has_sustain_pedal(events, start, end):
        return []
    return [
        {
            "code": "sustain_pedal_suggested",
            "start": start,
            "end": end,
            "message": "Connected arpeggio texture may benefit from CC64 sustain pedal.",
            "suggestion": "Add CC64 pedal down near the start and release after the phrase.",
        }
    ]


def _connected_arpeggio_phrase(notes: list[dict[str, Any]]) -> tuple[float, float] | None:
    ordered = sorted(notes, key=lambda note: (note["start"], note["pitch"]))
    run: list[dict[str, Any]] = []
    previous_start: float | None = None
    for note in ordered:
        start = float(note["start"])
        if previous_start is None or 0 < start - previous_start <= 0.75 + 1e-6:
            run.append(note)
        else:
            run = [note]
        previous_start = start
        if len(run) >= 4 and len({float(item["start"]) for item in run}) >= 4:
            return float(run[0]["start"]), max(float(item["end"]) for item in run)
    return None


def _has_sustain_pedal(events: list[dict[str, Any]], start: float, end: float) -> bool:
    return any(
        str(event.get("type")) == "control_change"
        and int(event.get("controller", -1)) == 64
        and int(event.get("value", 0)) >= 64
        and start - 0.25 <= float(event.get("start", 0.0)) <= end + 0.25
        for event in events
    )


def _max_problem_severity(issues: list[dict[str, Any]]) -> str | None:
    problem_severities = [
        str(issue["severity"])
        for issue in issues
        if str(issue.get("severity")) in {"warning", "error"}
    ]
    if not problem_severities:
        return None
    return max(problem_severities, key=lambda severity: SEVERITY_RANK[severity])


def _playability_label(max_problem_severity: str | None) -> str:
    if max_problem_severity == "error":
        return "likely_unplayable"
    if max_problem_severity == "warning":
        return "playable_with_warnings"
    return "playable"
