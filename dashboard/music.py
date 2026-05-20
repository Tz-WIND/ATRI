"""Music library API — scans directories, reads metadata, serves audio & artwork."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from quart import Blueprint, Response, jsonify, request

from core.music_project import (
    automation_diff,
    automation_learned_parameter_rename,
    automation_learned_parameter_upsert,
    automation_query,
    automation_retarget,
    automation_write,
    default_project,
    find_track,
    import_audio_clip,
    load_project,
    midi_diff,
    midi_write,
    normalize_audio_waveform,
    project_summary,
    save_project,
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

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle

AUDIO_EXTS = {
    ".mp3",
    ".flac",
    ".wav",
    ".ogg",
    ".m4a",
    ".aac",
    ".wma",
    ".aiff",
    ".alac",
    ".ape",
    ".dsf",
    ".dff",
}
HOST_AUDIO_EXTS = {".aac", ".flac", ".m4a", ".mp3", ".wav"}

bp = Blueprint("music", __name__, url_prefix="/api/music")

_lifecycle: Lifecycle | None = None

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


def _audio_type_error(message: str, **extra: Any) -> tuple[Response, int]:
    return jsonify({"type": "error", "error_type": "type_error", "error": message, **extra}), 400


def _is_in_music_dirs(filepath: str) -> bool:
    """Validate that a file path is within one of the configured music directories."""
    dirs = _music_dirs()
    if not dirs:
        return False
    try:
        resolved = Path(filepath).resolve()
    except (OSError, RuntimeError):
        return False
    for d in dirs:
        try:
            d_resolved = Path(d).resolve()
            if os.path.commonpath([resolved, d_resolved]) == str(d_resolved):
                return True
        except (OSError, ValueError):
            continue
    return False


def _file_id(filepath: str) -> str:
    return hashlib.md5(filepath.encode("utf-8")).hexdigest()  # noqa: S324


def _read_metadata(filepath: str) -> dict | None:
    try:
        import mutagen
        from mutagen.easyid3 import EasyID3
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4
        from mutagen.oggvorbis import OggVorbis
        from mutagen.wave import WAVE
    except ImportError:
        return None

    p = Path(filepath)
    if not p.exists() or p.suffix.lower() not in AUDIO_EXTS:
        return None

    try:
        audio = mutagen.File(filepath)
        if audio is None:
            return None

        info = {
            "id": _file_id(filepath),
            "path": filepath.replace("\\", "/"),
            "filename": p.name,
            "title": p.stem,
            "artist": "Unknown Artist",
            "album": "Unknown Album",
            "duration": 0,
            "track_number": 0,
            "year": "",
            "genre": "",
            "format": p.suffix.lstrip(".").upper(),
            "sample_rate": 0,
            "bit_depth": 0,
            "bitrate": 0,
            "channels": 0,
            "has_cover": False,
            "lossless": p.suffix.lower()
            in {".flac", ".wav", ".aiff", ".alac", ".ape", ".dsf", ".dff"},
        }

        if audio.info:
            info["duration"] = round(audio.info.length, 2) if hasattr(audio.info, "length") else 0
            info["sample_rate"] = getattr(audio.info, "sample_rate", 0)
            info["channels"] = getattr(audio.info, "channels", 0)
            info["bitrate"] = getattr(audio.info, "bitrate", 0)
            info["bit_depth"] = getattr(audio.info, "bits_per_sample", 0)

        ext = p.suffix.lower()
        if ext == ".flac":
            f = FLAC(filepath)
            info["title"] = (f.get("title") or [p.stem])[0]
            info["artist"] = (f.get("artist") or ["Unknown Artist"])[0]
            info["album"] = (f.get("album") or ["Unknown Album"])[0]
            info["track_number"] = int((f.get("tracknumber") or ["0"])[0].split("/")[0] or 0)
            info["year"] = (f.get("date") or [""])[0]
            info["genre"] = (f.get("genre") or [""])[0]
            info["has_cover"] = len(f.pictures) > 0
        elif ext == ".mp3":
            try:
                tags = EasyID3(filepath)
                info["title"] = (tags.get("title") or [p.stem])[0]
                info["artist"] = (tags.get("artist") or ["Unknown Artist"])[0]
                info["album"] = (tags.get("album") or ["Unknown Album"])[0]
                info["track_number"] = int((tags.get("tracknumber") or ["0"])[0].split("/")[0] or 0)
                info["year"] = (tags.get("date") or [""])[0]
                info["genre"] = (tags.get("genre") or [""])[0]
            except Exception:
                logger.debug("Music: MP3 EasyID3 tag read error", exc_info=True)
            from mutagen.id3 import ID3

            try:
                id3 = ID3(filepath)
                info["has_cover"] = any(k.startswith("APIC") for k in id3.keys())
            except Exception:
                logger.debug("Music: MP3 ID3 cover check error", exc_info=True)
        elif ext in (".m4a", ".aac"):
            try:
                m4 = MP4(filepath)
                tags = m4.tags if m4.tags is not None else {}  # type: ignore[assignment]
                info["title"] = (tags.get("\xa9nam") or [p.stem])[0]
                info["artist"] = (tags.get("\xa9ART") or ["Unknown Artist"])[0]
                info["album"] = (tags.get("\xa9alb") or ["Unknown Album"])[0]
                tn = tags.get("trkn")
                info["track_number"] = tn[0][0] if tn else 0
                info["year"] = (tags.get("\xa9day") or [""])[0]
                info["genre"] = (tags.get("\xa9gen") or [""])[0]
                info["has_cover"] = "covr" in tags
            except Exception:
                logger.debug("Music: M4A tag read error", exc_info=True)
        elif ext == ".ogg":
            try:
                ogg = OggVorbis(filepath)
                info["title"] = (ogg.get("title") or [p.stem])[0]
                info["artist"] = (ogg.get("artist") or ["Unknown Artist"])[0]
                info["album"] = (ogg.get("album") or ["Unknown Album"])[0]
                info["track_number"] = int((ogg.get("tracknumber") or ["0"])[0].split("/")[0] or 0)
                info["year"] = (ogg.get("date") or [""])[0]
                info["genre"] = (ogg.get("genre") or [""])[0]
            except Exception:
                logger.debug("Music: OGG tag read error", exc_info=True)
        elif ext == ".wav":
            try:
                w = WAVE(filepath)
                if w.tags:
                    info["title"] = str(w.tags.get("TIT2", p.stem))
            except Exception:
                logger.debug("Music: WAV tag read error", exc_info=True)

        return info
    except Exception:
        return None


def _get_cover_bytes(filepath: str) -> tuple[bytes, str] | None:
    try:
        from mutagen.flac import FLAC
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4
    except ImportError:
        return None

    p = Path(filepath)
    ext = p.suffix.lower()

    try:
        if ext == ".flac":
            f = FLAC(filepath)
            if f.pictures:
                pic = f.pictures[0]
                return pic.data, pic.mime
        elif ext == ".mp3":
            id3 = ID3(filepath)
            for key in id3.keys():
                if key.startswith("APIC"):
                    frame = id3[key]
                    return frame.data, frame.mime
        elif ext in (".m4a", ".aac"):
            m4 = MP4(filepath)
            m4_tags: dict[str, Any] = cast("dict[str, Any]", m4.tags or {})
            covr = m4_tags.get("covr")
            if covr:
                return bytes(covr[0]), "image/jpeg"
    except Exception:
        logger.debug("Music: embedded cover extraction error", exc_info=True)

    for name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png"):
        cover_file = p.parent / name
        if cover_file.exists():
            mime = "image/jpeg" if name.endswith(".jpg") else "image/png"
            return cover_file.read_bytes(), mime

    return None


def _find_lyrics(filepath: str) -> str | None:
    p = Path(filepath)
    for ext in (".lrc", ".LRC"):
        lrc = p.with_suffix(ext)
        if lrc.exists():
            try:
                return lrc.read_text(encoding="utf-8", errors="replace")
            except Exception:
                logger.debug("Music: lyrics file read error", exc_info=True)

    try:
        ext = p.suffix.lower()
        if ext == ".mp3":
            from mutagen.id3 import ID3

            id3 = ID3(filepath)
            for key in id3.keys():
                if key.startswith("USLT"):
                    return str(id3[key])
        elif ext == ".flac":
            from mutagen.flac import FLAC

            f = FLAC(filepath)
            lyrics = f.get("lyrics") or f.get("LYRICS") or f.get("unsyncedlyrics")
            if lyrics:
                return str(lyrics[0])
    except Exception:
        logger.debug("Music: embedded lyrics read error", exc_info=True)

    return None


# ── Routes ──


@bp.route("/dirs", methods=["GET"])
async def get_dirs():
    return jsonify({"directories": _music_dirs()})


@bp.route("/dirs", methods=["POST"])
async def save_dirs():
    data = await request.get_json()
    dirs = data.get("directories", [])
    _cfg()["music_directories"] = dirs
    if _lifecycle:
        _lifecycle.save_config()
    return jsonify({"ok": True})


@bp.route("/scan", methods=["POST"])
async def scan_library():
    dirs = _music_dirs()
    songs = []
    seen = set()

    for d in dirs:
        dp = Path(d)
        if not dp.exists() or not dp.is_dir():  # noqa: ASYNC240
            continue
        for root, _, files in os.walk(dp):
            for fname in files:
                fpath = os.path.join(root, fname)
                if Path(fname).suffix.lower() not in AUDIO_EXTS:
                    continue
                fid = _file_id(fpath)
                if fid in seen:
                    continue
                seen.add(fid)
                meta = _read_metadata(fpath)
                if meta:
                    songs.append(meta)

    songs.sort(
        key=lambda s: (
            s["artist"].lower(),
            s["album"].lower(),
            s["track_number"],
            s["title"].lower(),
        )
    )

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

    mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    file_size = os.path.getsize(filepath)  # noqa: ASYNC240

    range_header = request.headers.get("Range")
    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            def generate():
                with open(filepath, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk_size = min(65536, remaining)
                        data = f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return Response(
                generate(),
                status=206,
                content_type=mime,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                    "Cache-Control": "public, max-age=86400",
                },
            )

    def generate_full():
        with open(filepath, "rb") as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                yield data

    return Response(
        generate_full(),
        content_type=mime,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "public, max-age=86400",
        },
    )


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
        "running": host.is_running,
        "sample_rate": host.sample_rate,
        "buffer_size": host.buffer_size,
        "audio_engine": host.audio_engine,
        "bit_depth": host.bit_depth,
        "binary_path": host.binary_path or "",
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


def _track_slot(track: dict[str, Any], slot_id: str) -> dict[str, Any]:
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


def _instrument_slot(track: dict[str, Any]) -> dict[str, Any]:
    return _track_slot(track, "instrument")


def _slot_index(slot_id: str) -> int:
    if slot_id == "instrument":
        return 0
    match = re.fullmatch(r"insert_(\d+)", slot_id)
    if match:
        return min(255, max(1, int(match.group(1))))
    return 255


def _is_automation_track(track: dict[str, Any]) -> bool:
    return str(track.get("type") or "").strip().lower() == "automation"


def _host_track_id_for_project_target(
    project: dict[str, Any],
    target_track_id: Any,
) -> int | None:
    try:
        track = find_track(project, int(target_track_id))
    except (TypeError, ValueError):
        return None
    host_track_id = track.get("host_track_id")
    if host_track_id is None:
        return None
    return int(host_track_id)


def _host_track_id_for_project_track(
    project: dict[str, Any],
    project_track_id: object,
) -> int | None:
    try:
        wanted = int(cast(Any, project_track_id))
    except (TypeError, ValueError):
        return None
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        try:
            track_id = int(cast(Any, track.get("id", -1)))
        except (TypeError, ValueError):
            continue
        if track_id != wanted:
            continue
        host_track_id = track.get("host_track_id")
        if host_track_id is None:
            return None
        return int(host_track_id)
    return None


def _route_kind_for_host(track: dict[str, Any]) -> str:
    return "bus" if str(track.get("type") or "").strip().lower() == "bus" else "track"


def _route_output_for_host(
    project: dict[str, Any],
    track: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    output_bus_id = track.get("output_bus_id")
    if output_bus_id is None:
        return None, None
    host_output_id = _host_track_id_for_project_track(project, output_bus_id)
    if host_output_id is not None:
        return host_output_id, None
    return None, {
        "track_id": track.get("id"),
        "output_bus_id": output_bus_id,
        "reason": "output bus is not synced",
    }


def _route_sends_for_host(
    project: dict[str, Any],
    track: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sends: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for send in track.get("sends", []):
        if not isinstance(send, dict):
            continue
        target_bus_id = send.get("target_bus_id")
        host_target_id = _host_track_id_for_project_track(project, target_bus_id)
        if host_target_id is None:
            skipped.append(
                {
                    "track_id": track.get("id"),
                    "send_id": send.get("id"),
                    "target_bus_id": target_bus_id,
                    "reason": "send target bus is not synced",
                }
            )
            continue
        sends.append(
            {
                "target_track_id": host_target_id,
                "level": float(send.get("level", 1.0) or 0.0),
                "enabled": bool(send.get("enabled", True)),
            }
        )
    return sends, skipped


def _master_bus_for_host(project: dict[str, Any]) -> dict[str, Any] | None:
    master_bus = project.get("master_bus")
    if not isinstance(master_bus, dict):
        return None
    master_bus["type"] = "bus"
    master_bus["name"] = str(master_bus.get("name") or "Master Bus")
    master_bus.setdefault("volume", 1.0)
    master_bus.setdefault("pan", 0.0)
    master_bus.setdefault("mute", False)
    master_bus.setdefault("solo", False)
    master_bus.setdefault("plugin_slots", [])
    master_bus.setdefault("notes", [])
    master_bus.setdefault("midi_events", [])
    master_bus.setdefault("clips", [])
    master_bus.setdefault("sends", [])
    return master_bus


def _automation_lanes_for_host(
    project: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lanes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict) or not _is_automation_track(track):
            continue
        target_payload = track.get("target")
        target: dict[str, Any] = target_payload if isinstance(target_payload, dict) else {}
        kind = str(target.get("kind") or "")
        host_target: dict[str, Any]
        if kind in {"tempo_bpm", "time_signature_numerator"}:
            host_target = {"kind": kind}
        else:
            host_track_id = _host_track_id_for_project_target(project, target.get("track_id"))
            if host_track_id is None:
                skipped.append(
                    {"track_id": track.get("id"), "reason": "target track is not synced"}
                )
                continue
            if kind == "plugin_parameter":
                host_target = {
                    "kind": "plugin_parameter",
                    "track_id": host_track_id,
                    "slot_index": _slot_index(str(target.get("slot_id") or "instrument")),
                    "param_index": int(target.get("param_index", 0) or 0),
                }
            elif kind in {"track_volume", "track_pan"}:
                host_target = {"kind": kind, "track_id": host_track_id}
            else:
                skipped.append(
                    {"track_id": track.get("id"), "reason": "unsupported automation target"}
                )
                continue
        points = []
        for point in track.get("automation", {}).get("points", []):
            if not isinstance(point, dict):
                continue
            points.append(
                {
                    "beat": float(point.get("beat", 0.0) or 0.0),
                    "value": float(point.get("value", 0.0) or 0.0),
                    "curve": str(point.get("curve") or "linear"),
                }
            )
        lanes.append(
            {
                "target": host_target,
                "points": points,
                "muted": bool(track.get("mute", False)),
            }
        )
    return lanes, skipped


async def _load_track_slot(
    host,
    host_track_id: int,
    slot: dict[str, Any],
) -> dict[str, Any]:
    slot_id = str(slot.get("id") or "instrument")
    slot_index = _slot_index(slot_id)
    slot_type = str(slot.get("type") or "empty")

    if slot.get("type") == "vst3" and slot.get("path"):
        response = cast(
            dict[str, Any],
            await host.send_command(
                "load_vst3",
                {
                    "track_id": int(host_track_id),
                    "slot_index": slot_index,
                    "path": str(slot.get("path") or ""),
                    "name": str(slot.get("name") or "") or None,
                },
            ),
        )
        return await _restore_slot_state(host, host_track_id, slot_index, slot, response)
    if slot_type == "vst2":
        clear_response = await host.send_command(
            "clear_processor_slot",
            {"track_id": int(host_track_id), "slot_index": slot_index},
        )
        return {
            "type": "error",
            "cmd": "load_vst2",
            "slot_id": slot_id,
            "slot_index": slot_index,
            "message": "VST2 scan is available, but VST2 loading is not implemented yet",
            "clear": clear_response,
        }
    if slot_id == "instrument":
        response = cast(
            dict[str, Any],
            await host.send_command(
                "load_builtin_synth",
                {"track_id": int(host_track_id), "slot_index": slot_index},
            ),
        )
        return await _restore_slot_state(host, host_track_id, slot_index, slot, response)
    return cast(
        dict[str, Any],
        await host.send_command(
            "clear_processor_slot",
            {"track_id": int(host_track_id), "slot_index": slot_index},
        ),
    )


async def _restore_slot_state(
    host,
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


async def _load_track_slots(
    host,
    host_track_id: int,
    track: dict[str, Any],
) -> list[dict[str, Any]]:
    if track.get("type") == "audio":
        return []
    raw_slots = track.get("plugin_slots")
    if track.get("type") == "bus":
        slots = raw_slots if isinstance(raw_slots, list) else []
    else:
        slots = (
            raw_slots if isinstance(raw_slots, list) and raw_slots else [_instrument_slot(track)]
        )
    commands = []
    for slot in slots:
        if isinstance(slot, dict):
            commands.append(await _load_track_slot(host, host_track_id, slot))
    return commands


async def _capture_plugin_states(
    project: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    host = _host_manager()
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
            slot_index = _slot_index(slot_id)
            response = await host.send_command(
                "get_plugin_state",
                {"track_id": int(host_track_id), "slot_index": slot_index},
            )
            responses.append(
                {
                    "track_id": track.get("id"),
                    "host_track_id": int(host_track_id),
                    "slot_id": slot_id,
                    "slot_index": slot_index,
                    "response": response,
                }
            )
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            state_b64 = str(data.get("state_b64") or "")
            if state_b64:
                slot["state_b64"] = state_b64

    return project, responses


async def _capture_and_save_plugin_states(
    project: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    project = project if isinstance(project, dict) else load_project()
    project, responses = await _capture_plugin_states(project)
    if responses:
        project = save_project(project)
    return project, responses


async def open_plugin_editor_for_track(
    track_id: int,
    *,
    slot_id: str = "instrument",
) -> tuple[dict[str, Any], int]:
    project = load_project()
    try:
        track = find_track(project, track_id)
    except ValueError as e:
        return {"ok": False, "error": str(e), "host": _host_snapshot()}, 404

    host = _host_manager()
    if not host.is_running:
        return {"ok": False, "error": "host process not running", "host": _host_snapshot()}, 409

    sync = None
    if track.get("host_track_id") is None:
        sync = await _sync_project_to_host(project, broadcast=True)
        project = sync.get("project", project)
        track = find_track(project, track_id)

    host_track_id = track.get("host_track_id")
    if host_track_id is None:
        return {
            "ok": False,
            "error": "track is not synced to the host",
            "host": _host_snapshot(),
            "sync": sync,
        }, 409

    slot = _track_slot(track, slot_id)
    if slot.get("type") in {"empty", "vst2"}:
        return {
            "ok": False,
            "error": "selected plugin slot does not have a native editor",
            "host": _host_snapshot(),
            "plugin": slot,
            "sync": sync,
        }, 409

    slot_index = _slot_index(slot_id)
    response = await host.send_command(
        "open_plugin_editor",
        {"track_id": int(host_track_id), "slot_index": slot_index},
    )
    ok = response.get("type") == "ack"
    status = 200 if ok else 409
    return {
        "ok": ok,
        "project_track_id": int(track_id),
        "host_track_id": int(host_track_id),
        "slot_id": slot_id,
        "slot_index": slot_index,
        "plugin": slot,
        "response": response,
        "sync": sync,
        "host": _host_snapshot(),
    }, status


async def _broadcast_project(project: dict[str, Any]) -> None:
    dashboard = getattr(_lifecycle, "dashboard", None) if _lifecycle else None
    if dashboard:
        await dashboard.broadcast(
            {
                "type": "music_project",
                "project": project,
                "summary": project_summary(project),
            }
        )


async def _sync_project_to_host(
    project: dict[str, Any],
    *,
    broadcast: bool = False,
) -> dict[str, Any]:
    host = _host_manager()
    if not host.is_running:
        if broadcast:
            await _broadcast_project(project)
        return {"host_running": False, "commands": []}

    commands: list[dict[str, Any]] = []
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
    commands.append(
        await host.send_command(
            "set_tempo",
            {"bpm": float(project.get("tempo", 120.0)), "time_sig": meter},
        )
    )

    for stale_host_track_id in sorted(host_track_ids - project_host_track_ids):
        response = await host.send_command("remove_track", {"id": stale_host_track_id})
        commands.append(response)
        if response.get("type") != "error":
            host_track_ids.remove(stale_host_track_id)

    project_changed = False
    routing_skipped: list[dict[str, Any]] = []
    routing_routes = 0
    route_tracks: list[dict[str, Any]] = []
    master_slots_loaded = False
    for track in project.get("tracks", []):
        if not isinstance(track, dict):
            continue
        if _is_automation_track(track):
            track["host_track_id"] = None
            continue
        host_track_id = track.get("host_track_id")
        if host_track_id is None or int(host_track_id) not in host_track_ids:
            response = await host.send_command("add_track", {"name": track.get("name", "Track")})
            commands.append(response)
            host_track_id = _response_data(response).get("track_id")
            if host_track_id is None:
                continue
            track["host_track_id"] = int(host_track_id)
            host_track_ids.add(int(host_track_id))
            project_changed = True
            commands.extend(await _load_track_slots(host, int(host_track_id), track))

        host_track_id = int(host_track_id)
        route_tracks.append(track)

    if master_bus is not None:
        host_track_id = master_bus.get("host_track_id")
        if host_track_id is None or int(host_track_id) not in host_track_ids:
            response = await host.send_command(
                "add_track",
                {"name": master_bus.get("name", "Master Bus")},
            )
            commands.append(response)
            host_track_id = _response_data(response).get("track_id")
            if host_track_id is not None:
                master_bus["host_track_id"] = int(host_track_id)
                host_track_ids.add(int(host_track_id))
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

    for track in route_tracks:
        host_track_id = int(track["host_track_id"])
        commands.append(
            await host.send_command(
                "set_route_config",
                {
                    "track_id": host_track_id,
                    "kind": _route_kind_for_host(track),
                    "output_track_id": None,
                },
            )
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
        commands.append(
            await host.send_command(
                "set_route_config",
                {
                    "track_id": host_track_id,
                    "kind": None,
                    "output_track_id": output_track_id,
                },
            )
        )
        route_sends, send_skips = _route_sends_for_host(project, track)
        routing_skipped.extend(send_skips)
        commands.append(
            await host.send_command(
                "set_route_sends",
                {
                    "track_id": host_track_id,
                    "sends": route_sends,
                },
            )
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
        midi_events = [
            {
                key: event[key]
                for key in (
                    "type",
                    "start",
                    "channel",
                    "pitch",
                    "velocity",
                    "controller",
                    "value",
                    "program",
                    "pressure",
                    "data_b64",
                )
                if key in event
            }
            for event in track.get("midi_events", [])
            if isinstance(event, dict)
        ]
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
        commands.append(
            await host.send_command(
                "set_midi",
                {"track_id": host_track_id, "notes": notes, "events": midi_events},
            )
        )
        commands.append(
            await host.send_command(
                "set_audio_clips",
                {"track_id": host_track_id, "clips": audio_clips},
            )
        )
        commands.append(
            await host.send_command(
                "set_volume",
                {"track_id": host_track_id, "value": float(track.get("volume", 0.8))},
            )
        )
        commands.append(
            await host.send_command(
                "set_pan",
                {"track_id": host_track_id, "value": float(track.get("pan", 0.0))},
            )
        )
        commands.append(
            await host.send_command(
                "set_mute",
                {"track_id": host_track_id, "value": bool(track.get("mute", False))},
            )
        )
        commands.append(
            await host.send_command(
                "set_solo",
                {"track_id": host_track_id, "value": bool(track.get("solo", False))},
            )
        )

    automation_lanes, skipped_automation = _automation_lanes_for_host(project)
    commands.append(
        await host.send_command(
            "set_automation",
            {"lanes": automation_lanes},
        )
    )

    if project_changed:
        project = save_project(project)
    if broadcast:
        await _broadcast_project(project)

    return {
        "host_running": True,
        "commands": commands,
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
    return jsonify(
        {
            "project": project,
            "summary": project_summary(project),
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
    return jsonify({"ok": True, "project": project, "sync": sync, "state": state_capture})


@bp.route("/studio/demo", methods=["POST"])
async def reset_studio_demo():
    await _json_payload()
    project = save_project(default_project())
    sync = await _sync_project_to_host(
        project,
        broadcast=True,
    )
    return jsonify({"ok": True, "project": project, "sync": sync})


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
    return (
        jsonify(
            {
                "ok": ok,
                "error": response.get("message") if not ok else None,
                "response": response,
                "host": _host_snapshot(),
            }
        ),
        200 if ok else 409,
    )


def _slot_id_from_index(slot_index: int) -> str:
    if slot_index <= 0:
        return "instrument"
    return f"insert_{slot_index}"


def _captured_parameter_for_project(
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
    slot_index = int(captured.get("slot_index", 0) or 0)
    slot_id = _slot_id_from_index(slot_index)
    slot = _track_slot(project_track, slot_id)
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
            "slot_label": "Instrument" if slot_id == "instrument" else f"Insert {slot_index}",
            "plugin_name": str(captured.get("plugin_name") or slot.get("name") or "Plugin"),
            "param_name": param_name,
            "units": str(captured.get("units") or ""),
        },
        "value": float(captured.get("value", 0.0) or 0.0),
    }


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
                        "project": project,
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
            "project": project,
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
    return jsonify({"ok": True, "project": project, "learned_parameter": learned})


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
    return jsonify({"ok": True, "project": project, "summary": summary, "sync": sync})


@bp.route("/studio/midi/diff", methods=["POST"])
async def studio_midi_diff():
    data = await _json_payload()
    try:
        project, summary = midi_diff(int(data.get("track_id", 1)), data.get("operations") or [])
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, "project": project, "summary": summary, "sync": sync})


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
    track_id = None if raw_track_id in (None, "") else int(str(raw_track_id))
    try:
        project, summary = automation_write(
            target,
            points=data.get("points") if isinstance(data.get("points"), list) else [],
            name=str(data.get("name") or ""),
            track_id=track_id,
            color=str(data.get("color") or "") or None,
        )
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, "project": project, "summary": summary, "sync": sync})


@bp.route("/studio/automation/<int:track_id>", methods=["PATCH"])
async def studio_automation_diff(track_id: int):
    data = await _json_payload()
    try:
        project, summary = automation_diff(track_id, data.get("operations") or [])
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, "project": project, "summary": summary, "sync": sync})


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
    return jsonify({"ok": True, "project": project, "summary": summary, "sync": sync})


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
        await uploaded.save(saved_path)
        if not saved_path.exists() or saved_path.stat().st_size == 0:
            saved_path.unlink(missing_ok=True)
            return jsonify({"error": "audio file is empty"}), 400
        duration_seconds = _audio_duration_seconds(saved_path, form.get("duration_seconds"))
        start = float(form.get("start") or 0.0)
        waveform = _audio_waveform_from_form(form.get("waveform"))
        project, track, clip = import_audio_clip(
            saved_path,
            name=Path(original_name.replace("\\", "/")).stem,
            start=start,
            duration_seconds=duration_seconds,
            waveform=waveform,
        )
    except (OSError, TypeError, ValueError) as e:
        try:
            saved_path.unlink(missing_ok=True)
        except OSError:
            pass
        return jsonify({"error": str(e)}), 400

    sync = await _sync_project_to_host(project, broadcast=False)
    audio_error = _sync_audio_clip_error(sync)
    if audio_error:
        try:
            saved_path.unlink(missing_ok=True)
        except OSError:
            pass
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
    return jsonify({"ok": True, "project": project, "track": track, "clip": clip, "sync": sync})


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
    return jsonify({"ok": True, "project": project, "track": track, "sync": sync})


@bp.route("/studio/tracks/<int:track_id>", methods=["PATCH"])
async def studio_update_track(track_id: int):
    data = await _json_payload()
    try:
        project, track = update_project_track(track_id, data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    sync = await _sync_project_to_host(project, broadcast=True)
    return jsonify({"ok": True, "project": project, "track": track, "sync": sync})


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
    return jsonify({"ok": True, "project": project, "track": track, "sync": sync})


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
            "project": project,
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
