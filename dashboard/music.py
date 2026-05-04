"""Music library API — scans directories, reads metadata, serves audio & artwork."""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from quart import Blueprint, jsonify, request, Response

if TYPE_CHECKING:
    from core.lifecycle import Lifecycle

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".wma", ".aiff", ".alac", ".ape", ".dsf", ".dff"}

bp = Blueprint("music", __name__, url_prefix="/api/music")

_lifecycle: "Lifecycle | None" = None


def init_music(lifecycle: "Lifecycle"):
    global _lifecycle
    _lifecycle = lifecycle


def _cfg():
    return _lifecycle.config if _lifecycle else {}


def _music_dirs() -> list[str]:
    return _cfg().get("music_directories", [])


def _cache_path() -> Path:
    p = Path("data/music_cache.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _file_id(filepath: str) -> str:
    return hashlib.md5(filepath.encode("utf-8")).hexdigest()


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
            "lossless": p.suffix.lower() in {".flac", ".wav", ".aiff", ".alac", ".ape", ".dsf", ".dff"},
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
                pass
            from mutagen.id3 import ID3
            try:
                id3 = ID3(filepath)
                info["has_cover"] = any(k.startswith("APIC") for k in id3.keys())
            except Exception:
                pass
        elif ext in (".m4a", ".aac"):
            try:
                m4 = MP4(filepath)
                tags = m4.tags or {}
                info["title"] = (tags.get("\xa9nam") or [p.stem])[0]
                info["artist"] = (tags.get("\xa9ART") or ["Unknown Artist"])[0]
                info["album"] = (tags.get("\xa9alb") or ["Unknown Album"])[0]
                tn = tags.get("trkn")
                info["track_number"] = tn[0][0] if tn else 0
                info["year"] = (tags.get("\xa9day") or [""])[0]
                info["genre"] = (tags.get("\xa9gen") or [""])[0]
                info["has_cover"] = "covr" in tags
            except Exception:
                pass
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
                pass
        elif ext == ".wav":
            try:
                w = WAVE(filepath)
                if w.tags:
                    info["title"] = str(w.tags.get("TIT2", p.stem))
            except Exception:
                pass

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
            covr = (m4.tags or {}).get("covr")
            if covr:
                return bytes(covr[0]), "image/jpeg"
    except Exception:
        pass

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
                pass

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
                return lyrics[0]
    except Exception:
        pass

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
        if not dp.exists() or not dp.is_dir():
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

    songs.sort(key=lambda s: (s["artist"].lower(), s["album"].lower(), s["track_number"], s["title"].lower()))

    try:
        _cache_path().write_text(json.dumps(songs, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return jsonify({"songs": songs, "count": len(songs)})


@bp.route("/library", methods=["GET"])
async def get_library():
    cp = _cache_path()
    if cp.exists():
        try:
            songs = json.loads(cp.read_text(encoding="utf-8"))
            return jsonify({"songs": songs, "count": len(songs)})
        except Exception:
            pass
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
    if not Path(filepath).exists():
        return jsonify({"error": "file not found"}), 404

    mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    file_size = os.path.getsize(filepath)

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


# ── Agent control endpoint (receives commands from MusicTool) ──

@bp.route("/control", methods=["POST"])
async def control():
    """Receive player control commands from agent tool and broadcast via WS."""
    data = await request.get_json()
    action = data.get("action", "")
    if _lifecycle and hasattr(_lifecycle, "dashboard") and _lifecycle.dashboard:
        await _lifecycle.dashboard.broadcast({
            "type": "music_control",
            "action": action,
            "payload": data.get("payload", {}),
        })
    return jsonify({"ok": True, "action": action})
