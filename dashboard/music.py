"""Music library API — scans directories, reads metadata, serves audio & artwork."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import re
import shutil
import subprocess
import zipfile
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4
from weakref import WeakKeyDictionary

from quart import Blueprint, Response, jsonify, request, send_file

from core.music_export import (
    MIDI_SCHEMA_VERSION,
    build_export_manifest,
    read_dawproject_archive,
    write_dawproject_archive,
    write_export_manifest,
    write_project_midi,
)
from core.music_project import (
    active_project_archive_id,
    automation_diff,
    automation_learned_parameter_rename,
    automation_learned_parameter_upsert,
    automation_query,
    automation_retarget,
    automation_write,
    clip_diff,
    default_project,
    find_track,
    import_audio_clip,
    list_project_archives,
    load_project,
    midi_diff,
    midi_write,
    normalize_audio_waveform,
    normalize_project,
    project_summary,
    save_project,
    save_project_as_archive,
    set_active_project_archive,
    set_track_plugin,
)
from core.music_project import (
    create_track as create_project_track,
)
from core.music_project import (
    delete_track as delete_project_track,
)
from core.music_project import (
    update_track as update_project_track,
)
from core.utils import atomic_write_text
from dashboard import host_dawproject_sync, music_library, music_streaming
from dashboard.host_dawproject_sync import (
    dawproject_snapshot_status,
    request_host_dawproject_snapshot_export,
)
from dashboard.routes._helpers import (
    add_trusted_directories,
    directory_trust_required_payload,
    resolve_workspace_path,
    untrusted_external_directories,
)
from dashboard.studio import bridge_context as bridge_context_service
from dashboard.studio import export_options, host_projection, plugin_state

# Re-exported for routes/tests that patch `dashboard.music`.
host_project_sync_prompt_context = host_dawproject_sync.host_project_sync_prompt_context
sync_latest_host_dawproject_for_daw_agent = (
    host_dawproject_sync.sync_latest_host_dawproject_for_daw_agent
)

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle

HOST_AUDIO_EXTS = {".aac", ".flac", ".m4a", ".mp3", ".wav"}
EXPORT_FORMATS = export_options.EXPORT_FORMATS
RAW_HOST_COMMAND_DENYLIST = {"bounce", "render_wav"}
BRIDGE_API_VERSION = 1
BRIDGE_LATEST_EXPORT_FILENAME = "atri-bridge-latest-export.json"
_bridge_export_instance_id = bridge_context_service.bridge_export_instance_id
record_bridge_host_context = bridge_context_service.record_bridge_host_context
bridge_host_context_for_instance = bridge_context_service.bridge_host_context_for_instance
_bridge_context_for_export_payload = bridge_context_service.export_context_for_payload
_bridge_beat_range_for_context = bridge_context_service.beat_range_for_context
_bridge_midi_scope_for_payload = bridge_context_service.midi_scope_for_payload
_bridge_selection_summary = bridge_context_service.selection_summary
_bridge_seconds_range_from_beats = bridge_context_service.seconds_range_from_beats
_bridge_created_at = bridge_context_service.created_at
StudioExportError = export_options.StudioExportError
_safe_export_stem = export_options.safe_export_stem
_normalize_export_format = export_options.normalize_export_format
_normalize_export_mode = export_options.normalize_export_mode
_normalize_export_target = export_options.normalize_export_target
_normalize_export_sample_rate = export_options.normalize_export_sample_rate
_normalize_export_bit_depth = export_options.normalize_export_bit_depth
_normalize_export_bitrate = export_options.normalize_export_bitrate
_normalize_export_consumer = export_options.normalize_export_consumer
_project_length_seconds = export_options.project_length_seconds
_export_time_range = export_options.export_time_range
_payload_has_explicit_time_range = export_options.payload_has_explicit_time_range
_read_metadata = music_library.read_metadata
_get_cover_bytes = music_library.get_cover_bytes
_find_lyrics = music_library.find_lyrics
_stream_audio_response = music_streaming.stream_audio_response
_track_slot = plugin_state.track_slot
_slot_index = plugin_state.slot_index
_load_track_slot = plugin_state.load_track_slot
_load_track_slots = plugin_state.load_track_slots
_captured_parameter_for_project = plugin_state.captured_parameter_for_project
_is_automation_track = host_projection.is_automation_track
_route_kind_for_host = host_projection.route_kind_for_host
_route_output_for_host = host_projection.route_output_for_host
_route_sends_for_host = host_projection.route_sends_for_host
_master_bus_for_host = host_projection.master_bus_for_host
_midi_events_for_host = host_projection.midi_events_for_host
_automation_lanes_for_host = host_projection.automation_lanes_for_host

bp = Blueprint("music", __name__, url_prefix="/api/music")

_lifecycle: Lifecycle | None = None
_project_broadcast_snapshot: dict[str, Any] | None = None
_project_broadcast_revision: str | None = None
_HOST_SYNC_SESSION_KEY = "__session__"
_host_sync_fingerprints: WeakKeyDictionary[object, dict[str, str]] = WeakKeyDictionary()
_host_sync_fingerprints_by_id: dict[int, dict[str, str]] = {}

logger = logging.getLogger(__name__)


def init_music(lifecycle: Lifecycle):
    global _lifecycle
    _lifecycle = lifecycle


def _cfg() -> dict[str, Any]:
    return _lifecycle.config if _lifecycle else {}


def _music_dirs() -> list[str]:
    return cast(list[str], _cfg().get("music_directories", []))


def _cache_path() -> Path:
    p = Path("data/music_cache.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _audio_import_dir() -> Path:
    path = Path("data/music_workstation/audio")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _audio_export_dir() -> Path:
    path = Path("data/music_workstation/exports")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_audio_filename(filename: str) -> str:
    raw_name = Path(str(filename or "audio.wav").replace("\\", "/")).name
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw_name).strip(" ._")
    return safe or "audio.wav"


def _audio_duration_seconds(path: Path, fallback: Any = None) -> float:
    try:
        parsed = float(fallback)
    except (TypeError, ValueError):
        parsed = 0.0
    if parsed > 0:
        return parsed

    metadata = _read_metadata(str(path))
    try:
        return max(0.0, float((metadata or {}).get("duration") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _audio_waveform_from_form(raw: Any) -> list[float | dict[str, float]]:
    if not raw:
        return []
    try:
        loaded = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    return normalize_audio_waveform(loaded)


def _audio_waveform_from_payload(raw: Any) -> list[float | dict[str, float]]:
    if isinstance(raw, str):
        return _audio_waveform_from_form(raw)
    return normalize_audio_waveform(raw)


def _audio_type_error(message: str, **extra: Any) -> tuple[Response, int]:
    return jsonify({"type": "error", "error_type": "type_error", "error": message, **extra}), 400


def _audio_file_missing_or_empty(path: Path) -> bool:
    return not path.exists() or path.stat().st_size == 0


def _delete_audio_import_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _delete_export_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _non_automation_tracks(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        track
        for track in project.get("tracks", [])
        if isinstance(track, dict) and not _is_automation_track(track)
    ]


def _export_tracks_for_payload(
    project: dict[str, Any],
    payload: dict[str, Any],
    target: str,
) -> list[dict[str, Any]]:
    if target == "entire_project":
        tracks = _non_automation_tracks(project)
    else:
        raw_track_ids = payload.get("track_ids")
        if not isinstance(raw_track_ids, list) or not raw_track_ids:
            raise StudioExportError("track_ids is required for selected_tracks export")
        tracks = []
        for raw_track_id in raw_track_ids:
            try:
                track = find_track(project, int(raw_track_id))
            except (TypeError, ValueError) as exc:
                raise StudioExportError(f"track not found: {raw_track_id}", 404) from exc
            if _is_automation_track(track):
                raise StudioExportError(f"track is not exportable: {raw_track_id}", 400)
            tracks.append(track)

    export_tracks: list[dict[str, Any]] = []
    for track in tracks:
        host_track_id = track.get("host_track_id")
        if host_track_id is None:
            raise StudioExportError(f"track is not synced to the host: {track.get('id')}", 409)
        export_tracks.append(
            {
                "project_track_id": int(track["id"]),
                "host_track_id": int(host_track_id),
                "name": str(track.get("name") or f"Track {track['id']}"),
            }
        )
    if not export_tracks:
        raise StudioExportError("no exportable tracks found", 400)
    return export_tracks


def _unique_zip_names(stems: list[dict[str, Any]], format_name: str) -> dict[int, str]:
    used: set[str] = set()
    names: dict[int, str] = {}
    for stem in stems:
        base = _safe_export_stem(stem.get("name"), f"Track {stem['project_track_id']}")
        candidate = f"{base}.{format_name}"
        suffix = 2
        while candidate.lower() in used:
            candidate = f"{base} {suffix}.{format_name}"
            suffix += 1
        used.add(candidate.lower())
        names[int(stem["project_track_id"])] = candidate
    return names


def _ffmpeg_path() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    return str(imageio_ffmpeg.get_ffmpeg_exe())


def _run_ffmpeg_encode(
    source: Path,
    target: Path,
    *,
    format_name: str,
    bit_depth: str,
    bitrate: str,
) -> None:
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise StudioExportError(f"ffmpeg is required for {format_name} export", 409)

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map_metadata",
        "-1",
    ]
    if format_name == "flac":
        sample_fmt = "s16" if bit_depth == "i16" else "s32"
        command.extend(["-c:a", "flac", "-sample_fmt", sample_fmt, "-compression_level", "8"])
    elif format_name == "mp3":
        command.extend(["-c:a", "libmp3lame", "-b:a", bitrate])
    else:
        raise StudioExportError("format is not supported", 400)
    command.append(str(target))

    try:
        subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise StudioExportError(f"ffmpeg is required for {format_name} export", 409) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        raise StudioExportError(f"ffmpeg failed: {stderr or exc}", 409) from exc


async def _encode_export_file(
    source: Path,
    target: Path,
    *,
    format_name: str,
    bit_depth: str,
    bitrate: str,
) -> None:
    await asyncio.to_thread(
        _run_ffmpeg_encode,
        source,
        target,
        format_name=format_name,
        bit_depth=bit_depth,
        bitrate=bitrate,
    )


async def _render_host_wav(
    host: Any,
    path: Path,
    *,
    start: float,
    end: float,
    track_ids: list[int] | None,
    sample_rate: int,
    bit_depth: str,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "path": str(path),
        "format": "wav",
        "start": start,
        "end": end,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
    }
    if track_ids is not None:
        params["track_ids"] = track_ids
    response = await host.send_command("bounce", params, response_timeout=None)
    if response.get("type") == "error":
        raise StudioExportError(str(response.get("message") or "host bounce failed"), 409)
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return data


def _export_download_url(path: Path) -> str:
    return f"/api/music/studio/export/download/{path.name}"


def _is_in_music_dirs(filepath: str) -> bool:
    return music_library.is_in_music_dirs(filepath, _music_dirs())


# ── Routes ──


@bp.route("/dirs", methods=["GET"])
async def get_dirs():
    return jsonify({"directories": _music_dirs()})


@bp.route("/dirs", methods=["POST"])
async def save_dirs():
    data = await request.get_json() or {}
    dirs = data.get("directories", [])
    if not isinstance(dirs, list):
        dirs = []
    untrusted = untrusted_external_directories(
        _cfg(),
        dirs,
        extra_trusted=_music_dirs(),
    )
    if untrusted and data.get("trust") is not True:
        return jsonify(directory_trust_required_payload(untrusted)), 409
    if untrusted:
        add_trusted_directories(_cfg(), untrusted)
    _cfg()["music_directories"] = dirs
    if _lifecycle:
        _lifecycle.save_config()
    return jsonify({"ok": True})


@bp.route("/scan", methods=["POST"])
async def scan_library():
    songs = music_library.scan_music_directories(_music_dirs())

    try:
        _cache_path().write_text(json.dumps(songs, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.debug("Failed to write music cache", exc_info=True)

    return jsonify({"songs": songs, "count": len(songs)})


@bp.route("/library", methods=["GET"])
async def get_library():
    cp = _cache_path()
    if cp.exists():
        try:
            songs = json.loads(cp.read_text(encoding="utf-8"))
            return jsonify({"songs": songs, "count": len(songs)})
        except Exception:
            logger.debug("Failed to read music cache", exc_info=True)
    return jsonify({"songs": [], "count": 0})


@bp.route("/stream/<song_id>")
async def stream_audio(song_id: str):
    cp = _cache_path()
    if not cp.exists():
        return jsonify({"error": "library not scanned"}), 404

    songs = json.loads(cp.read_text(encoding="utf-8"))
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        return jsonify({"error": "song not found"}), 404

    filepath = song["path"]
    # Validate path is within configured music directories
    if not _is_in_music_dirs(filepath):
        return jsonify({"error": "file outside music directories"}), 403
    if not Path(filepath).exists():  # noqa: ASYNC240
        return jsonify({"error": "file not found"}), 404

    return _stream_audio_response(filepath, request.headers.get("Range"))


@bp.route("/cover/<song_id>")
async def get_cover(song_id: str):
    cp = _cache_path()
    if not cp.exists():
        return jsonify({"error": "library not scanned"}), 404

    songs = json.loads(cp.read_text(encoding="utf-8"))
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        return jsonify({"error": "song not found"}), 404

    result = _get_cover_bytes(song["path"])
    if not result:
        return Response(status=204)

    data, mime = result
    return Response(data, content_type=mime, headers={"Cache-Control": "public, max-age=604800"})


@bp.route("/lyrics/<song_id>")
async def get_lyrics(song_id: str):
    cp = _cache_path()
    if not cp.exists():
        return jsonify({"lyrics": None})

    songs = json.loads(cp.read_text(encoding="utf-8"))
    song = next((s for s in songs if s["id"] == song_id), None)
    if not song:
        return jsonify({"lyrics": None})

    lyrics = _find_lyrics(song["path"])
    return jsonify({"lyrics": lyrics})


# ─── AI Music Workstation / Rust Host ───


async def _json_payload() -> dict[str, Any]:
    data = await request.get_json()
    return data if isinstance(data, dict) else {}


def _host_manager():
    from core.host import get_host_manager

    return get_host_manager()


def _host_snapshot() -> dict[str, Any]:
    host = _host_manager()
    return {
        "running": bool(getattr(host, "is_running", False)),
        "sample_rate": getattr(host, "sample_rate", None),
        "buffer_size": getattr(host, "buffer_size", None),
        "audio_engine": getattr(host, "audio_engine", ""),
        "bit_depth": getattr(host, "bit_depth", ""),
        "binary_path": getattr(host, "binary_path", "") or "",
    }


def _response_data(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else {}


def _sync_audio_clip_error(sync: dict[str, Any]) -> str | None:
    commands = sync.get("commands") if isinstance(sync, dict) else None
    if not isinstance(commands, list):
        return None
    for response in commands:
        if (
            isinstance(response, dict)
            and response.get("type") == "error"
            and response.get("cmd") == "set_audio_clips"
        ):
            return str(response.get("message") or "failed to import audio clip")
    return None


async def _capture_plugin_states(
    project: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return await plugin_state.capture_plugin_states(project, host_manager=_host_manager)


async def _capture_and_save_plugin_states(
    project: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return await plugin_state.capture_and_save_plugin_states(
        project,
        host_manager=_host_manager,
        load_project=load_project,
        save_project=save_project,
    )


async def open_plugin_editor_for_track(
    track_id: int,
    *,
    slot_id: str = "instrument",
) -> tuple[dict[str, Any], int]:
    return await plugin_state.open_plugin_editor_for_track(
        track_id,
        slot_id=slot_id,
        load_project=load_project,
        find_track=find_track,
        host_manager=_host_manager,
        host_snapshot=_host_snapshot,
        sync_project_to_host=_sync_project_to_host,
    )


def _json_pointer_path(path: str, token: object) -> str:
    escaped = str(token).replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}" if path else f"/{escaped}"


def _identified_list_ids(items: list[Any]) -> list[str] | None:
    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") is None:
            return None
        ids.append(str(item["id"]))
    return ids if len(set(ids)) == len(ids) else None


def _shared_identified_ids(previous_ids: list[str], current_ids: list[str]) -> list[str]:
    matcher = SequenceMatcher(a=previous_ids, b=current_ids, autojunk=False)
    shared: list[str] = []
    for block in matcher.get_matching_blocks():
        shared.extend(previous_ids[block.a : block.a + block.size])
    return shared


def _identified_list_patch(
    previous: list[Any],
    current: list[Any],
    path: str,
) -> list[dict[str, Any]] | None:
    previous_ids = _identified_list_ids(previous)
    current_ids = _identified_list_ids(current)
    if previous_ids is None or current_ids is None:
        return None

    previous_by_id = dict(zip(previous_ids, previous, strict=True))
    current_by_id = dict(zip(current_ids, current, strict=True))
    shared_ids = _shared_identified_ids(previous_ids, current_ids)
    shared_id_set = set(shared_ids)
    operations: list[dict[str, Any]] = []

    for index in range(len(previous_ids) - 1, -1, -1):
        if previous_ids[index] not in shared_id_set:
            operations.append({"op": "remove", "path": _json_pointer_path(path, index)})

    intermediate_ids = [item_id for item_id in previous_ids if item_id in shared_id_set]
    for index, item_id in enumerate(current_ids):
        if item_id in shared_id_set:
            continue
        operations.append(
            {"op": "add", "path": _json_pointer_path(path, index), "value": current_by_id[item_id]}
        )
        intermediate_ids.insert(index, item_id)

    if intermediate_ids != current_ids:
        return [{"op": "replace", "path": path or "", "value": current}]

    for index, item_id in enumerate(current_ids):
        if item_id in shared_id_set:
            operations.extend(
                _json_patch(
                    previous_by_id[item_id],
                    current_by_id[item_id],
                    _json_pointer_path(path, index),
                )
            )
    return operations


def _json_patch(previous: Any, current: Any, path: str = "") -> list[dict[str, Any]]:
    if previous == current:
        return []
    if isinstance(previous, dict) and isinstance(current, dict):
        operations: list[dict[str, Any]] = []
        for key in sorted(previous.keys() - current.keys()):
            operations.append({"op": "remove", "path": _json_pointer_path(path, key)})
        for key in sorted(current.keys()):
            child_path = _json_pointer_path(path, key)
            if key not in previous:
                operations.append({"op": "add", "path": child_path, "value": current[key]})
            else:
                operations.extend(_json_patch(previous[key], current[key], child_path))
        return operations
    if isinstance(previous, list) and isinstance(current, list):
        identified_patch = _identified_list_patch(previous, current, path)
        if identified_patch is not None:
            return identified_patch
        operations = []
        shared_length = min(len(previous), len(current))
        for index in range(shared_length):
            operations.extend(
                _json_patch(previous[index], current[index], _json_pointer_path(path, index))
            )
        for index in range(len(previous) - 1, len(current) - 1, -1):
            operations.append({"op": "remove", "path": _json_pointer_path(path, index)})
        for index in range(shared_length, len(current)):
            operations.append(
                {"op": "add", "path": _json_pointer_path(path, index), "value": current[index]}
            )
        return operations
    return [{"op": "replace", "path": path or "", "value": current}]


def _remember_project_broadcast_snapshot(
    project: dict[str, Any],
    revision: str | None = None,
) -> None:
    global _project_broadcast_revision, _project_broadcast_snapshot

    _project_broadcast_snapshot = deepcopy(project)
    _project_broadcast_revision = revision or _project_revision(project)


async def _broadcast_project(project: dict[str, Any]) -> None:
    global _project_broadcast_revision, _project_broadcast_snapshot

    dashboard = getattr(_lifecycle, "dashboard", None) if _lifecycle else None
    if dashboard:
        revision = _project_revision(project)
        base_revision = _project_broadcast_revision
        patch = (
            _json_patch(_project_broadcast_snapshot, project)
            if _project_broadcast_snapshot is not None
            else None
        )
        await dashboard.broadcast(
            {
                "type": "music_project",
                "base_revision": base_revision,
                "revision": revision,
                "patch": patch,
                "summary": project_summary(project),
            }
        )
        _remember_project_broadcast_snapshot(project, revision)


async def reconcile_dashboard_audio_streaming() -> None:
    dashboard = getattr(_lifecycle, "dashboard", None) if _lifecycle else None
    if dashboard:
        await dashboard.reconcile_audio_streaming_state()


def _project_save_fingerprint(project: dict[str, Any]) -> str:
    normalized = normalize_project(project)
    normalized.pop("updated_at", None)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _project_revision(project: dict[str, Any]) -> str:
    return hashlib.sha256(_project_save_fingerprint(project).encode("utf-8")).hexdigest()


def _project_payload(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": project,
        "revision": _project_revision(project),
        "active_project_id": active_project_archive_id(),
    }


def _project_differs_from_saved_project(project: dict[str, Any]) -> bool:
    return _project_save_fingerprint(project) != _project_save_fingerprint(load_project())


def _host_sync_cache_for(host: Any) -> dict[str, str]:
    try:
        cache = _host_sync_fingerprints.get(host)
    except TypeError:
        return _host_sync_fingerprints_by_id.setdefault(id(host), {})
    if cache is None:
        cache = {}
        _host_sync_fingerprints[host] = cache
    return cache


def _clear_host_sync_caches() -> None:
    _host_sync_fingerprints.clear()
    _host_sync_fingerprints_by_id.clear()


def _clear_host_track_sync_cache(cache: dict[str, str], host_track_id: int) -> None:
    prefix = f"track:{host_track_id}:"
    for key in [key for key in cache if key.startswith(prefix)]:
        cache.pop(key, None)


def _host_sync_fingerprint(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _host_sync_session_fingerprint(host: Any) -> str:
    process = getattr(host, "_process", None)
    process_identity = None
    if process is not None:
        process_identity = {
            "object_id": id(process),
            "pid": getattr(process, "pid", None),
        }
    return _host_sync_fingerprint(
        {
            "binary_path": str(getattr(host, "binary_path", "") or ""),
            "process": process_identity,
        }
    )


async def _send_changed_host_command(
    host: Any,
    commands: list[dict[str, Any]],
    cache: dict[str, str],
    key: str,
    cmd: str,
    params: dict[str, Any],
    *,
    force: bool = False,
) -> bool:
    fingerprint = _host_sync_fingerprint({"cmd": cmd, "params": params})
    if not force and cache.get(key) == fingerprint:
        return False
    response = cast(dict[str, Any], await host.send_command(cmd, params))
    commands.append(response)
    if response.get("type") == "error":
        cache.pop(key, None)
    else:
        cache[key] = fingerprint
    return True


async def _sync_project_to_host(
    project: dict[str, Any],
    *,
    broadcast: bool = False,
) -> dict[str, Any]:
    host = _host_manager()
    if not host.is_running:
        _clear_host_sync_caches()
        project = save_project(project)
        if broadcast:
            await _broadcast_project(project)
        return {
            "host_running": False,
            "commands": [],
            "revision": _project_revision(project),
            "project": project,
            "summary": project_summary(project),
        }

    commands: list[dict[str, Any]] = []
    sync_cache = _host_sync_cache_for(host)
    session_fingerprint = _host_sync_session_fingerprint(host)
    if sync_cache.get(_HOST_SYNC_SESSION_KEY) != session_fingerprint:
        sync_cache.clear()
        sync_cache[_HOST_SYNC_SESSION_KEY] = session_fingerprint
    project_changed = _project_differs_from_saved_project(project)
    status = await host.send_command("get_status")
    host_track_ids = {
        int(track.get("id", -1)) for track in status.get("tracks", []) if isinstance(track, dict)
    }
    project_host_track_ids: set[int] = set()
    master_bus = _master_bus_for_host(project)
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        if _is_automation_track(track):
            continue
        project_host_track_id = track.get("host_track_id")
        if project_host_track_id is not None:
            project_host_track_ids.add(int(project_host_track_id))
    if master_bus is not None and master_bus.get("host_track_id") is not None:
        project_host_track_ids.add(int(master_bus["host_track_id"]))

    meter = project.get("time_signature") or [4, 4]
    await _send_changed_host_command(
        host,
        commands,
        sync_cache,
        "global:tempo",
        "set_tempo",
        {"bpm": float(project.get("tempo", 120.0)), "time_sig": meter},
    )

    for stale_host_track_id in sorted(host_track_ids - project_host_track_ids):
        response = await host.send_command("remove_track", {"id": stale_host_track_id})
        commands.append(response)
        if response.get("type") != "error":
            host_track_ids.remove(stale_host_track_id)
            _clear_host_track_sync_cache(sync_cache, stale_host_track_id)

    routing_skipped: list[dict[str, Any]] = []
    routing_routes = 0
    force_track_sync_ids: set[int] = set()
    route_tracks: list[dict[str, Any]] = []
    master_slots_loaded = False
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        if _is_automation_track(track):
            track["host_track_id"] = None
            continue
        host_track_id = track.get("host_track_id")
        previous_host_track_id = int(host_track_id) if host_track_id is not None else None
        if previous_host_track_id is None or previous_host_track_id not in host_track_ids:
            if previous_host_track_id is not None:
                _clear_host_track_sync_cache(sync_cache, previous_host_track_id)
            response = await host.send_command("add_track", {"name": track.get("name", "Track")})
            commands.append(response)
            new_host_track_id = _response_data(response).get("track_id")
            if new_host_track_id is None:
                continue
            host_track_id = int(new_host_track_id)
            track["host_track_id"] = int(host_track_id)
            host_track_ids.add(host_track_id)
            force_track_sync_ids.add(host_track_id)
            project_changed = True
            commands.extend(await _load_track_slots(host, host_track_id, track))
        else:
            host_track_id = previous_host_track_id

        route_tracks.append(track)

    if master_bus is not None:
        host_track_id = master_bus.get("host_track_id")
        previous_host_track_id = int(host_track_id) if host_track_id is not None else None
        if previous_host_track_id is None or previous_host_track_id not in host_track_ids:
            if previous_host_track_id is not None:
                _clear_host_track_sync_cache(sync_cache, previous_host_track_id)
            response = await host.send_command(
                "add_track",
                {"name": master_bus.get("name", "Master Bus")},
            )
            commands.append(response)
            host_track_id = _response_data(response).get("track_id")
            if host_track_id is not None:
                master_bus["host_track_id"] = int(host_track_id)
                host_track_ids.add(int(host_track_id))
                force_track_sync_ids.add(int(host_track_id))
                project_changed = True
                commands.extend(await _load_track_slots(host, int(host_track_id), master_bus))
                master_slots_loaded = True

        if host_track_id is not None:
            route_tracks.append(master_bus)

    master_host_track_id = (
        int(master_bus["host_track_id"])
        if master_bus is not None and master_bus.get("host_track_id") is not None
        else None
    )

    route_kind_sent = False
    for track in route_tracks:
        host_track_id = int(track["host_track_id"])
        route_kind_sent = (
            await _send_changed_host_command(
                host,
                commands,
                sync_cache,
                f"track:{host_track_id}:route_kind",
                "set_route_config",
                {
                    "track_id": host_track_id,
                    "kind": _route_kind_for_host(track),
                    "output_track_id": None,
                },
                force=host_track_id in force_track_sync_ids,
            )
            or route_kind_sent
        )
        routing_routes += 1

    for track in route_tracks:
        host_track_id = int(track["host_track_id"])
        if track is master_bus:
            output_track_id, routing_skip = None, None
        else:
            output_track_id, routing_skip = _route_output_for_host(project, track)
            if (
                output_track_id is None
                and routing_skip is None
                and master_host_track_id is not None
                and track.get("output_bus_id") is None
            ):
                output_track_id = master_host_track_id
        if routing_skip is not None:
            routing_skipped.append(routing_skip)
        await _send_changed_host_command(
            host,
            commands,
            sync_cache,
            f"track:{host_track_id}:route_output",
            "set_route_config",
            {
                "track_id": host_track_id,
                "kind": None,
                "output_track_id": output_track_id,
            },
            force=route_kind_sent or host_track_id in force_track_sync_ids,
        )
        route_sends, send_skips = _route_sends_for_host(project, track)
        routing_skipped.extend(send_skips)
        await _send_changed_host_command(
            host,
            commands,
            sync_cache,
            f"track:{host_track_id}:route_sends",
            "set_route_sends",
            {
                "track_id": host_track_id,
                "sends": route_sends,
            },
            force=route_kind_sent or host_track_id in force_track_sync_ids,
        )

    if master_host_track_id is not None and not master_slots_loaded:
        commands.extend(
            await _load_track_slots(
                host,
                master_host_track_id,
                cast(dict[str, Any], master_bus),
            )
        )

    for track in route_tracks:
        host_track_id = int(track["host_track_id"])
        notes = [
            {
                "pitch": int(note["pitch"]),
                "start": float(note["start"]),
                "duration": float(note["duration"]),
                "velocity": int(note["velocity"]),
            }
            for note in track.get("notes", [])
        ]
        midi_events = _midi_events_for_host(track)
        audio_clips = [
            {
                "path": str(clip.get("path") or clip.get("source") or ""),
                "start": float(clip.get("start", 0.0) or 0.0),
                "duration": float(clip.get("duration", 0.0) or 0.0),
                "source_offset": float(clip.get("source_offset", 0.0) or 0.0),
                "gain": float(clip.get("gain", 1.0) or 1.0),
                "channel_type": str(track.get("channel_type") or "multichannel"),
            }
            for clip in track.get("clips", [])
            if isinstance(clip, dict)
            and clip.get("type") == "audio"
            and str(clip.get("path") or clip.get("source") or "")
        ]
        track_force = host_track_id in force_track_sync_ids
        await _send_changed_host_command(
            host,
            commands,
            sync_cache,
            f"track:{host_track_id}:midi",
            "set_midi",
            {"track_id": host_track_id, "notes": notes, "events": midi_events},
            force=track_force,
        )
        await _send_changed_host_command(
            host,
            commands,
            sync_cache,
            f"track:{host_track_id}:audio_clips",
            "set_audio_clips",
            {"track_id": host_track_id, "clips": audio_clips},
            force=track_force,
        )
        await _send_changed_host_command(
            host,
            commands,
            sync_cache,
            f"track:{host_track_id}:volume",
            "set_volume",
            {"track_id": host_track_id, "value": float(track.get("volume", 0.8))},
            force=track_force,
        )
        await _send_changed_host_command(
            host,
            commands,
            sync_cache,
            f"track:{host_track_id}:pan",
            "set_pan",
            {"track_id": host_track_id, "value": float(track.get("pan", 0.0))},
            force=track_force,
        )
        await _send_changed_host_command(
            host,
            commands,
            sync_cache,
            f"track:{host_track_id}:mute",
            "set_mute",
            {"track_id": host_track_id, "value": bool(track.get("mute", False))},
            force=track_force,
        )
        await _send_changed_host_command(
            host,
            commands,
            sync_cache,
            f"track:{host_track_id}:solo",
            "set_solo",
            {"track_id": host_track_id, "value": bool(track.get("solo", False))},
            force=track_force,
        )

    automation_lanes, skipped_automation = _automation_lanes_for_host(project)
    await _send_changed_host_command(
        host,
        commands,
        sync_cache,
        "global:automation",
        "set_automation",
        {"lanes": automation_lanes},
    )

    if project_changed or broadcast:
        project = save_project(project)
    if broadcast:
        await _broadcast_project(project)

    return {
        "host_running": True,
        "commands": commands,
        "revision": _project_revision(project),
        "automation": {
            "lanes": len(automation_lanes),
            "skipped": skipped_automation,
        },
        "routing": {
            "routes": routing_routes,
            "skipped": routing_skipped,
        },
        "project": project,
        "summary": project_summary(project),
    }


async def sync_current_project_to_host(*, broadcast: bool = False) -> dict[str, Any]:
    return await _sync_project_to_host(load_project(), broadcast=broadcast)


@bp.route("/studio/project", methods=["GET"])
async def studio_project():
    project = load_project()
    revision = _project_revision(project)
    _remember_project_broadcast_snapshot(project, revision)
    return jsonify(
        {
            "project": project,
            "revision": revision,
            "active_project_id": active_project_archive_id(),
            "projects": list_project_archives(),
            "summary": project_summary(project),
            "host": _host_snapshot(),
        }
    )


@bp.route("/studio/projects", methods=["GET"])
async def studio_projects():
    return jsonify(
        {
            "projects": list_project_archives(),
            "active_project_id": active_project_archive_id(),
        }
    )


@bp.route("/studio/projects/save-copy", methods=["POST"])
async def studio_save_project_copy():
    data = await _json_payload()
    project = load_project()
    title = str(data.get("title") or "").strip()
    copied = save_project_as_archive(project, title=title, activate=True)
    sync = None
    if data.get("sync", True) is not False:
        sync = await _sync_project_to_host(copied, broadcast=True)
        copied = sync.get("project", copied)
    else:
        await _broadcast_project(copied)
    return jsonify(
        {
            "ok": True,
            **_project_payload(copied),
            "projects": list_project_archives(),
            "sync": sync,
            "host": _host_snapshot(),
        }
    )


@bp.route("/studio/projects/<project_id>/open", methods=["POST"])
async def studio_open_project(project_id: str):
    data = await _json_payload()
    try:
        project = set_active_project_archive(project_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    sync = None
    if data.get("sync", True) is not False:
        sync = await _sync_project_to_host(project, broadcast=True)
        project = sync.get("project", project)
    else:
        await _broadcast_project(project)
    return jsonify(
        {
            "ok": True,
            **_project_payload(project),
            "projects": list_project_archives(),
            "sync": sync,
            "host": _host_snapshot(),
        }
    )


@bp.route("/studio/project", methods=["PUT"])
async def save_studio_project():
    data = await _json_payload()
    project, state_capture = await _capture_plugin_states(data.get("project") or {})
    project = save_project(project)
    sync = await _sync_project_to_host(
        project,
        broadcast=True,
    )
    return jsonify(
        {
            "ok": True,
            **_project_payload(project),
            "projects": list_project_archives(),
            "sync": sync,
            "state": state_capture,
        }
    )


@bp.route("/studio/demo", methods=["POST"])
async def reset_studio_demo():
    await _json_payload()
    project = save_project(default_project())
    sync = await _sync_project_to_host(
        project,
        broadcast=True,
    )
    return jsonify(
        {
            "ok": True,
            **_project_payload(project),
            "projects": list_project_archives(),
            "sync": sync,
        }
    )


@bp.route("/studio/host/start", methods=["POST"])
async def start_audio_host():
    data = await _json_payload()
    host = _host_manager()
    try:
        await host.start()
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e), "host": _host_snapshot()}), 409
    except OSError as e:
        return jsonify({"ok": False, "error": str(e), "host": _host_snapshot()}), 500

    sync = None
    if data.get("sync", True):
        sync = await _sync_project_to_host(load_project(), broadcast=True)
    await reconcile_dashboard_audio_streaming()
    return jsonify({"ok": True, "host": _host_snapshot(), "sync": sync})


@bp.route("/studio/host/stop", methods=["POST"])
async def stop_audio_host():
    _, state_capture = await _capture_and_save_plugin_states()
    host = _host_manager()
    await host.stop()
    return jsonify({"ok": True, "host": _host_snapshot(), "state": state_capture})


@bp.route("/studio/host/status", methods=["GET"])
async def audio_host_status():
    host = _host_manager()
    engine = None
    if host.is_running:
        engine = await host.send_command("get_status")
    return jsonify({"host": _host_snapshot(), "engine": engine})


@bp.route("/studio/host/command", methods=["POST"])
async def audio_host_command():
    data = await _json_payload()
    cmd = str(data.get("cmd") or "").strip()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if not cmd:
        return jsonify({"error": "cmd is required"}), 400
    if cmd.lower() in RAW_HOST_COMMAND_DENYLIST:
        return jsonify({"error": "command is not allowed through the raw host endpoint"}), 403
    host = _host_manager()
    if not host.is_running:
        return jsonify({"error": "host process not running", "host": _host_snapshot()}), 409
    response = await host.send_command(cmd, params)
    return jsonify({"response": response, "host": _host_snapshot()})


@bp.route("/studio/tracks/<int:track_id>/plugin/editor", methods=["POST"])
async def studio_open_plugin_editor(track_id: int):
    data = await _json_payload()
    slot_id = str(data.get("slot_id") or "instrument")
    result, status = await open_plugin_editor_for_track(track_id, slot_id=slot_id)
    return jsonify(result), status


@bp.route("/studio/tracks/<int:track_id>/plugin/parameters", methods=["GET"])
async def studio_plugin_parameters(track_id: int):
    slot_id = str(request.args.get("slot_id") or "instrument")
    project = load_project()
    try:
        track = find_track(project, track_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "host": _host_snapshot()}), 404

    host = _host_manager()
    if not host.is_running:
        return (
            jsonify({"ok": False, "error": "host process not running", "host": _host_snapshot()}),
            409,
        )
    if track.get("host_track_id") is None:
        sync = await _sync_project_to_host(project, broadcast=True)
        project = sync.get("project", project)
        track = find_track(project, track_id)
    host_track_id = track.get("host_track_id")
    if host_track_id is None:
        return (
            jsonify(
                {"ok": False, "error": "track is not synced to the host", "host": _host_snapshot()}
            ),
            409,
        )

    slot_index = _slot_index(slot_id)
    response = await host.send_command(
        "list_plugin_parameters",
        {"track_id": int(host_track_id), "slot_index": slot_index},
    )
    ok = response.get("type") != "error"
    status = 200 if ok else 409
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return (
        jsonify(
            {
                "ok": ok,
                "error": response.get("message") if not ok else None,
                "project_track_id": track_id,
                "host_track_id": int(host_track_id),
                "slot_id": slot_id,
                "slot_index": slot_index,
                "plugin": _track_slot(track, slot_id),
                "parameters": data.get("parameters") or [],
                "parameter_count": data.get("parameter_count", 0),
                "response": response,
                "host": _host_snapshot(),
            }
        ),
        status,
    )


@bp.route("/studio/plugin/parameter", methods=["POST"])
async def studio_set_plugin_parameter():
    data = await _json_payload()
    track_id = int(data.get("track_id", 1))
    slot_id = str(data.get("slot_id") or "instrument")
    param_index = int(data.get("param_index", data.get("index", 0)) or 0)
    value = float(data.get("value", 0.0) or 0.0)
    project = load_project()
    try:
        track = find_track(project, track_id)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "host": _host_snapshot()}), 404
    host = _host_manager()
    if not host.is_running:
        return (
            jsonify({"ok": False, "error": "host process not running", "host": _host_snapshot()}),
            409,
        )
    if track.get("host_track_id") is None:
        sync = await _sync_project_to_host(project, broadcast=True)
        project = sync.get("project", project)
        track = find_track(project, track_id)
    host_track_id = track.get("host_track_id")
    if host_track_id is None:
        return (
            jsonify(
                {"ok": False, "error": "track is not synced to the host", "host": _host_snapshot()}
            ),
            409,
        )
    slot_index = _slot_index(slot_id)
    response = await host.send_command(
        "set_plugin_parameter",
        {
            "track_id": int(host_track_id),
            "slot_index": slot_index,
            "index": param_index,
            "value": value,
        },
    )
    ok = response.get("type") != "error"
    state_capture: list[dict[str, Any]] = []
    if ok:
        project, state_capture = await _capture_and_save_plugin_states(project)
        await _broadcast_project(project)
    return (
        jsonify(
            {
                "ok": ok,
                "error": response.get("message") if not ok else None,
                "response": response,
                **(_project_payload(project) if ok else {"project": None, "revision": None}),
                "state": state_capture,
                "host": _host_snapshot(),
            }
        ),
        200 if ok else 409,
    )


@bp.route("/studio/plugin/captured-parameters", methods=["GET"])
async def studio_captured_plugin_parameters():
    project = load_project()
    captured_for_project: list[dict[str, Any]] = []
    host = _host_manager()
    if host.is_running:
        response = await host.send_command("poll_captured_plugin_parameters")
        if response.get("type") == "error":
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": response.get("message"),
                        "captured": [],
                        "learned_parameters": project.get("automation_learned_parameters", []),
                        **_project_payload(project),
                        "host": _host_snapshot(),
                    }
                ),
                409,
            )
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        for captured in data.get("parameters") or []:
            if not isinstance(captured, dict):
                continue
            learned_payload = _captured_parameter_for_project(project, captured)
            if not learned_payload:
                continue
            project, learned = automation_learned_parameter_upsert(learned_payload)
            captured_for_project.append(learned)
    return jsonify(
        {
            "ok": True,
            "captured": captured_for_project,
            "learned_parameters": project.get("automation_learned_parameters", []),
            **_project_payload(project),
            "host": _host_snapshot(),
        }
    )


@bp.route("/studio/plugin/learned-parameters/<parameter_id>", methods=["PATCH"])
async def studio_rename_learned_plugin_parameter(parameter_id: str):
    data = await _json_payload()
    try:
        project, learned = automation_learned_parameter_rename(
            parameter_id,
            str(data.get("name") or ""),
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    await _broadcast_project(project)
    return jsonify({"ok": True, **_project_payload(project), "learned_parameter": learned})


@bp.route("/studio/plugins", methods=["GET", "POST"])
async def studio_plugins():
    host = _host_manager()
    if not host.is_running:
        return jsonify(
            {
                "plugins": {"vst3": [], "vst2": [], "priority": ["vst3", "vst2"]},
                "host": _host_snapshot(),
            }
        )

    data = await _json_payload() if request.method == "POST" else {}
    params = {}
    if isinstance(data.get("paths"), list):
        params["paths"] = data["paths"]
    if isinstance(data.get("vst2_paths"), list):
        params["vst2_paths"] = data["vst2_paths"]
    response = await host.send_command("scan_plugins", params)
    return jsonify(
        {
            "plugins": response.get("data") or {},
            "response": response,
            "host": _host_snapshot(),
        }
    )


@bp.route("/studio/transport", methods=["POST"])
async def studio_transport():
    data = await _json_payload()
    action = str(data.get("action") or "").strip()
    command_map = {"play": "play", "pause": "pause", "stop": "stop", "seek": "seek"}
    if action not in command_map:
        return jsonify({"error": "action must be play, pause, stop, or seek"}), 400
    host = _host_manager()
    if not host.is_running:
        return jsonify({"error": "host process not running", "host": _host_snapshot()}), 409
    params = {}
    if action == "seek":
        params["position"] = float(data.get("position", 0.0) or 0.0)
    response = await host.send_command(command_map[action], params)
    ok = response.get("type") != "error"
    status = 200 if ok else 409
    return (
        jsonify(
            {
                "ok": ok,
                "error": response.get("message") if not ok else None,
                "response": response,
                "host": _host_snapshot(),
            }
        ),
        status,
    )


@bp.route("/studio/sync", methods=["POST"])
async def sync_studio_project():
    data = await _json_payload()
    project, state_capture = await _capture_and_save_plugin_states()
    sync = await _sync_project_to_host(
        project,
        broadcast=bool(data.get("broadcast", False)),
    )
    return jsonify({"ok": True, "sync": sync, "host": _host_snapshot(), "state": state_capture})


@bp.route("/studio/midi/write", methods=["POST"])
async def studio_midi_write():
    data = await _json_payload()
    try:
        project, summary = midi_write(
            int(data.get("track_id", 1)),
            data.get("notes") or [],
            start=data.get("start"),
            end=data.get("end"),
            mode=str(data.get("mode") or "replace"),
        )
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "summary": summary, "sync": sync})


@bp.route("/studio/midi/diff", methods=["POST"])
async def studio_midi_diff():
    data = await _json_payload()
    try:
        project, summary = midi_diff(int(data.get("track_id", 1)), data.get("operations") or [])
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "summary": summary, "sync": sync})


@bp.route("/studio/clips/diff", methods=["POST"])
async def studio_clip_diff():
    data = await _json_payload()
    try:
        project, summary = clip_diff(data.get("operations") or [])
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "summary": summary, "sync": sync})


@bp.route("/studio/automation", methods=["GET"])
async def studio_automation_query():
    include_points = str(request.args.get("include_points") or "").lower() in {
        "1",
        "true",
        "yes",
    }
    track_id_arg = request.args.get("track_id")
    track_id = int(track_id_arg) if track_id_arg else None
    return jsonify(
        {
            "ok": True,
            "automation": automation_query(track_id=track_id, include_points=include_points),
        }
    )


@bp.route("/studio/automation", methods=["POST"])
async def studio_automation_write():
    data = await _json_payload()
    target_payload = data.get("target")
    target: dict[str, Any] = target_payload if isinstance(target_payload, dict) else {}
    raw_track_id = data.get("track_id")
    try:
        track_id = None if raw_track_id in (None, "") else int(str(raw_track_id))
        project, summary = automation_write(
            target,
            points=data.get("points") if isinstance(data.get("points"), list) else [],
            name=str(data.get("name") or ""),
            track_id=track_id,
            color=str(data.get("color") or "") or None,
        )
    except (TypeError, ValueError) as e:
        message = "invalid track_id" if raw_track_id not in (None, "") else str(e)
        return jsonify({"error": message}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "summary": summary, "sync": sync})


@bp.route("/studio/automation/global", methods=["POST"])
async def studio_global_automation_write():
    data = await _json_payload()
    kind = str(data.get("kind") or "").strip().lower()
    if kind != "tempo_bpm":
        return jsonify({"error": "kind must be tempo_bpm"}), 400
    raw_track_id = data.get("track_id")
    try:
        track_id = None if raw_track_id in (None, "") else int(str(raw_track_id))
        project, summary = automation_write(
            {"kind": kind},
            points=data.get("points") if isinstance(data.get("points"), list) else [],
            name=str(data.get("name") or ""),
            track_id=track_id,
            color=str(data.get("color") or "") or None,
        )
    except (TypeError, ValueError) as e:
        message = "invalid track_id" if raw_track_id not in (None, "") else str(e)
        return jsonify({"error": message}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "summary": summary, "sync": sync})


@bp.route("/studio/automation/<int:track_id>", methods=["PATCH"])
async def studio_automation_diff(track_id: int):
    data = await _json_payload()
    try:
        project, summary = automation_diff(track_id, data.get("operations") or [])
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "summary": summary, "sync": sync})


@bp.route("/studio/automation/<int:track_id>/retarget", methods=["POST"])
async def studio_automation_retarget(track_id: int):
    data = await _json_payload()
    target_payload = data.get("target")
    target: dict[str, Any] = target_payload if isinstance(target_payload, dict) else {}
    try:
        project, summary = automation_retarget(track_id, target)
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "summary": summary, "sync": sync})


@bp.route("/studio/export", methods=["POST"])
async def studio_export_audio():
    data = await _json_payload()
    payload, status_code = await _studio_export_payload(data)
    return jsonify(payload), status_code


@bp.route("/studio/bridge/status", methods=["GET"])
async def studio_bridge_status():
    project = load_project()
    return jsonify(_bridge_status_payload(project))


@bp.route("/studio/bridge/export", methods=["POST"])
async def studio_bridge_export():
    data = await _json_payload()
    bridge_payload = {**data, "consumer": "bridge"}
    payload, status_code = await _studio_export_payload(bridge_payload)
    if payload.get("ok"):
        payload = {
            **payload,
            "bridge": _bridge_contract_payload(),
        }
    return jsonify(payload), status_code


@bp.route("/studio/bridge/export/latest", methods=["GET"])
async def studio_bridge_latest_export():
    instance_id = _bridge_export_instance_id(request.args)
    return jsonify(
        {
            "ok": True,
            "bridge": _bridge_contract_payload(),
            "export": _load_latest_bridge_export(instance_id=instance_id),
        }
    )


@bp.route("/studio/bridge/context", methods=["POST"])
async def studio_bridge_context():
    data = await _json_payload()
    try:
        context = record_bridge_host_context(data)
    except ValueError as exc:
        return jsonify({"ok": False, "bridge": _bridge_contract_payload(), "error": str(exc)}), 400
    return jsonify({"ok": True, "bridge": _bridge_contract_payload(), "context": context})


async def _studio_export_payload(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    sync: dict[str, Any] | None = None
    try:
        format_name = _normalize_export_format(data.get("format"))
        target = _normalize_export_target(data.get("target", data.get("scope")))
        consumer = _normalize_export_consumer(data.get("consumer"))
        if format_name == "midi":
            project = load_project()
            export_id = uuid4().hex[:10]
            export = _perform_midi_export(
                project,
                data,
                export_id=export_id,
                target=target,
                consumer=consumer,
            )
            _remember_bridge_export(export, consumer, data)
            sync = {
                "host_running": bool(getattr(_host_manager(), "is_running", False)),
                "skipped": True,
            }
            return _studio_export_success_payload(export, used_ffmpeg=False, sync=sync), 200
        if format_name == "dawproject":
            project = load_project()
            export_id = uuid4().hex[:10]
            export = _perform_dawproject_export(
                project,
                data,
                export_id=export_id,
                target=target,
                consumer=consumer,
            )
            _remember_bridge_export(export, consumer, data)
            sync = {
                "host_running": bool(getattr(_host_manager(), "is_running", False)),
                "skipped": True,
            }
            return _studio_export_success_payload(export, used_ffmpeg=False, sync=sync), 200

        mode = _normalize_export_mode(data.get("mode"))
        sample_rate = _normalize_export_sample_rate(data.get("sample_rate"))
        bit_depth = _normalize_export_bit_depth(data.get("bit_depth"), format_name)
        bitrate = _normalize_export_bitrate(data.get("bitrate"))
        if format_name != "wav" and not _ffmpeg_path():
            raise StudioExportError(f"ffmpeg is required for {format_name} export", 409)

        host = _host_manager()
        if not host.is_running:
            raise StudioExportError("host process not running", 409)

        project = load_project()
        host_context = _bridge_context_for_export_payload(data, consumer)
        bridge_beat_range, range_source = _bridge_beat_range_for_context(data, host_context)
        if (
            consumer == "bridge"
            and bridge_beat_range
            and not _payload_has_explicit_time_range(data)
        ):
            start, end = _bridge_seconds_range_from_beats(project, host_context, bridge_beat_range)
        else:
            start, end = _export_time_range(project, data)
        sync = await _sync_project_to_host(project, broadcast=False)
        if not sync.get("host_running"):
            raise StudioExportError("host process not running", 409)
        project = cast(dict[str, Any], sync.get("project") or project)
        export_payload = data
        if consumer == "bridge":
            resolved_target, resolved_track_ids = _bridge_midi_scope_for_payload(
                project,
                data,
                target,
                consumer,
                host_context,
            )
            if resolved_target != target or resolved_track_ids is not None:
                export_payload = {**data, "target": resolved_target}
                if resolved_track_ids is not None:
                    export_payload["track_ids"] = resolved_track_ids
                target = resolved_target
        export_tracks = _export_tracks_for_payload(project, export_payload, target)

        export_id = uuid4().hex[:10]
        export = await _perform_studio_export(
            host,
            project,
            export_tracks,
            export_id=export_id,
            mode=mode,
            target=target,
            format_name=format_name,
            sample_rate=sample_rate,
            bit_depth=bit_depth,
            bitrate=bitrate,
            start=start,
            end=end,
        )
        if consumer == "bridge":
            export["time_range_seconds"] = [float(start), float(end)]
            export["bridge_export"] = {
                "source": "bridge",
                "created_at": _bridge_created_at(),
                "range_source": range_source,
                "primary_file": export.get("path"),
            }
            selection_summary = _bridge_selection_summary(
                data,
                host_context,
                track_ids=[
                    int(track["project_track_id"])
                    for track in export.get("tracks", [])
                    if isinstance(track, dict) and track.get("project_track_id")
                ],
                beat_range=bridge_beat_range,
                range_source=range_source,
            )
            if selection_summary:
                export["selection_summary"] = selection_summary
    except StudioExportError as exc:
        return {"ok": False, "error": str(exc), "host": _host_snapshot()}, exc.status_code

    _remember_bridge_export(export, consumer, data)
    return (
        _studio_export_success_payload(export, used_ffmpeg=format_name != "wav", sync=sync),
        200,
    )


def _studio_export_success_payload(
    export: dict[str, Any],
    *,
    used_ffmpeg: bool,
    sync: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ok": True,
        "export": export,
        "exports": [export],
        "used_ffmpeg": used_ffmpeg,
        "sync": sync,
        "host": _host_snapshot(),
    }


def _remember_bridge_export(
    export: dict[str, Any],
    consumer: str,
    payload: dict[str, Any],
) -> None:
    if consumer != "bridge":
        return
    instance_id = _bridge_export_instance_id(payload)
    scoped_export = deepcopy(export)
    bridge_export = scoped_export.setdefault("bridge_export", {})
    if isinstance(bridge_export, dict):
        bridge_export.setdefault("source", "bridge")
        bridge_export.setdefault("created_at", _bridge_created_at())
        bridge_export.setdefault("range_source", "project")
        bridge_export.setdefault("primary_file", scoped_export.get("path"))
    if instance_id:
        bridge_scope = {"instance_id": instance_id}
        scoped_export["bridge_scope"] = bridge_scope
        export["bridge_scope"] = bridge_scope
        export.setdefault("bridge_export", deepcopy(bridge_export))
    _write_latest_bridge_export(scoped_export)
    if instance_id:
        _write_latest_bridge_export(scoped_export, instance_id=instance_id)


def _latest_bridge_export_path(*, instance_id: str | None = None) -> Path:
    if instance_id:
        return _audio_export_dir() / _bridge_instance_latest_export_filename(instance_id)
    return _audio_export_dir() / BRIDGE_LATEST_EXPORT_FILENAME


def _bridge_instance_latest_export_filename(instance_id: str) -> str:
    digest = hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", instance_id).strip("._-")[:48]
    return f"atri-bridge-latest-export.{slug or 'instance'}.{digest}.json"


def _load_latest_bridge_export(*, instance_id: str | None = None) -> dict[str, Any] | None:
    path = _latest_bridge_export_path(instance_id=instance_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    export = payload.get("export") if isinstance(payload, dict) else None
    if not isinstance(export, dict):
        return None
    if instance_id:
        scope = export.get("bridge_scope")
        scoped_instance_id = scope.get("instance_id") if isinstance(scope, dict) else None
        if scoped_instance_id and str(scoped_instance_id) != str(instance_id):
            return None
    primary_path = export.get("path")
    if not primary_path:
        bridge_export = export.get("bridge_export")
        if isinstance(bridge_export, dict):
            primary_path = bridge_export.get("primary_file")
    if not primary_path or not Path(str(primary_path)).exists():
        return None
    return deepcopy(export)


def _write_latest_bridge_export(
    export: dict[str, Any],
    *,
    instance_id: str | None = None,
) -> None:
    payload = json.dumps({"export": export}, ensure_ascii=False, indent=2)
    atomic_write_text(
        _latest_bridge_export_path(instance_id=instance_id),
        payload,
        prefix=".bridge_latest_export_",
    )


def _bridge_contract_payload() -> dict[str, Any]:
    return {
        "api_version": BRIDGE_API_VERSION,
        "manifest_schema_version": MIDI_SCHEMA_VERSION,
        "local_only": True,
    }


def _bridge_status_payload(project: dict[str, Any]) -> dict[str, Any]:
    formats = sorted(EXPORT_FORMATS)
    return {
        "ok": True,
        "bridge": _bridge_contract_payload(),
        "project": {
            "title": str(project.get("title") or "ATRI Session"),
            "revision": _project_revision(project),
            "summary": project_summary(project),
        },
        "formats": formats,
        "exports": {
            "formats": formats,
            "hostless_formats": ["dawproject", "midi"],
            "host_required_formats": ["flac", "mp3", "wav"],
        },
        "host": _host_snapshot(),
    }


def _perform_midi_export(
    project: dict[str, Any],
    payload: dict[str, Any],
    *,
    export_id: str,
    target: str,
    consumer: str,
) -> dict[str, Any]:
    export_dir = _audio_export_dir()
    project_stem = _safe_export_stem(project.get("title"), "ATRI Export")
    final_path = export_dir / f"{export_id}_{project_stem}.mid"
    manifest_path = export_dir / f"{export_id}_atri-export-manifest.json"
    host_context = _bridge_context_for_export_payload(payload, consumer)
    resolved_target, track_ids = _bridge_midi_scope_for_payload(
        project,
        payload,
        target,
        consumer,
        host_context,
    )

    try:
        beat_range, range_source = _bridge_beat_range_for_context(payload, host_context)
        summary = write_project_midi(
            project,
            final_path,
            track_ids=track_ids,
            beat_range=beat_range,
        )
    except (OSError, ValueError) as exc:
        raise StudioExportError(str(exc), 400) from exc

    file_entry = {
        "role": "midi",
        "path": str(final_path),
        "filename": final_path.name,
        "download_url": _export_download_url(final_path),
    }
    export: dict[str, Any] = {
        "id": export_id,
        "mode": "project",
        "target": resolved_target,
        "format": "midi",
        "path": str(final_path),
        "filename": final_path.name,
        "download_url": _export_download_url(final_path),
        "track_ids": summary["track_ids"],
        "tracks": summary["tracks"],
        "files": [file_entry],
        "summary": summary,
    }
    if "beat_range" in summary:
        export["beat_range"] = summary["beat_range"]
    selection_summary = _bridge_selection_summary(
        payload,
        host_context,
        track_ids=summary["track_ids"],
        beat_range=beat_range,
        range_source=range_source,
    )
    if selection_summary:
        export["selection_summary"] = selection_summary
    if consumer == "bridge":
        export["bridge_export"] = {
            "source": "bridge",
            "created_at": _bridge_created_at(),
            "range_source": range_source,
            "primary_file": str(final_path),
            "manifest_path": str(manifest_path),
        }
        preview = _bridge_preview_for_midi_export(
            project,
            track_ids,
            summary.get("beat_range"),
            filename=final_path.name,
            range_source=range_source,
            selection_summary=selection_summary,
        )
        if preview:
            export["bridge_preview"] = preview
    manifest = build_export_manifest(project, export, consumer=consumer)
    try:
        write_export_manifest(manifest_path, manifest)
    except OSError as exc:
        raise StudioExportError(str(exc), 400) from exc

    export["manifest_path"] = str(manifest_path)
    export["manifest"] = manifest
    return export


def _bridge_preview_for_midi_export(
    project: dict[str, Any],
    track_ids: list[int] | None,
    beat_range: Any,
    *,
    filename: str = "",
    range_source: str = "project",
    selection_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    selected_ids = set(track_ids or [])
    tracks: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict) or _is_automation_track(track):
            continue
        try:
            raw_track_id = track.get("id")
            if raw_track_id is None:
                continue
            track_id = int(raw_track_id)
        except (TypeError, ValueError):
            continue
        if selected_ids and track_id not in selected_ids:
            continue
        tracks.append(track)
    if not tracks:
        return None

    start, end = _preview_beat_range(project, beat_range)
    track_previews: list[dict[str, Any]] = []
    all_pitches: list[int] = []
    note_count = 0
    for track in tracks:
        notes = [
            note
            for note in _preview_track_notes(track)
            if note["start"] < end and note["start"] + note["duration"] > start
        ]
        pitches = [int(note["pitch"]) for note in notes]
        all_pitches.extend(pitches)
        note_count += len(notes)
        track_previews.append(
            {
                "track_id": int(track["id"]),
                "track_name": str(track.get("name") or f"Track {track['id']}"),
                "note_count": len(notes),
                "pitch_range": [min(pitches), max(pitches)] if pitches else [60, 60],
            }
        )

    if not track_previews:
        return None
    first_track = track_previews[0]
    return {
        "kind": "midi_region",
        "track_id": int(first_track["track_id"]),
        "track_name": str(first_track["track_name"]),
        "beat_range": [float(start), float(end)],
        "range_source": range_source,
        "filename": filename,
        "track_count": len(track_previews),
        "note_count": note_count,
        "pitch_range": [min(all_pitches), max(all_pitches)] if all_pitches else [60, 60],
        "tracks": track_previews,
        **({"selection": selection_summary} if selection_summary else {}),
    }


def _preview_beat_range(project: dict[str, Any], beat_range: Any) -> tuple[float, float]:
    if isinstance(beat_range, (list, tuple)) and len(beat_range) >= 2:
        try:
            start = max(0.0, float(beat_range[0]))
            end = max(start + 0.25, float(beat_range[1]))
            return start, end
        except (TypeError, ValueError):
            pass
    length = project.get("length_beats", 16)
    try:
        return 0.0, max(0.25, float(length or 16))
    except (TypeError, ValueError):
        return 0.0, 16.0


def _preview_track_notes(track: dict[str, Any]) -> list[dict[str, float | int]]:
    notes: list[dict[str, float | int]] = []
    raw_notes = track.get("notes")
    if isinstance(raw_notes, list):
        for note in raw_notes:
            normalized = _preview_note(note, clip_start=0.0)
            if normalized:
                notes.append(normalized)

    raw_clips = track.get("clips")
    if isinstance(raw_clips, list):
        for clip in raw_clips:
            if not isinstance(clip, dict) or clip.get("type") != "midi":
                continue
            try:
                clip_start = float(clip.get("start") or 0.0)
            except (TypeError, ValueError):
                clip_start = 0.0
            clip_notes = clip.get("notes")
            if not isinstance(clip_notes, list):
                continue
            for note in clip_notes:
                normalized = _preview_note(note, clip_start=clip_start)
                if normalized:
                    notes.append(normalized)
    return notes


def _preview_note(note: Any, *, clip_start: float) -> dict[str, float | int] | None:
    if not isinstance(note, dict):
        return None
    try:
        start = clip_start + float(note.get("start", note.get("beat", 0.0)) or 0.0)
        duration = max(0.001, float(note.get("duration", 0.25) or 0.25))
        pitch = max(0, min(127, round(float(note.get("pitch", 60) or 60))))
    except (TypeError, ValueError):
        return None
    return {"start": start, "duration": duration, "pitch": pitch}


def _perform_dawproject_export(
    project: dict[str, Any],
    payload: dict[str, Any],
    *,
    export_id: str,
    target: str,
    consumer: str,
) -> dict[str, Any]:
    export_dir = _audio_export_dir()
    project_stem = _safe_export_stem(project.get("title"), "ATRI Export")
    final_path = export_dir / f"{export_id}_{project_stem}.dawproject"
    host_context = _bridge_context_for_export_payload(payload, consumer)
    _resolved_target, track_ids = _bridge_midi_scope_for_payload(
        project,
        payload,
        target,
        consumer,
        host_context,
    )

    try:
        export = write_dawproject_archive(
            project,
            final_path,
            export_id=export_id,
            consumer=consumer,
            track_ids=track_ids,
            workspace_root=_cfg().get("workspace") or ".",
        )
    except (OSError, ValueError) as exc:
        raise StudioExportError(str(exc), 400) from exc

    export["download_url"] = _export_download_url(final_path)
    for file in export.get("files", []):
        if isinstance(file, dict) and file.get("role") == "dawproject":
            file["download_url"] = _export_download_url(final_path)
    if consumer == "bridge":
        export["bridge_export"] = {
            "source": "bridge",
            "created_at": _bridge_created_at(),
            "range_source": "project",
            "primary_file": str(final_path),
        }
        selection_summary = _bridge_selection_summary(
            payload,
            host_context,
            track_ids=track_ids,
            beat_range=None,
            range_source="project",
        )
        if selection_summary:
            export["selection_summary"] = selection_summary
    return export


async def _perform_studio_export(
    host: Any,
    project: dict[str, Any],
    export_tracks: list[dict[str, Any]],
    *,
    export_id: str,
    mode: str,
    target: str,
    format_name: str,
    sample_rate: int,
    bit_depth: str,
    bitrate: str,
    start: float,
    end: float,
) -> dict[str, Any]:
    export_dir = _audio_export_dir()
    project_stem = _safe_export_stem(project.get("title"), "ATRI Export")

    if mode == "mixdown":
        return await _perform_mixdown_export(
            host,
            export_dir,
            export_tracks,
            export_id=export_id,
            project_stem=project_stem,
            target=target,
            format_name=format_name,
            sample_rate=sample_rate,
            bit_depth=bit_depth,
            bitrate=bitrate,
            start=start,
            end=end,
        )

    return await _perform_stems_export(
        host,
        export_dir,
        export_tracks,
        export_id=export_id,
        project_stem=project_stem,
        target=target,
        format_name=format_name,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        bitrate=bitrate,
        start=start,
        end=end,
    )


async def _perform_mixdown_export(
    host: Any,
    export_dir: Path,
    export_tracks: list[dict[str, Any]],
    *,
    export_id: str,
    project_stem: str,
    target: str,
    format_name: str,
    sample_rate: int,
    bit_depth: str,
    bitrate: str,
    start: float,
    end: float,
) -> dict[str, Any]:
    filename = f"{export_id}_{project_stem}.{format_name}"
    final_path = export_dir / filename
    wav_path = (
        final_path if format_name == "wav" else export_dir / f"{export_id}_{project_stem}.wav"
    )
    track_ids = (
        [int(track["host_track_id"]) for track in export_tracks]
        if target == "selected_tracks"
        else None
    )

    await _render_host_wav(
        host,
        wav_path,
        start=start,
        end=end,
        track_ids=track_ids,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
    )
    if format_name != "wav":
        await _encode_export_file(
            wav_path,
            final_path,
            format_name=format_name,
            bit_depth=bit_depth,
            bitrate=bitrate,
        )
        _delete_export_file(wav_path)

    return {
        "id": export_id,
        "mode": "mixdown",
        "target": target,
        "format": format_name,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "bitrate": bitrate if format_name == "mp3" else None,
        "path": str(final_path),
        "filename": final_path.name,
        "download_url": _export_download_url(final_path),
        "track_ids": [int(track["project_track_id"]) for track in export_tracks]
        if target == "selected_tracks"
        else None,
        "files": [
            {
                "path": str(final_path),
                "filename": final_path.name,
                "download_url": _export_download_url(final_path),
            }
        ],
    }


async def _perform_stems_export(
    host: Any,
    export_dir: Path,
    export_tracks: list[dict[str, Any]],
    *,
    export_id: str,
    project_stem: str,
    target: str,
    format_name: str,
    sample_rate: int,
    bit_depth: str,
    bitrate: str,
    start: float,
    end: float,
) -> dict[str, Any]:
    zip_path = export_dir / f"{export_id}_{project_stem}_stems.zip"
    zip_names = _unique_zip_names(export_tracks, format_name)
    files: list[dict[str, Any]] = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for track in export_tracks:
            track_stem = _safe_export_stem(track.get("name"), f"Track {track['project_track_id']}")
            file_prefix = f"{export_id}_{track['project_track_id']}_{track_stem}"
            final_path = export_dir / f"{file_prefix}.{format_name}"
            wav_path = final_path if format_name == "wav" else export_dir / f"{file_prefix}.wav"

            await _render_host_wav(
                host,
                wav_path,
                start=start,
                end=end,
                track_ids=[int(track["host_track_id"])],
                sample_rate=sample_rate,
                bit_depth=bit_depth,
            )
            if format_name != "wav":
                await _encode_export_file(
                    wav_path,
                    final_path,
                    format_name=format_name,
                    bit_depth=bit_depth,
                    bitrate=bitrate,
                )
                _delete_export_file(wav_path)

            archive_name = zip_names[int(track["project_track_id"])]
            archive.write(final_path, arcname=archive_name)
            _delete_export_file(final_path)
            files.append(
                {
                    "track_id": int(track["project_track_id"]),
                    "host_track_id": int(track["host_track_id"]),
                    "name": track["name"],
                    "path": str(final_path),
                    "filename": archive_name,
                }
            )

    return {
        "id": export_id,
        "mode": "stems",
        "target": target,
        "format": format_name,
        "sample_rate": sample_rate,
        "bit_depth": bit_depth,
        "bitrate": bitrate if format_name == "mp3" else None,
        "path": str(zip_path),
        "filename": zip_path.name,
        "download_url": _export_download_url(zip_path),
        "track_ids": [int(track["project_track_id"]) for track in export_tracks],
        "files": files,
    }


@bp.route("/studio/export/download/<path:filename>", methods=["GET"])
async def studio_download_export(filename: str):
    safe_name = Path(str(filename).replace("\\", "/")).name
    if safe_name != filename:
        return jsonify({"error": "invalid export filename"}), 403
    path = _audio_export_dir() / safe_name
    if not path.exists() or not path.is_file():
        return jsonify({"error": "export not found"}), 404
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    response = await send_file(path, mimetype=mimetype, conditional=True)
    response.headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return response


@bp.route("/studio/audio/import", methods=["POST"])
async def studio_import_audio():
    form = await request.form
    files = await request.files
    uploaded = files.get("file")
    if uploaded is None:
        return jsonify({"error": "no audio file uploaded"}), 400

    original_name = str(form.get("original_name") or uploaded.filename or "Audio")
    safe_name = _safe_audio_filename(uploaded.filename or original_name)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in HOST_AUDIO_EXTS:
        return _audio_type_error("unsupported audio file type")

    saved_path = _audio_import_dir() / f"{uuid4().hex[:10]}_{safe_name}"
    try:
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        await uploaded.save(saved_path)
    except OSError as e:
        _delete_audio_import_file(saved_path)
        return jsonify({"error": str(e)}), 400
    return await _finish_audio_import(
        saved_path,
        original_name=original_name,
        start=form.get("start"),
        duration_seconds=form.get("duration_seconds"),
        waveform=_audio_waveform_from_form(form.get("waveform")),
    )


@bp.route("/studio/audio/import-file", methods=["POST"])
async def studio_import_audio_file():
    data = await _json_payload()
    raw_path = str(data.get("file_path") or data.get("path") or "").strip()
    if not raw_path:
        return jsonify({"error": "file_path is required"}), 400

    try:
        _, source_path = resolve_workspace_path(str(_cfg().get("workspace") or "."), raw_path)
    except PermissionError:
        return jsonify({"error": "path outside workspace"}), 403

    if not source_path.exists() or not source_path.is_file():
        return jsonify({"error": f"audio file not found: {raw_path}"}), 400
    if source_path.suffix.lower() not in HOST_AUDIO_EXTS:
        return _audio_type_error("unsupported audio file type")

    safe_name = _safe_audio_filename(source_path.name)
    saved_path = _audio_import_dir() / f"{uuid4().hex[:10]}_{safe_name}"
    try:
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, saved_path)
    except OSError as e:
        _delete_audio_import_file(saved_path)
        return jsonify({"error": str(e)}), 400

    original_name = str(data.get("original_name") or data.get("name") or source_path.name)
    start = data["start"] if "start" in data else data.get("start_beat")
    return await _finish_audio_import(
        saved_path,
        original_name=original_name,
        start=start,
        duration_seconds=data.get("duration_seconds"),
        waveform=_audio_waveform_from_payload(data.get("waveform")),
    )


@bp.route("/studio/import/dawproject-file", methods=["POST"])
async def studio_import_dawproject_file():
    data = await _json_payload()
    raw_path = str(data.get("file_path") or data.get("path") or "").strip()
    if not raw_path:
        return jsonify({"error": "file_path is required"}), 400

    mode = str(data.get("mode") or "replace").strip().lower()
    if mode != "replace":
        return jsonify({"error": "mode must be replace"}), 400

    try:
        _, source_path = resolve_workspace_path(str(_cfg().get("workspace") or "."), raw_path)
    except PermissionError:
        return jsonify({"error": "path outside workspace"}), 403

    if not source_path.exists() or not source_path.is_file():
        return jsonify({"error": f"DAWproject file not found: {raw_path}"}), 400
    if source_path.suffix.lower() != ".dawproject":
        return jsonify({"error": "unsupported DAWproject file type"}), 400

    try:
        project, import_summary = read_dawproject_archive(source_path)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    project = save_project_as_archive(project, activate=True)
    sync = None
    if data.get("sync", True) is not False:
        sync = await _sync_project_to_host(project, broadcast=True)
        project = sync.get("project", project)
    else:
        await _broadcast_project(project)

    return jsonify(
        {
            "ok": True,
            **_project_payload(project),
            "summary": import_summary,
            "sync": sync,
            "host": _host_snapshot(),
        }
    )


@bp.route("/studio/dawproject-snapshot/status", methods=["GET"])
async def studio_dawproject_snapshot_status():
    return jsonify(dawproject_snapshot_status())


@bp.route("/studio/dawproject-snapshot/request-export", methods=["POST"])
async def studio_dawproject_snapshot_request_export():
    data = await _json_payload()
    request_payload = request_host_dawproject_snapshot_export(
        host=str(data.get("host") or "studio_one"),
        source=str(data.get("source") or "manual"),
        instance_id=str(data.get("instance_id") or ""),
    )
    return jsonify({"ok": True, "request": request_payload})


async def _finish_audio_import(
    saved_path: Path,
    *,
    original_name: str,
    start: Any = None,
    duration_seconds: Any = None,
    waveform: list[float | dict[str, float]] | None = None,
):
    try:
        if _audio_file_missing_or_empty(saved_path):
            _delete_audio_import_file(saved_path)
            return jsonify({"error": "audio file is empty"}), 400
        project, track, clip = import_audio_clip(
            saved_path,
            name=Path(original_name.replace("\\", "/")).stem,
            start=float(start or 0.0),
            duration_seconds=_audio_duration_seconds(saved_path, duration_seconds),
            waveform=waveform or [],
        )
    except (OSError, TypeError, ValueError) as e:
        _delete_audio_import_file(saved_path)
        return jsonify({"error": str(e)}), 400

    sync = await _sync_project_to_host(project, broadcast=False)
    audio_error = _sync_audio_clip_error(sync)
    if audio_error:
        _delete_audio_import_file(saved_path)
        rollback_project = None
        try:
            rollback_project, _ = delete_project_track(int(track["id"]))
        except (KeyError, TypeError, ValueError):
            pass
        if rollback_project is not None:
            await _sync_project_to_host(rollback_project, broadcast=True)
        return _audio_type_error(audio_error, sync=sync)

    project = sync.get("project", project)
    try:
        track = find_track(project, int(track["id"]))
        clip_id = clip.get("id")
        clip = next(
            item
            for item in track.get("clips", [])
            if isinstance(item, dict) and item.get("id") == clip_id
        )
    except (StopIteration, TypeError, ValueError):
        pass
    await _broadcast_project(project)
    return jsonify(
        {"ok": True, **_project_payload(project), "track": track, "clip": clip, "sync": sync}
    )


@bp.route("/studio/tracks", methods=["POST"])
async def studio_create_track():
    data = await _json_payload()
    track_type = str(data.get("type") or data.get("track_type") or "instrument")
    default_name = "Audio Track" if track_type == "audio" else "Instrument"
    project, track = create_project_track(
        str(data.get("name") or default_name),
        color=str(data.get("color") or ""),
        track_type=track_type,
        channel_type=str(data.get("channel_type") or "multichannel"),
    )
    routing_updates = {key: data[key] for key in ("output_bus_id", "sends") if key in data}
    if routing_updates:
        project, track = update_project_track(int(track["id"]), routing_updates)
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "track": track, "sync": sync})


@bp.route("/studio/tracks/<int:track_id>", methods=["PATCH"])
async def studio_update_track(track_id: int):
    data = await _json_payload()
    try:
        project, track = update_project_track(track_id, data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "track": track, "sync": sync})


# ── Agent control endpoint (receives commands from MusicTool) ──


@bp.route("/studio/tracks/<int:track_id>", methods=["DELETE"])
async def studio_delete_track(track_id: int):
    try:
        project, track = delete_project_track(track_id)
    except ValueError as e:
        message = str(e)
        status = 400 if message == "cannot delete the last track" else 404
        return jsonify({"error": message}), status
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, **_project_payload(project), "track": track, "sync": sync})


@bp.route("/studio/tracks/<int:track_id>/plugin", methods=["POST"])
async def studio_set_track_plugin(track_id: int):
    data = await _json_payload()
    await _capture_and_save_plugin_states()
    plugin = data.get("plugin") if isinstance(data.get("plugin"), dict) else None
    slot_id = str(data.get("slot_id") or "instrument")
    try:
        project, track = set_track_plugin(track_id, plugin, slot_id=slot_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404

    load_response = None
    sync = None
    host = _host_manager()
    if host.is_running and track.get("host_track_id") is None:
        sync = await _sync_project_to_host(project, broadcast=False)
        project = sync.get("project", project)
        track = find_track(project, track_id)

    host_track_id = track.get("host_track_id")
    if host.is_running and host_track_id is not None:
        slot = _track_slot(track, slot_id)
        load_response = await _load_track_slot(host, int(host_track_id), slot)

    await _broadcast_project(project)
    return jsonify(
        {
            "ok": True,
            **_project_payload(project),
            "track": track,
            "plugin": _track_slot(track, slot_id),
            "load": load_response,
            "sync": sync,
            "host": _host_snapshot(),
        }
    )


@bp.route("/control", methods=["POST"])
async def control():
    """Receive player control commands from agent tool and broadcast via WS."""
    data = await request.get_json()
    action = data.get("action", "")
    if _lifecycle and hasattr(_lifecycle, "dashboard") and _lifecycle.dashboard:
        await _lifecycle.dashboard.broadcast(
            {
                "type": "music_control",
                "action": action,
                "payload": data.get("payload", {}),
            }
        )
    return jsonify({"ok": True, "action": action})
