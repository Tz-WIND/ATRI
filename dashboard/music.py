"""Music library API — scans directories, reads metadata, serves audio & artwork."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import zipfile
from copy import deepcopy
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast
from uuid import uuid4
from weakref import WeakKeyDictionary

from quart import Blueprint, Response, jsonify, request, send_file

from core.music_export import (
    MIDI_SCHEMA_VERSION,
    build_export_manifest,
    write_dawproject_archive,
    write_export_manifest,
    write_project_midi,
)
from core.music_project import (
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
    load_project,
    midi_diff,
    midi_write,
    normalize_audio_waveform,
    normalize_project,
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
from dashboard.routes._helpers import resolve_workspace_path

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
EXPORT_FORMATS = {"wav", "flac", "mp3", "midi", "dawproject"}
EXPORT_BIT_DEPTHS = {"i16", "i24", "f32"}
EXPORT_SAMPLE_RATES = {44100, 48000, 88200, 96000, 192000}
EXPORT_BITRATES = {"128k", "192k", "256k", "320k"}
RAW_HOST_COMMAND_DENYLIST = {"bounce", "render_wav"}
BRIDGE_API_VERSION = 1

bp = Blueprint("music", __name__, url_prefix="/api/music")

_lifecycle: Lifecycle | None = None
_project_broadcast_snapshot: dict[str, Any] | None = None
_project_broadcast_revision: str | None = None
_HOST_SYNC_SESSION_KEY = "__session__"
_host_sync_fingerprints: WeakKeyDictionary[object, dict[str, str]] = WeakKeyDictionary()
_host_sync_fingerprints_by_id: dict[int, dict[str, str]] = {}

logger = logging.getLogger(__name__)

CURVE_SAMPLE_STEP_BEATS = 1.0 / 64.0
CURVE_MAX_SAMPLES_PER_SEGMENT = 4096


class _HostAutomationPoint(TypedDict):
    beat: float
    value: float
    curve: str
    curve_amount: Any


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


def _safe_export_stem(value: Any, fallback: str = "ATRI Export") -> str:
    stem = Path(_safe_audio_filename(str(value or fallback))).stem.strip(" ._")
    return stem or fallback


class StudioExportError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


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


def _normalize_export_format(value: Any) -> str:
    format_name = str(value or "wav").strip().lower().lstrip(".")
    if format_name not in EXPORT_FORMATS:
        raise StudioExportError("format is not supported", 400)
    return format_name


def _normalize_export_mode(value: Any) -> str:
    mode = str(value or "mixdown").strip().lower()
    if mode not in {"mixdown", "stems"}:
        raise StudioExportError("mode must be mixdown or stems", 400)
    return mode


def _normalize_export_target(value: Any) -> str:
    target = str(value or "entire_project").strip().lower()
    aliases = {
        "project": "entire_project",
        "all": "entire_project",
        "all_tracks": "entire_project",
        "selected": "selected_tracks",
        "tracks": "selected_tracks",
    }
    target = aliases.get(target, target)
    if target not in {"entire_project", "selected_tracks"}:
        raise StudioExportError("target must be entire_project or selected_tracks", 400)
    return target


def _normalize_export_sample_rate(value: Any) -> int:
    try:
        sample_rate = int(value or 48000)
    except (TypeError, ValueError):
        raise StudioExportError(
            "sample_rate must be one of 44100, 48000, 88200, 96000, 192000"
        ) from None
    if sample_rate not in EXPORT_SAMPLE_RATES:
        raise StudioExportError("sample_rate must be one of 44100, 48000, 88200, 96000, 192000")
    return sample_rate


def _normalize_export_bit_depth(value: Any, format_name: str) -> str:
    bit_depth = str(value or "i24").strip().lower()
    if bit_depth not in EXPORT_BIT_DEPTHS:
        raise StudioExportError("bit_depth must be i16, i24, or f32")
    if format_name == "flac" and bit_depth == "f32":
        raise StudioExportError("flac export requires i16 or i24 bit_depth")
    return bit_depth


def _normalize_export_bitrate(value: Any) -> str:
    if value is None or value == "":
        return "320k"
    if isinstance(value, int):
        bitrate = f"{value}k"
    else:
        bitrate = str(value).strip().lower()
        if bitrate.isdigit():
            bitrate = f"{bitrate}k"
    if bitrate not in EXPORT_BITRATES:
        raise StudioExportError("bitrate must be 128k, 192k, 256k, or 320k")
    return bitrate


def _project_length_seconds(project: dict[str, Any]) -> float:
    try:
        length_beats = max(0.0, float(project.get("length_beats", 16.0) or 0.0))
    except (TypeError, ValueError):
        length_beats = 16.0
    try:
        tempo = max(1.0, float(project.get("tempo", 120.0) or 120.0))
    except (TypeError, ValueError):
        tempo = 120.0
    return length_beats * 60.0 / tempo


def _export_time_range(project: dict[str, Any], payload: dict[str, Any]) -> tuple[float, float]:
    try:
        start = float(payload.get("start", payload.get("start_seconds", 0.0)) or 0.0)
        end_raw = payload.get("end", payload.get("end_seconds"))
        end = float(end_raw) if end_raw is not None else _project_length_seconds(project)
    except (TypeError, ValueError):
        raise StudioExportError("start and end must be numbers") from None
    if start < 0:
        raise StudioExportError("start must be non-negative")
    if end <= start:
        raise StudioExportError("end must be after start")
    return start, end


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


def _normalize_export_consumer(value: Any) -> str:
    consumer = str(value or "export").strip().lower()
    return consumer if consumer in {"export", "bridge"} else "export"


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


def _curve_amount(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not parsed or parsed != parsed:
        return 0.0
    return round(max(-1.0, min(1.0, parsed)), 6)


def _curve_sample_beats(start: float, end: float) -> list[float]:
    if end <= start:
        return []
    step = CURVE_SAMPLE_STEP_BEATS
    estimated = int((end - start) / step)
    if estimated > CURVE_MAX_SAMPLES_PER_SEGMENT:
        step = (end - start) / CURVE_MAX_SAMPLES_PER_SEGMENT
    beats: list[float] = []
    beat = start + step
    while beat < end - 1e-9:
        beats.append(round(beat, 6))
        beat += step
    return beats


def _curve_value(
    start_value: float,
    end_value: float,
    beat: float,
    start_beat: float,
    end_beat: float,
    curve_amount: float,
    minimum: float,
    maximum: float,
) -> float:
    value_range = max(1e-9, maximum - minimum)
    position = max(0.0, min(1.0, (beat - start_beat) / max(1e-9, end_beat - start_beat)))
    start_unit = max(0.0, min(1.0, (start_value - minimum) / value_range))
    end_unit = max(0.0, min(1.0, (end_value - minimum) / value_range))
    linear_unit = start_unit + (end_unit - start_unit) * position
    bend = 4.0 * position * (1.0 - position) * _curve_amount(curve_amount)
    return minimum + max(0.0, min(1.0, linear_unit + bend)) * value_range


def _midi_curve_lane_key(event: dict[str, Any]) -> tuple[Any, ...] | None:
    event_type = str(event.get("type") or event.get("kind") or "").strip().lower()
    event_type = event_type.replace("-", "_").replace(" ", "_")
    if event_type in {"cc", "controller"}:
        event_type = "control_change"
    elif event_type in {"pitchbend"}:
        event_type = "pitch_bend"
    elif event_type in {"aftertouch", "after_touch"}:
        event_type = "channel_pressure"
    if event_type == "control_change":
        return (event_type, int(event.get("channel", 0) or 0), int(event.get("controller", 0) or 0))
    if event_type in {"pitch_bend", "channel_pressure"}:
        return (event_type, int(event.get("channel", 0) or 0))
    if event_type == "polyphonic_key_pressure":
        return (
            event_type,
            int(event.get("channel", 0) or 0),
            int(event.get("pitch", 60) or 60),
        )
    return None


def _midi_curve_value_field(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "").strip().lower()
    return "pressure" if event_type in {"channel_pressure", "polyphonic_key_pressure"} else "value"


def _midi_curve_bounds(event: dict[str, Any]) -> tuple[float, float]:
    return (-8192.0, 8191.0) if str(event.get("type") or "") == "pitch_bend" else (0.0, 127.0)


def _midi_curve_value(event: dict[str, Any]) -> float:
    field = _midi_curve_value_field(event)
    return float(event.get(field, event.get("value", 0)) or 0)


HOST_MIDI_EVENT_KEYS = (
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


def _host_midi_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: event[key] for key in HOST_MIDI_EVENT_KEYS if key in event}


def _midi_events_for_host(track: dict[str, Any]) -> list[dict[str, Any]]:
    events = [
        {**_host_midi_event(event), "curve_amount": event.get("curve_amount")}
        for event in track.get("midi_events", [])
        if isinstance(event, dict)
    ]
    expanded = [_host_midi_event(event) for event in events]
    lane_events: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for event in events:
        lane_key = _midi_curve_lane_key(event)
        if lane_key is not None:
            lane_events.setdefault(lane_key, []).append(event)

    for lane in lane_events.values():
        lane.sort(key=lambda event: float(event.get("start", 0.0) or 0.0))
        occupied = {round(float(event.get("start", 0.0) or 0.0), 6) for event in lane}
        for left, right in pairwise(lane):
            start = float(left.get("start", 0.0) or 0.0)
            end = float(right.get("start", 0.0) or 0.0)
            curve_amount = _curve_amount(left.get("curve_amount"))
            if end <= start or (
                abs(_midi_curve_value(left) - _midi_curve_value(right)) < 1e-9
                and abs(curve_amount) < 1e-9
            ):
                continue
            minimum, maximum = _midi_curve_bounds(left)
            value_field = _midi_curve_value_field(left)
            for beat in _curve_sample_beats(start, end):
                if beat in occupied:
                    continue
                value = round(
                    _curve_value(
                        _midi_curve_value(left),
                        _midi_curve_value(right),
                        beat,
                        start,
                        end,
                        curve_amount,
                        minimum,
                        maximum,
                    )
                )
                sampled = _host_midi_event(left)
                sampled["start"] = beat
                sampled[value_field] = int(max(minimum, min(maximum, value)))
                expanded.append(sampled)

    return sorted(
        expanded,
        key=lambda event: (
            float(event.get("start", 0.0) or 0.0),
            str(event.get("type") or ""),
            int(event.get("controller", event.get("pitch", -1)) or -1),
        ),
    )


def _automation_points_for_host(track: dict[str, Any]) -> list[dict[str, Any]]:
    automation = track.get("automation", {})
    value_min = float(automation.get("value_min", 0.0) or 0.0)
    value_max = float(automation.get("value_max", 1.0) or 1.0)
    if value_max < value_min:
        value_min, value_max = value_max, value_min
    raw_points: list[_HostAutomationPoint] = [
        {
            "beat": float(point.get("beat", 0.0) or 0.0),
            "value": float(point.get("value", 0.0) or 0.0),
            "curve": str(point.get("curve") or "linear"),
            "curve_amount": point.get("curve_amount"),
        }
        for point in automation.get("points", [])
        if isinstance(point, dict)
    ]
    raw_points.sort(key=lambda point: point["beat"])
    points: list[dict[str, Any]] = [
        {"beat": point["beat"], "value": point["value"], "curve": point["curve"]}
        for point in raw_points
    ]
    for left, right in pairwise(raw_points):
        curve_amount = _curve_amount(left.get("curve_amount"))
        if abs(curve_amount) < 1e-9 or left["curve"] == "hold" or right["beat"] <= left["beat"]:
            continue
        for beat in _curve_sample_beats(left["beat"], right["beat"]):
            points.append(
                {
                    "beat": beat,
                    "value": round(
                        _curve_value(
                            left["value"],
                            right["value"],
                            beat,
                            left["beat"],
                            right["beat"],
                            curve_amount,
                            value_min,
                            value_max,
                        ),
                        6,
                    ),
                    "curve": "linear",
                }
            )
    return sorted(points, key=lambda point: point["beat"])


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
        if kind == "tempo_bpm":
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
        lanes.append(
            {
                "target": host_target,
                "points": _automation_points_for_host(track),
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
    return {"project": project, "revision": _project_revision(project)}


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
    return jsonify({"ok": True, **_project_payload(project), "sync": sync, "state": state_capture})


@bp.route("/studio/demo", methods=["POST"])
async def reset_studio_demo():
    await _json_payload()
    project = save_project(default_project())
    sync = await _sync_project_to_host(
        project,
        broadcast=True,
    )
    return jsonify({"ok": True, **_project_payload(project), "sync": sync})


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


async def _studio_export_payload(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
    sync: dict[str, Any] | None = None
    try:
        format_name = _normalize_export_format(data.get("format"))
        target = _normalize_export_target(data.get("target", data.get("scope")))
        if format_name == "midi":
            project = load_project()
            export_id = uuid4().hex[:10]
            export = _perform_midi_export(
                project,
                data,
                export_id=export_id,
                target=target,
                consumer=_normalize_export_consumer(data.get("consumer")),
            )
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
                consumer=_normalize_export_consumer(data.get("consumer")),
            )
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
        start, end = _export_time_range(project, data)
        sync = await _sync_project_to_host(project, broadcast=False)
        if not sync.get("host_running"):
            raise StudioExportError("host process not running", 409)
        project = cast(dict[str, Any], sync.get("project") or project)
        export_tracks = _export_tracks_for_payload(project, data, target)

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
    except StudioExportError as exc:
        return {"ok": False, "error": str(exc), "host": _host_snapshot()}, exc.status_code

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


def _bridge_contract_payload() -> dict[str, Any]:
    return {
        "api_version": BRIDGE_API_VERSION,
        "manifest_schema_version": MIDI_SCHEMA_VERSION,
        "local_only": True,
    }


def _bridge_status_payload(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "bridge": _bridge_contract_payload(),
        "project": {
            "title": str(project.get("title") or "ATRI Session"),
            "revision": _project_revision(project),
            "summary": project_summary(project),
        },
        "exports": {
            "formats": sorted(EXPORT_FORMATS),
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
    track_ids = _midi_track_ids_for_payload(project, payload, target)

    try:
        summary = write_project_midi(project, final_path, track_ids=track_ids)
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
        "target": target,
        "format": "midi",
        "path": str(final_path),
        "filename": final_path.name,
        "download_url": _export_download_url(final_path),
        "track_ids": summary["track_ids"],
        "tracks": summary["tracks"],
        "files": [file_entry],
        "summary": summary,
    }
    manifest = build_export_manifest(project, export, consumer=consumer)
    manifest_path = export_dir / f"{export_id}_atri-export-manifest.json"
    try:
        write_export_manifest(manifest_path, manifest)
    except OSError as exc:
        raise StudioExportError(str(exc), 400) from exc

    export["manifest_path"] = str(manifest_path)
    export["manifest"] = manifest
    return export


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
    track_ids = _midi_track_ids_for_payload(project, payload, target)

    try:
        export = write_dawproject_archive(
            project,
            final_path,
            export_id=export_id,
            consumer=consumer,
            track_ids=track_ids,
        )
    except (OSError, ValueError) as exc:
        raise StudioExportError(str(exc), 400) from exc

    export["download_url"] = _export_download_url(final_path)
    for file in export.get("files", []):
        if isinstance(file, dict) and file.get("role") == "dawproject":
            file["download_url"] = _export_download_url(final_path)
    return export


def _midi_track_ids_for_payload(
    project: dict[str, Any],
    payload: dict[str, Any],
    target: str,
) -> list[int] | None:
    if target == "entire_project":
        return None
    raw_track_ids = payload.get("track_ids")
    if not isinstance(raw_track_ids, list) or not raw_track_ids:
        raise StudioExportError("track_ids is required for selected_tracks export")
    track_ids: list[int] = []
    for raw_track_id in raw_track_ids:
        try:
            track = find_track(project, int(raw_track_id))
        except (TypeError, ValueError) as exc:
            raise StudioExportError(f"track not found: {raw_track_id}", 404) from exc
        if _is_automation_track(track):
            raise StudioExportError(f"track is not exportable: {raw_track_id}", 400)
        track_ids.append(int(track["id"]))
    return track_ids


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
