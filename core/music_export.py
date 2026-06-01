"""Export helpers for Music Studio projects."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from xml.etree import ElementTree

MIDI_TICKS_PER_BEAT = 480
MIDI_SCHEMA_VERSION = 1


def write_project_midi(
    project: dict[str, Any],
    path: Path | str,
    *,
    track_ids: list[int] | None = None,
    beat_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Write a Standard MIDI File for the selected project MIDI tracks."""
    export_tracks = _selected_midi_tracks(project, track_ids)
    normalized_range = _normalize_beat_range(beat_range)
    if normalized_range is not None:
        export_tracks = [
            ranged_track
            for track in export_tracks
            if (ranged_track := _midi_track_in_beat_range(track, normalized_range)) is not None
        ]
    if not export_tracks:
        raise ValueError("no MIDI content found")

    chunks = [_track_chunk(_conductor_events(project))]
    note_count = 0
    event_count = 0
    for track in export_tracks:
        messages, notes, events = _track_messages(track)
        chunks.append(_track_chunk(messages))
        note_count += notes
        event_count += events

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = b"MThd" + (6).to_bytes(4, "big")
    header += (1).to_bytes(2, "big")
    header += len(chunks).to_bytes(2, "big")
    header += MIDI_TICKS_PER_BEAT.to_bytes(2, "big")
    output_path.write_bytes(header + b"".join(chunks))

    summary = {
        "path": str(output_path),
        "filename": output_path.name,
        "format": "midi",
        "ticks_per_beat": MIDI_TICKS_PER_BEAT,
        "track_count": len(export_tracks),
        "track_ids": [track["id"] for track in export_tracks],
        "tracks": [
            {"project_track_id": track["id"], "name": track["name"]} for track in export_tracks
        ],
        "note_count": note_count,
        "event_count": event_count,
    }
    if normalized_range is not None:
        summary["beat_range"] = [normalized_range[0], normalized_range[1]]
    return summary


def write_dawproject_archive(
    project: dict[str, Any],
    path: Path | str,
    *,
    export_id: str,
    consumer: str = "export",
    track_ids: list[int] | None = None,
    workspace_root: Path | str = ".",
) -> dict[str, Any]:
    """Write a DAWproject archive with ATRI's best-effort plug-in state payloads."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    export_tracks = _selected_project_tracks(project, track_ids)
    audio_files = _collect_audio_files(export_tracks, workspace_root=workspace_root)
    plugin_states, plugin_warnings = _collect_plugin_states(export_tracks)
    project_xml = _dawproject_project_xml(project, export_tracks, audio_files, plugin_states)
    metadata_xml = _dawproject_metadata_xml(project)
    files = [{"role": "dawproject", **_archive_file_entry(output_path)}]
    files.extend(_asset_manifest_files(audio_files, role="audio"))
    files.extend(_asset_manifest_files(plugin_states, role="plugin_state"))

    export: dict[str, Any] = {
        "id": export_id,
        "mode": "project",
        "target": "selected_tracks" if track_ids else "entire_project",
        "format": "dawproject",
        "path": str(output_path),
        "filename": output_path.name,
        "download_url": "",
        "track_ids": [int(track.get("id", 0)) for track in export_tracks],
        "tracks": _archive_track_manifest(export_tracks),
        "files": files,
        "plugin_states": plugin_states,
        "warnings": plugin_warnings,
        "plugin_state_count": len(plugin_states),
    }
    manifest = build_export_manifest(project, export, consumer=consumer)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.xml", project_xml)
        archive.writestr("metadata.xml", metadata_xml)
        archive.writestr(
            "atri-export-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )
        for audio_file in audio_files:
            archive.write(audio_file["source_path"], arcname=audio_file["archive_path"])
        for plugin_state in plugin_states:
            archive.writestr(plugin_state["archive_path"], plugin_state["state_bytes"])

    export["manifest"] = manifest
    return _strip_binary_state(export)


def read_dawproject_archive(path: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read MIDI tracks from a DAWproject archive into ATRI's project schema."""
    from core.music_project import normalize_project

    archive_path = Path(path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            project_xml = archive.read(_dawproject_archive_member(archive, "project.xml"))
            metadata_xml = _dawproject_optional_member(archive, "metadata.xml")
            metadata_bytes = archive.read(metadata_xml) if metadata_xml else b""
    except KeyError as exc:
        raise ValueError("DAWproject archive is missing project.xml") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError("invalid DAWproject archive") from exc

    root = _dawproject_xml_root(project_xml, "project.xml")
    raw_project = _dawproject_raw_project(root, metadata_bytes, archive_path)
    project = normalize_project(raw_project)
    summary = _dawproject_import_summary(project, archive_path)
    return project, summary


def build_export_manifest(
    project: dict[str, Any],
    export: dict[str, Any],
    *,
    consumer: str = "export",
) -> dict[str, Any]:
    """Build the versioned export manifest consumed by future bridge clients."""
    format_name = str(export.get("format") or "").strip().lower()
    manifest: dict[str, Any] = {
        "schema_version": MIDI_SCHEMA_VERSION,
        "consumer": _manifest_consumer(consumer),
        "export_id": str(export.get("id") or ""),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "format": format_name,
        "project": {
            "title": str(project.get("title") or "ATRI Session"),
            "tempo": _positive_float(project.get("tempo"), 120.0),
            "time_signature": _project_meter(project),
            "length_beats": _positive_float(project.get("length_beats"), 0.0),
        },
        "files": _manifest_files(export),
        "tracks": _manifest_tracks(export),
        "plugin_states": _manifest_plugin_states(export),
        "warnings": _manifest_warnings(export),
        "capabilities": {
            "midi": format_name in {"midi", "dawproject"},
            "audio": format_name in {"wav", "flac", "mp3", "dawproject"},
            "stems": str(export.get("mode") or "").strip().lower() == "stems",
            "dawproject": format_name == "dawproject",
        },
    }
    export_range = _manifest_range(export)
    if export_range:
        manifest["range"] = export_range
    bridge = _manifest_bridge(export)
    if bridge:
        manifest["bridge"] = bridge
    selection = export.get("selection_summary")
    if isinstance(selection, dict) and selection:
        manifest["selection"] = _manifest_public_dict(selection)
    return manifest


def write_export_manifest(path: Path | str, manifest: dict[str, Any]) -> None:
    """Persist an export manifest as stable, readable JSON."""
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _selected_midi_tracks(
    project: dict[str, Any],
    track_ids: list[int] | None,
) -> list[dict[str, Any]]:
    wanted = {int(track_id) for track_id in track_ids} if track_ids else None
    tracks = []
    for raw_track in project.get("tracks", []):
        if not isinstance(raw_track, dict):
            continue
        track_id = _positive_int(raw_track.get("id"), 0)
        if wanted is not None and track_id not in wanted:
            continue
        if str(raw_track.get("type") or "").strip().lower() == "automation":
            continue
        track = _midi_track(raw_track, track_id)
        if track["notes"] or track["events"]:
            tracks.append(track)
    return tracks


def _selected_project_tracks(
    project: dict[str, Any],
    track_ids: list[int] | None,
) -> list[dict[str, Any]]:
    wanted = {int(track_id) for track_id in track_ids} if track_ids else None
    tracks = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        track_id = _positive_int(track.get("id"), 0)
        if wanted is not None and track_id not in wanted:
            continue
        if str(track.get("type") or "").strip().lower() == "automation":
            continue
        tracks.append(track)
    if not tracks:
        raise ValueError("no exportable tracks found")
    return tracks


def _normalize_beat_range(value: tuple[float, float] | None) -> tuple[float, float] | None:
    if value is None:
        return None
    start = max(0.0, _non_negative_float(value[0], 0.0))
    end = max(start, _non_negative_float(value[1], start))
    if end <= start:
        return None
    return start, end


def _midi_track_in_beat_range(
    track: dict[str, Any],
    beat_range: tuple[float, float],
) -> dict[str, Any] | None:
    start, end = beat_range
    notes = []
    for note in track["notes"]:
        note_start = _non_negative_float(note.get("start"), 0.0)
        note_end = note_start + _non_negative_float(note.get("duration"), 0.25)
        if note_start < end and note_end > start:
            clipped_start = max(note_start, start)
            clipped_end = min(note_end, end)
            notes.append(
                {
                    **note,
                    "start": max(0.0, note_start - start),
                    "duration": max(1.0 / MIDI_TICKS_PER_BEAT, clipped_end - clipped_start),
                }
            )

    events = []
    for event in track["events"]:
        event_start = _non_negative_float(event.get("start"), 0.0)
        if start <= event_start <= end:
            events.append({**event, "start": max(0.0, event_start - start)})

    if not notes and not events:
        return None
    return {**track, "notes": notes, "events": events}


def _midi_track(track: dict[str, Any], track_id: int) -> dict[str, Any]:
    return {
        "id": track_id,
        "name": str(track.get("name") or f"Track {track_id}"),
        "notes": _track_notes(track),
        "events": _track_events(track),
    }


def _track_notes(track: dict[str, Any]) -> list[dict[str, Any]]:
    notes = track.get("notes")
    if isinstance(notes, list) and notes:
        return [_normalize_note(note, 0.0) for note in notes if isinstance(note, dict)]

    clip_notes = []
    for clip in track.get("clips", []):
        if not isinstance(clip, dict) or str(clip.get("type") or "midi").lower() != "midi":
            continue
        clip_start = _non_negative_float(clip.get("start"), 0.0)
        for note in clip.get("notes", []):
            if isinstance(note, dict):
                clip_notes.append(_normalize_note(note, clip_start))
    return clip_notes


def _track_events(track: dict[str, Any]) -> list[dict[str, Any]]:
    events = track.get("midi_events")
    if isinstance(events, list) and events:
        return [_normalize_event(event, 0.0) for event in events if isinstance(event, dict)]

    clip_events = []
    for clip in track.get("clips", []):
        if not isinstance(clip, dict) or str(clip.get("type") or "midi").lower() != "midi":
            continue
        clip_start = _non_negative_float(clip.get("start"), 0.0)
        for event in clip.get("events", []):
            if isinstance(event, dict):
                clip_events.append(_normalize_event(event, clip_start))
    return clip_events


def _collect_audio_files(
    tracks: list[dict[str, Any]],
    *,
    workspace_root: Path | str,
) -> list[dict[str, Any]]:
    files = []
    used_names: set[str] = set()
    workspace_path = Path(workspace_root or ".").resolve()
    for track in tracks:
        for clip in track.get("clips", []):
            if not isinstance(clip, dict) or str(clip.get("type") or "").lower() != "audio":
                continue
            source_path = _resolve_workspace_asset_path(clip.get("path"), workspace_path)
            if source_path is None:
                continue
            archive_name = _unique_archive_name(
                "media/audio",
                source_path.name,
                used_names,
            )
            files.append(
                {
                    "archive_path": archive_name,
                    "source_path": source_path,
                    "filename": Path(archive_name).name,
                    "track_id": _positive_int(track.get("id"), 0),
                    "clip_id": str(clip.get("id") or ""),
                    "name": str(clip.get("name") or source_path.stem),
                }
            )
    return files


def _resolve_workspace_asset_path(raw_path: Any, workspace_path: Path) -> Path | None:
    raw = str(raw_path or "").strip()
    if not raw:
        return None
    try:
        source_path = Path(raw)
        if not source_path.is_absolute():
            source_path = workspace_path / source_path
        source_path = source_path.resolve()
        source_path.relative_to(workspace_path)
    except (OSError, RuntimeError, ValueError):
        return None
    if not source_path.exists() or not source_path.is_file():
        return None
    return source_path


def _collect_plugin_states(tracks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    states = []
    warnings = []
    used_names: set[str] = set()
    for track in tracks:
        track_id = _positive_int(track.get("id"), 0)
        for slot in track.get("plugin_slots", []):
            if not isinstance(slot, dict) or str(slot.get("type") or "") not in {"vst2", "vst3"}:
                continue
            state_b64 = str(slot.get("state_b64") or "")
            if not state_b64:
                warnings.append(f"plugin state missing: track_id={track_id} slot={slot.get('id')}")
                continue
            try:
                state_bytes = base64.b64decode(state_b64, validate=True)
            except (ValueError, binascii.Error):
                warnings.append(f"plugin state invalid: track_id={track_id} slot={slot.get('id')}")
                continue
            archive_path = _unique_archive_name(
                "plugins",
                f"track-{track_id}-{slot.get('id', 'slot')}-{slot.get('name', 'plugin')}.state",
                used_names,
            )
            states.append(_plugin_state_record(track, slot, archive_path, state_b64, state_bytes))
    return states, warnings


def _plugin_state_record(
    track: dict[str, Any],
    slot: dict[str, Any],
    archive_path: str,
    state_b64: str,
    state_bytes: bytes,
) -> dict[str, Any]:
    return {
        "track_id": _positive_int(track.get("id"), 0),
        "track_name": str(track.get("name") or ""),
        "slot_id": str(slot.get("id") or "slot"),
        "plugin_type": str(slot.get("type") or ""),
        "plugin_name": str(slot.get("name") or "Plugin"),
        "vendor": str(slot.get("vendor") or ""),
        "category": str(slot.get("category") or ""),
        "version": str(slot.get("version") or ""),
        "path": str(slot.get("path") or slot.get("dll_path") or ""),
        "archive_path": archive_path,
        "state_b64": state_b64,
        "state_bytes": state_bytes,
        "state_size": len(state_bytes),
        "state_sha256": hashlib.sha256(state_bytes).hexdigest(),
    }


def _dawproject_project_xml(
    project: dict[str, Any],
    tracks: list[dict[str, Any]],
    audio_files: list[dict[str, Any]],
    plugin_states: list[dict[str, Any]],
) -> bytes:
    root = ElementTree.Element(
        "Project",
        {"version": "1.0", "application": "ATRI"},
    )
    transport = ElementTree.SubElement(root, "Transport")
    ElementTree.SubElement(
        transport,
        "Tempo",
        {"value": str(_positive_float(project.get("tempo"), 120.0))},
    )
    meter = _project_meter(project)
    ElementTree.SubElement(
        transport,
        "TimeSignature",
        {"numerator": str(meter[0]), "denominator": str(meter[1])},
    )
    structure = ElementTree.SubElement(root, "Structure")
    arrangement = ElementTree.SubElement(root, "Arrangement")
    for track in tracks:
        _append_track_xml(structure, arrangement, track, audio_files, plugin_states)
    ElementTree.indent(root)
    return cast(bytes, ElementTree.tostring(root, encoding="utf-8", xml_declaration=True))


def _dawproject_metadata_xml(project: dict[str, Any]) -> bytes:
    root = ElementTree.Element("MetaData")
    ElementTree.SubElement(root, "Title").text = str(project.get("title") or "ATRI Session")
    ElementTree.SubElement(root, "Application", {"name": "ATRI"})
    ElementTree.indent(root)
    return cast(bytes, ElementTree.tostring(root, encoding="utf-8", xml_declaration=True))


def _dawproject_archive_member(archive: zipfile.ZipFile, filename: str) -> str:
    requested = filename.lower()
    for name in archive.namelist():
        if name.lower() == requested:
            return name
    raise KeyError(filename)


def _dawproject_optional_member(archive: zipfile.ZipFile, filename: str) -> str | None:
    try:
        return _dawproject_archive_member(archive, filename)
    except KeyError:
        return None


def _dawproject_raw_project(
    root: ElementTree.Element,
    metadata_xml: bytes,
    archive_path: Path,
) -> dict[str, Any]:
    tracks_by_ref = _dawproject_structure_tracks(root)
    ordered_refs = list(tracks_by_ref)

    for lane_index, lane in enumerate(_xml_iter(root, "Lane"), start=1):
        track_ref = _xml_attr(lane, "track", "trackId", "target", "id") or f"track_{lane_index}"
        if track_ref not in tracks_by_ref:
            tracks_by_ref[track_ref] = _dawproject_track_template(
                None,
                len(tracks_by_ref) + 1,
                _track_ref=track_ref,
            )
            ordered_refs.append(track_ref)
        tracks_by_ref[track_ref]["clips"].extend(_dawproject_midi_clips(lane, track_ref))

    title = _dawproject_metadata_title(metadata_xml) or _xml_attr(root, "name", "title")
    if not title:
        title = archive_path.stem or "Imported DAWproject"

    return {
        "title": title,
        "tempo": _dawproject_tempo(root),
        "time_signature": _dawproject_time_signature(root),
        "length_beats": _dawproject_length_beats(tracks_by_ref.values()),
        "tracks": [tracks_by_ref[track_ref] for track_ref in ordered_refs],
    }


def _dawproject_structure_tracks(root: ElementTree.Element) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, track_element in enumerate(_xml_iter(root, "Track"), start=1):
        track_ref = _xml_attr(track_element, "id", "track", "trackId") or f"track_{index}"
        if track_ref in result:
            continue
        result[track_ref] = _dawproject_track_template(
            track_element,
            index,
            _track_ref=track_ref,
        )
    return result


def _dawproject_track_template(
    track_element: ElementTree.Element | None,
    index: int,
    *,
    _track_ref: str,
) -> dict[str, Any]:
    track_type = _dawproject_track_type(_xml_attr(track_element, "type", "role"))
    channel = _xml_first_child(track_element, "Channel") if track_element is not None else None
    return {
        "id": index,
        "host_track_id": None,
        "type": track_type,
        "channel_type": "multichannel",
        "name": _xml_attr(track_element, "name", "label") or f"Track {index}",
        "color": _xml_attr(track_element, "color") or "",
        "volume": _xml_float_attr(channel, ("volume",), 0.8),
        "pan": _xml_float_attr(channel, ("pan",), 0.0),
        "mute": _xml_bool_attr(channel, ("mute",), False),
        "solo": _xml_bool_attr(channel, ("solo",), False),
        "instrument": "Audio Track" if track_type == "audio" else "ATRI Basic Synth",
        "plugin_slots": [],
        "output_bus_id": None,
        "sends": [],
        "clips": [],
        "notes": [],
        "midi_events": [],
    }


def _dawproject_track_type(raw_type: str) -> str:
    value = raw_type.strip().lower()
    if "audio" in value:
        return "audio"
    if "bus" in value or "group" in value or "master" in value:
        return "bus"
    return "instrument"


def _dawproject_midi_clips(lane: ElementTree.Element, track_ref: str) -> list[dict[str, Any]]:
    clips: list[dict[str, Any]] = []
    safe_track_ref = _dawproject_safe_token(track_ref) or "track"
    for clip_index, clip_element in enumerate(_xml_iter(lane, "Clip"), start=1):
        notes = _dawproject_clip_notes(clip_element, safe_track_ref, clip_index)
        if not notes:
            continue
        clip_start = _xml_float_attr(clip_element, ("time", "start"), 0.0)
        clip_duration = _xml_float_attr(
            clip_element,
            ("duration", "length"),
            _dawproject_notes_duration(notes),
        )
        clips.append(
            {
                "id": _xml_attr(clip_element, "id") or f"{safe_track_ref}_clip_{clip_index}",
                "type": "midi",
                "name": _xml_attr(clip_element, "name", "label") or "MIDI Clip",
                "start": clip_start,
                "duration": max(clip_duration, _dawproject_notes_duration(notes), 0.25),
                "notes": notes,
                "events": [],
            }
        )
    return clips


def _dawproject_clip_notes(
    clip_element: ElementTree.Element,
    safe_track_ref: str,
    clip_index: int,
) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for note_index, note_element in enumerate(_xml_iter(clip_element, "Note"), start=1):
        pitch = _xml_int_attr(note_element, ("key", "pitch", "note"), 60)
        duration = _xml_float_attr(note_element, ("duration", "length"), 0.25)
        notes.append(
            {
                "id": _xml_attr(note_element, "id")
                or f"{safe_track_ref}_clip_{clip_index}_note_{note_index}",
                "pitch": max(0, min(127, pitch)),
                "start": _xml_float_attr(note_element, ("time", "start"), 0.0),
                "duration": max(1.0 / MIDI_TICKS_PER_BEAT, duration),
                "velocity": _dawproject_velocity(_xml_attr(note_element, "velocity", "vel")),
            }
        )
    notes.sort(key=lambda note: (note["start"], note["pitch"], note["duration"]))
    return notes


def _dawproject_tempo(root: ElementTree.Element) -> float:
    tempo = _xml_first(root, "Tempo")
    return _xml_float_attr(tempo, ("value", "tempo", "bpm"), 120.0)


def _dawproject_time_signature(root: ElementTree.Element) -> list[int]:
    signature = _xml_first(root, "TimeSignature")
    numerator = _xml_int_attr(signature, ("numerator", "num"), 4)
    denominator = _xml_int_attr(signature, ("denominator", "den"), 4)
    return [numerator, denominator]


def _dawproject_metadata_title(metadata_xml: bytes) -> str:
    if not metadata_xml:
        return ""
    try:
        root = _dawproject_xml_root(metadata_xml, "metadata.xml")
    except ValueError as exc:
        if "unsafe XML" in str(exc):
            raise
        return ""
    title = _xml_first(root, "Title")
    return str(title.text or "").strip() if title is not None else ""


def _dawproject_xml_root(data: bytes, filename: str) -> ElementTree.Element:
    if _xml_contains_unsafe_markup(data):
        raise ValueError(f"DAWproject {filename} contains unsafe XML markup")
    try:
        return ElementTree.fromstring(data)  # noqa: S314 - DTD/entity markup is rejected above.
    except ElementTree.ParseError as exc:
        raise ValueError(f"DAWproject {filename} is invalid") from exc


def _xml_contains_unsafe_markup(data: bytes) -> bool:
    lowered = data.lower()
    return b"<!doctype" in lowered or b"<!entity" in lowered


def _dawproject_length_beats(tracks: Any) -> float:
    end = 0.0
    for track in tracks:
        for clip in track.get("clips", []):
            if not isinstance(clip, dict):
                continue
            end = max(
                end,
                float(clip.get("start", 0.0) or 0.0) + float(clip.get("duration", 0.0) or 0.0),
            )
    return max(16.0, end)


def _dawproject_import_summary(project: dict[str, Any], archive_path: Path) -> dict[str, Any]:
    midi_clip_count = sum(
        1
        for track in project.get("tracks", [])
        for clip in track.get("clips", [])
        if isinstance(clip, dict) and clip.get("type") == "midi"
    )
    note_count = sum(len(track.get("notes", [])) for track in project.get("tracks", []))
    return {
        "source": str(archive_path),
        "format": "dawproject",
        "track_count": len(project.get("tracks", [])),
        "midi_clip_count": midi_clip_count,
        "note_count": note_count,
        "tempo": _positive_float(project.get("tempo"), 120.0),
        "time_signature": _project_meter(project),
    }


def _dawproject_notes_duration(notes: list[dict[str, Any]]) -> float:
    return max(
        (
            float(note.get("start", 0.0) or 0.0) + float(note.get("duration", 0.0) or 0.0)
            for note in notes
        ),
        default=0.25,
    )


def _dawproject_velocity(raw: str) -> int:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 96
    if value <= 1.0:
        value *= 127.0
    return max(1, min(127, round(value)))


def _dawproject_safe_token(value: str) -> str:
    return "".join(char if char.isalnum() or char == "_" else "_" for char in value).strip("_")


def _xml_name(element: ElementTree.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1]


def _xml_iter(element: ElementTree.Element, name: str):
    for item in element.iter():
        if _xml_name(item) == name:
            yield item


def _xml_first(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next(_xml_iter(element, name), None)


def _xml_first_child(
    element: ElementTree.Element | None,
    name: str,
) -> ElementTree.Element | None:
    if element is None:
        return None
    return next((child for child in list(element) if _xml_name(child) == name), None)


def _xml_attr(element: ElementTree.Element | None, *names: str) -> str:
    if element is None:
        return ""
    for name in names:
        value = element.attrib.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _xml_float_attr(
    element: ElementTree.Element | None,
    names: tuple[str, ...],
    default: float,
) -> float:
    for name in names:
        raw = _xml_attr(element, name)
        if not raw:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return default


def _xml_int_attr(
    element: ElementTree.Element | None,
    names: tuple[str, ...],
    default: int,
) -> int:
    for name in names:
        raw = _xml_attr(element, name)
        if not raw:
            continue
        try:
            return int(float(raw))
        except ValueError:
            continue
    return default


def _xml_bool_attr(
    element: ElementTree.Element | None,
    names: tuple[str, ...],
    default: bool,
) -> bool:
    raw = _xml_attr(element, *names)
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _append_track_xml(
    structure: ElementTree.Element,
    arrangement: ElementTree.Element,
    track: dict[str, Any],
    audio_files: list[dict[str, Any]],
    plugin_states: list[dict[str, Any]],
) -> None:
    track_id = _positive_int(track.get("id"), 0)
    track_attrs = _track_xml_attrs(track, track_id)
    track_element = ElementTree.SubElement(structure, "Track", track_attrs)
    channel = ElementTree.SubElement(track_element, "Channel", _channel_xml_attrs(track))
    devices = ElementTree.SubElement(channel, "Devices")
    _append_plugin_xml(devices, track, plugin_states)
    lane = ElementTree.SubElement(arrangement, "Lane", {"track": f"track_{track_id}"})
    _append_clip_xml(lane, track, audio_files)


def _append_plugin_xml(
    devices: ElementTree.Element,
    track: dict[str, Any],
    plugin_states: list[dict[str, Any]],
) -> None:
    track_id = _positive_int(track.get("id"), 0)
    states_by_slot = {
        state["slot_id"]: state for state in plugin_states if int(state["track_id"]) == track_id
    }
    for slot in track.get("plugin_slots", []):
        if not isinstance(slot, dict) or str(slot.get("type") or "") not in {"vst2", "vst3"}:
            continue
        plugin_type = "Vst3Plugin" if str(slot.get("type")) == "vst3" else "Vst2Plugin"
        plugin = ElementTree.SubElement(devices, plugin_type, _plugin_xml_attrs(slot))
        state = states_by_slot.get(str(slot.get("id") or "slot"))
        if state:
            ElementTree.SubElement(plugin, "State", {"path": state["archive_path"]})
            chunk = ElementTree.SubElement(plugin, "ParameterChunk", {"encoding": "base64"})
            chunk.text = state["state_b64"]


def _append_clip_xml(
    lane: ElementTree.Element,
    track: dict[str, Any],
    audio_files: list[dict[str, Any]],
) -> None:
    notes = _track_notes(track)
    if notes:
        clip = ElementTree.SubElement(
            lane,
            "Clip",
            {"time": "0", "duration": str(_notes_duration(notes))},
        )
        notes_element = ElementTree.SubElement(clip, "Notes")
        for note in notes:
            ElementTree.SubElement(notes_element, "Note", _note_xml_attrs(note))
    for audio_file in audio_files:
        if int(audio_file["track_id"]) != _positive_int(track.get("id"), 0):
            continue
        ElementTree.SubElement(
            lane,
            "Audio",
            {
                "name": audio_file["name"],
                "file": audio_file["archive_path"],
                "clip": audio_file["clip_id"],
            },
        )


def _normalize_note(note: dict[str, Any], offset: float) -> dict[str, Any]:
    start = offset + _non_negative_float(note.get("start", note.get("beat")), 0.0)
    duration = max(1.0 / MIDI_TICKS_PER_BEAT, _non_negative_float(note.get("duration"), 0.25))
    return {
        "start": start,
        "duration": duration,
        "pitch": _bounded_int(note.get("pitch"), 60, 0, 127),
        "velocity": _bounded_int(note.get("velocity"), 96, 0, 127),
        "channel": _bounded_int(note.get("channel"), 0, 0, 15),
    }


def _normalize_event(event: dict[str, Any], offset: float) -> dict[str, Any]:
    normalized = dict(event)
    normalized["start"] = offset + _non_negative_float(event.get("start", event.get("beat")), 0.0)
    normalized["channel"] = _bounded_int(event.get("channel"), 0, 0, 15)
    return normalized


def _conductor_events(project: dict[str, Any]) -> list[tuple[int, int, bytes]]:
    tempo = _positive_float(project.get("tempo"), 120.0)
    events = [
        (0, 0, _meta_text(0x03, "ATRI Conductor")),
        (0, 1, _tempo_meta(tempo)),
        (0, 2, _meter_meta(_project_meter(project))),
    ]
    for meter in project.get("meter_events", []):
        if not isinstance(meter, dict):
            continue
        events.append((_beat_tick(meter.get("beat")), 2, _meter_meta(_meter_from_event(meter))))
    return events


def _track_messages(track: dict[str, Any]) -> tuple[list[tuple[int, int, bytes]], int, int]:
    messages = [(0, 0, _meta_text(0x03, track["name"]))]
    for note in track["notes"]:
        messages.extend(_note_messages(note))
    event_messages = [_midi_event_message(event) for event in track["events"]]
    messages.extend(message for message in event_messages if message is not None)
    return messages, len(track["notes"]), len([message for message in event_messages if message])


def _note_messages(note: dict[str, Any]) -> list[tuple[int, int, bytes]]:
    channel = _bounded_int(note.get("channel"), 0, 0, 15)
    pitch = _bounded_int(note.get("pitch"), 60, 0, 127)
    velocity = _bounded_int(note.get("velocity"), 96, 0, 127)
    start = _beat_tick(note.get("start"))
    note_end = float(note.get("start", 0.0)) + float(note.get("duration", 0.0))
    end = max(start + 1, _beat_tick(note_end))
    return [
        (start, 2, bytes([0x90 | channel, pitch, velocity])),
        (end, 1, bytes([0x80 | channel, pitch, 0])),
    ]


def _midi_event_message(event: dict[str, Any]) -> tuple[int, int, bytes] | None:
    event_type = str(event.get("type") or "").strip().lower()
    channel = _bounded_int(event.get("channel"), 0, 0, 15)
    tick = _beat_tick(event.get("start"))
    if event_type == "control_change":
        controller = _bounded_int(event.get("controller"), 0, 0, 127)
        value = _bounded_int(event.get("value"), 0, 0, 127)
        return (tick, 3, bytes([0xB0 | channel, controller, value]))
    if event_type == "program_change":
        program = _bounded_int(event.get("program", event.get("value")), 0, 0, 127)
        return (tick, 3, bytes([0xC0 | channel, program]))
    if event_type == "channel_pressure":
        pressure = _bounded_int(event.get("pressure", event.get("value")), 0, 0, 127)
        return (tick, 3, bytes([0xD0 | channel, pressure]))
    if event_type == "polyphonic_key_pressure":
        pitch = _bounded_int(event.get("pitch"), 60, 0, 127)
        pressure = _bounded_int(event.get("pressure", event.get("value")), 0, 0, 127)
        return (tick, 3, bytes([0xA0 | channel, pitch, pressure]))
    if event_type == "pitch_bend":
        bend = _bounded_int(event.get("value"), 0, -8192, 8191) + 8192
        return (tick, 3, bytes([0xE0 | channel, bend & 0x7F, (bend >> 7) & 0x7F]))
    return None


def _track_chunk(events: list[tuple[int, int, bytes]]) -> bytes:
    data = bytearray()
    previous_tick = 0
    for tick, _priority, message in sorted(events, key=lambda item: (item[0], item[1], item[2])):
        safe_tick = max(0, int(tick))
        data.extend(_var_len(safe_tick - previous_tick))
        data.extend(message)
        previous_tick = safe_tick
    data.extend(_var_len(0))
    data.extend(b"\xff\x2f\x00")
    return b"MTrk" + len(data).to_bytes(4, "big") + bytes(data)


def _meta_text(kind: int, text: str) -> bytes:
    payload = text.encode("utf-8", errors="replace")
    return bytes([0xFF, kind]) + _var_len(len(payload)) + payload


def _tempo_meta(bpm: float) -> bytes:
    micros = max(1, round(60_000_000 / bpm))
    return b"\xff\x51\x03" + micros.to_bytes(3, "big")


def _meter_meta(meter: list[int]) -> bytes:
    numerator = _bounded_int(meter[0] if meter else 4, 4, 1, 255)
    denominator = _meter_denominator_power(meter[1] if len(meter) > 1 else 4)
    return bytes([0xFF, 0x58, 0x04, numerator, denominator, 24, 8])


def _var_len(value: int) -> bytes:
    buffer = value & 0x7F
    value >>= 7
    bytes_out = [buffer]
    while value:
        buffer = (value & 0x7F) | 0x80
        bytes_out.insert(0, buffer)
        value >>= 7
    return bytes(bytes_out)


def _manifest_consumer(value: str) -> str:
    consumer = str(value or "export").strip().lower()
    return consumer if consumer in {"export", "bridge"} else "export"


def _manifest_files(export: dict[str, Any]) -> list[dict[str, Any]]:
    files = export.get("files")
    if not isinstance(files, list):
        return []
    return [
        {
            key: file[key]
            for key in (
                "role",
                "path",
                "filename",
                "download_url",
                "track_id",
                "host_track_id",
                "name",
            )
            if isinstance(file, dict) and key in file
        }
        for file in files
        if isinstance(file, dict)
    ]


def _manifest_tracks(export: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = export.get("tracks")
    if isinstance(tracks, list):
        return [track for track in tracks if isinstance(track, dict)]
    track_ids = export.get("track_ids")
    if not isinstance(track_ids, list):
        return []
    return [{"project_track_id": int(track_id)} for track_id in track_ids]


def _manifest_plugin_states(export: dict[str, Any]) -> list[dict[str, Any]]:
    states = export.get("plugin_states")
    if not isinstance(states, list):
        return []
    public_keys = (
        "track_id",
        "track_name",
        "slot_id",
        "plugin_type",
        "plugin_name",
        "vendor",
        "category",
        "version",
        "path",
        "archive_path",
        "state_size",
        "state_sha256",
    )
    return [
        {key: state[key] for key in public_keys if isinstance(state, dict) and key in state}
        for state in states
        if isinstance(state, dict)
    ]


def _manifest_warnings(export: dict[str, Any]) -> list[str]:
    warnings = export.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [str(warning) for warning in warnings if warning]


def _manifest_range(export: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    beat_range = export.get("beat_range")
    if isinstance(beat_range, list | tuple) and len(beat_range) >= 2:
        result["beat_range"] = [float(beat_range[0]), float(beat_range[1])]
    time_range = export.get("time_range_seconds")
    if isinstance(time_range, list | tuple) and len(time_range) >= 2:
        result["time_range_seconds"] = [float(time_range[0]), float(time_range[1])]
    bridge_export = export.get("bridge_export")
    if isinstance(bridge_export, dict) and bridge_export.get("range_source"):
        result["source"] = str(bridge_export.get("range_source"))
    return result


def _manifest_bridge(export: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    scope = export.get("bridge_scope")
    if isinstance(scope, dict) and scope:
        result["scope"] = _manifest_public_dict(scope)
    bridge_export = export.get("bridge_export")
    if isinstance(bridge_export, dict) and bridge_export:
        result["export"] = _manifest_public_dict(bridge_export)
    preview = export.get("bridge_preview")
    if isinstance(preview, dict) and preview:
        result["preview"] = _manifest_public_dict(preview)
    return result


def _manifest_public_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _manifest_public_value(item) for key, item in value.items() if item is not None
    }


def _manifest_public_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _manifest_public_dict(value)
    if isinstance(value, list | tuple):
        return [_manifest_public_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _archive_file_entry(path: Path) -> dict[str, str]:
    return {"path": str(path), "filename": path.name}


def _asset_manifest_files(files: list[dict[str, Any]], *, role: str) -> list[dict[str, Any]]:
    result = []
    for item in files:
        file_record = {
            "role": role,
            "path": str(item.get("archive_path") or ""),
            "filename": str(item.get("filename") or Path(str(item.get("archive_path"))).name),
        }
        for key in ("track_id", "clip_id", "slot_id", "plugin_name", "state_sha256"):
            if key in item:
                file_record[key] = item[key]
        result.append(file_record)
    return result


def _archive_track_manifest(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "project_track_id": _positive_int(track.get("id"), 0),
            "name": str(track.get("name") or ""),
        }
        for track in tracks
    ]


def _strip_binary_state(export: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(export)
    sanitized["plugin_states"] = _manifest_plugin_states(export)
    return sanitized


def _track_xml_attrs(track: dict[str, Any], track_id: int) -> dict[str, str]:
    attrs = {
        "id": f"track_{track_id}",
        "name": str(track.get("name") or f"Track {track_id}"),
        "type": str(track.get("type") or "instrument"),
    }
    color = str(track.get("color") or "")
    if color:
        attrs["color"] = color
    return attrs


def _channel_xml_attrs(track: dict[str, Any]) -> dict[str, str]:
    return {
        "volume": str(_positive_float(track.get("volume"), 0.8)),
        "pan": str(_bounded_float(track.get("pan"), 0.0, -1.0, 1.0)),
        "mute": _xml_bool(track.get("mute")),
        "solo": _xml_bool(track.get("solo")),
    }


def _plugin_xml_attrs(slot: dict[str, Any]) -> dict[str, str]:
    attrs = {
        "id": str(slot.get("id") or "slot"),
        "name": str(slot.get("name") or "Plugin"),
        "path": str(slot.get("path") or slot.get("dll_path") or ""),
    }
    for key in ("vendor", "category", "version"):
        value = str(slot.get(key) or "")
        if value:
            attrs[key] = value
    return attrs


def _note_xml_attrs(note: dict[str, Any]) -> dict[str, str]:
    return {
        "time": str(note["start"]),
        "duration": str(note["duration"]),
        "key": str(note["pitch"]),
        "velocity": str(round(_bounded_int(note.get("velocity"), 96, 0, 127) / 127, 6)),
        "channel": str(_bounded_int(note.get("channel"), 0, 0, 15)),
    }


def _notes_duration(notes: list[dict[str, Any]]) -> float:
    return max((note["start"] + note["duration"] for note in notes), default=0.0)


def _unique_archive_name(prefix: str, filename: str, used_names: set[str]) -> str:
    safe_name = _safe_archive_filename(filename)
    candidate = f"{prefix}/{safe_name}"
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    index = 2
    while candidate.lower() in used_names:
        candidate = f"{prefix}/{stem} {index}{suffix}"
        index += 1
    used_names.add(candidate.lower())
    return candidate


def _safe_archive_filename(filename: str) -> str:
    raw = Path(str(filename or "asset").replace("\\", "/")).name
    safe = "".join(char if char.isalnum() or char in "._- " else "_" for char in raw).strip(" ._")
    return safe or "asset"


def _xml_bool(value: Any) -> str:
    return "true" if bool(value) else "false"


def _project_meter(project: dict[str, Any]) -> list[int]:
    meter = project.get("time_signature")
    if isinstance(meter, (list, tuple)) and len(meter) == 2:
        return [_bounded_int(meter[0], 4, 1, 255), _meter_denominator(meter[1])]
    return [4, 4]


def _meter_from_event(event: dict[str, Any]) -> list[int]:
    return [
        _bounded_int(event.get("numerator"), 4, 1, 255),
        _meter_denominator(event.get("denominator")),
    ]


def _meter_denominator(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 4
    return parsed if parsed in {1, 2, 4, 8, 16, 32, 64, 128} else 4


def _meter_denominator_power(value: Any) -> int:
    denominator = _meter_denominator(value)
    power = 0
    while denominator > 1:
        denominator //= 2
        power += 1
    return power


def _beat_tick(value: Any) -> int:
    return round(_non_negative_float(value, 0.0) * MIDI_TICKS_PER_BEAT)


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _non_negative_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, parsed)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))
