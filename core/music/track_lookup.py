"""Track lookup helpers shared by project model and track operations."""

from __future__ import annotations

from typing import Any, cast


def find_track(project: dict[str, Any], track_id: int) -> dict[str, Any]:
    requested_id = int(track_id)
    raw_tracks = project.get("tracks", [])
    tracks = raw_tracks if isinstance(raw_tracks, list) else []
    for raw_track in tracks:
        if not isinstance(raw_track, dict):
            continue
        track = cast(dict[str, Any], raw_track)
        if int(track.get("id", -1)) == requested_id:
            return track
    for raw_track in tracks:
        if not isinstance(raw_track, dict):
            continue
        track = cast(dict[str, Any], raw_track)
        host_track_id = track.get("host_track_id")
        if host_track_id is not None and int(host_track_id) == requested_id:
            return track
    raise ValueError(f"track {track_id} not found")
