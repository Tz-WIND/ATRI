"""Track-level mutations for Music Studio projects."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from core.music import project_repository, track_lookup

find_track = track_lookup.find_track


def _project_model():
    from core.music import project_model

    return project_model


def create_track(
    name: str = "Instrument",
    *,
    color: str | None = None,
    track_type: str = "instrument",
    channel_type: str = "multichannel",
) -> tuple[dict[str, Any], dict[str, Any]]:
    music_project = _project_model()
    state: dict[str, Any] = {}

    def mutate(project: dict[str, Any]) -> dict[str, Any]:
        existing = [int(track["id"]) for track in project["tracks"]]
        track_id = max(existing, default=0) + 1
        normalized_type = music_project._normalize_track_type({"type": track_type}, clips=[])
        normalized_channel_type = music_project._normalize_track_channel_type(
            channel_type,
            track_type=normalized_type,
        )
        track: dict[str, Any] = {
            "id": track_id,
            "host_track_id": None,
            "type": normalized_type,
            "channel_type": normalized_channel_type,
            "name": name.strip() or f"Track {track_id}",
            "color": music_project._track_color(color, track_id - 1),
            "volume": 0.8,
            "pan": 0.0,
            "mute": False,
            "solo": False,
            "instrument": "Bus"
            if normalized_type == "bus"
            else "Audio Track"
            if normalized_type == "audio"
            else "ATRI Basic Synth",
            "plugin_slots": music_project._normalize_plugin_slots(
                {
                    "plugin_slots": [] if normalized_type == "bus" else None,
                    "instrument": "ATRI Basic Synth",
                },
                track_type=normalized_type,
            ),
            "output_bus_id": None,
            "sends": [],
            "clips": [],
            "notes": [],
            "midi_events": [],
        }
        project["tracks"].append(track)
        state["track_id"] = track_id
        return project

    project = project_repository.update_project(mutate)
    return project, find_track(project, state["track_id"])


def import_audio_clip(
    path: str | Path,
    *,
    name: str = "",
    start: float = 0.0,
    duration_seconds: float = 0.0,
    waveform: list[Any] | None = None,
    source: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create a new audio track containing a single imported audio clip."""
    music_project = _project_model()
    state: dict[str, Any] = {}

    def mutate(project: dict[str, Any]) -> dict[str, Any]:
        existing = [int(track["id"]) for track in project["tracks"]]
        track_id = max(existing, default=0) + 1
        track_color = music_project._track_color(None, track_id - 1)
        source_path = Path(path)
        clip_name = name.strip() or source_path.stem or "Audio Clip"
        tempo = music_project._positive_float(project.get("tempo"), 120.0)
        seconds = music_project._non_negative_float(duration_seconds, 0.0)
        duration_beats = max(0.25, seconds * tempo / 60.0) if seconds > 0 else 4.0
        clip: dict[str, Any] = {
            "id": f"clip_{uuid4().hex[:10]}",
            "type": "audio",
            "name": clip_name,
            "start": music_project._non_negative_float(start, 0.0),
            "duration": duration_beats,
            "duration_seconds": seconds,
            "color": track_color,
            "source": Path(source).as_posix() if source is not None else source_path.as_posix(),
            "path": source_path.as_posix(),
            "source_offset": 0.0,
            "gain": 1.0,
            "waveform": music_project._normalize_waveform(waveform),
            "notes": [],
            "events": [],
        }
        track: dict[str, Any] = {
            "id": track_id,
            "host_track_id": None,
            "type": "audio",
            "channel_type": "multichannel",
            "name": clip_name,
            "color": track_color,
            "volume": 0.8,
            "pan": 0.0,
            "mute": False,
            "solo": False,
            "instrument": "Audio Track",
            "plugin_slots": [],
            "clips": [clip],
            "notes": [],
            "midi_events": [],
        }
        project["tracks"].append(track)
        state["track_id"] = track_id
        state["clip_id"] = clip["id"]
        return project

    project = project_repository.update_project(mutate)
    synced_track = find_track(project, state["track_id"])
    clip_id = state["clip_id"]
    synced_clip = next(item for item in synced_track.get("clips", []) if item.get("id") == clip_id)
    return project, synced_track, synced_clip


def delete_track(track_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove a track from the project while keeping at least one track."""
    state: dict[str, Any] = {}

    def mutate(project: dict[str, Any]) -> dict[str, Any]:
        track = find_track(project, track_id)
        if len(project["tracks"]) <= 1:
            raise ValueError("cannot delete the last track")

        deleted_id = int(track["id"])
        state["track"] = track
        project["tracks"] = [
            item for item in project["tracks"] if int(item.get("id", -1)) != deleted_id
        ]
        for item in project["tracks"]:
            if item.get("output_bus_id") == deleted_id:
                item["output_bus_id"] = None
            item["sends"] = [
                send
                for send in item.get("sends", [])
                if isinstance(send, dict) and send.get("target_bus_id") != deleted_id
            ]
        return project

    project = project_repository.update_project(mutate)
    return project, state["track"]


def update_track(track_id: int, updates: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    music_project = _project_model()

    def mutate(project: dict[str, Any]) -> dict[str, Any]:
        track = find_track(project, track_id)
        if "name" in updates:
            track["name"] = str(updates["name"]).strip() or track["name"]
        if "color" in updates:
            track["color"] = music_project._track_color(updates["color"], track_id - 1)
        if "volume" in updates:
            track["volume"] = music_project._bounded_float(
                updates["volume"], track["volume"], 0.0, 2.0
            )
        if "pan" in updates:
            track["pan"] = music_project._bounded_float(updates["pan"], track["pan"], -1.0, 1.0)
        if "mute" in updates:
            track["mute"] = bool(updates["mute"])
        if "solo" in updates:
            track["solo"] = bool(updates["solo"])
        if "output_bus_id" in updates:
            track["output_bus_id"] = music_project._nullable_non_negative_int(
                updates.get("output_bus_id")
            )
        if "sends" in updates and isinstance(updates["sends"], list):
            track["sends"] = music_project._normalize_track_sends({"sends": updates["sends"]})
        if "type" in updates or "track_type" in updates:
            track["type"] = music_project._normalize_track_type(
                {"type": updates.get("type", updates.get("track_type"))},
                clips=track.get("clips", []),
            )
            if track["type"] == "audio":
                track["instrument"] = "Audio Track"
                track["plugin_slots"] = []
            elif track["type"] == "bus":
                track["instrument"] = "Bus"
                track["plugin_slots"] = music_project._normalize_plugin_slots(
                    track,
                    track_type="bus",
                )
            else:
                track["plugin_slots"] = music_project._normalize_plugin_slots(
                    track,
                    track_type="instrument",
                )
        if "channel_type" in updates:
            track["channel_type"] = music_project._normalize_track_channel_type(
                updates["channel_type"],
                track_type=str(track.get("type") or "instrument"),
            )
        if "instrument" in updates:
            track["instrument"] = str(updates["instrument"] or "ATRI Basic Synth")
        if "clips" in updates and isinstance(updates["clips"], list):
            track["clips"] = updates["clips"]
        if "plugin_slots" in updates and isinstance(updates["plugin_slots"], list):
            track["plugin_slots"] = music_project._normalize_plugin_slots(
                {"plugin_slots": updates["plugin_slots"]},
                track_type=str(track.get("type") or "instrument"),
            )
        return project

    project = project_repository.update_project(mutate)
    return project, find_track(project, track_id)


def set_track_plugin(
    track_id: int,
    plugin: dict[str, Any] | None,
    *,
    slot_id: str = "instrument",
) -> tuple[dict[str, Any], dict[str, Any]]:
    music_project = _project_model()
    slot_id = str(slot_id or "instrument").strip() or "instrument"

    def mutate(project: dict[str, Any]) -> dict[str, Any]:
        track = find_track(project, track_id)
        track_type = str(track.get("type") or "instrument")
        if track_type == "bus" and slot_id == "instrument":
            raise ValueError("bus tracks only support insert plugins")
        if track_type not in {"instrument", "bus"}:
            raise ValueError("only instrument and bus tracks support plugin inserts")
        slot = music_project._normalize_plugin_slot(plugin, slot_id=slot_id)
        slots = [s for s in track.get("plugin_slots", []) if s.get("id") != slot["id"]]
        track["plugin_slots"] = music_project._sort_plugin_slots([slot, *slots])
        if slot["id"] == "instrument":
            track["instrument"] = slot["name"]
        return project

    project = project_repository.update_project(mutate)
    return project, find_track(project, track_id)
