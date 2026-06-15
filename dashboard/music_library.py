"""Music library scanning and metadata helpers."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, cast

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

logger = logging.getLogger(__name__)


def is_in_music_dirs(filepath: str, music_dirs: list[str]) -> bool:
    """Validate that a file path is within one of the configured music directories."""
    if not music_dirs:
        return False
    try:
        resolved = Path(filepath).resolve()
    except (OSError, RuntimeError):
        return False
    for directory in music_dirs:
        try:
            directory_resolved = Path(directory).resolve()
            if os.path.commonpath([resolved, directory_resolved]) == str(directory_resolved):
                return True
        except (OSError, ValueError):
            continue
    return False


def file_id(filepath: str) -> str:
    return hashlib.md5(filepath.encode("utf-8")).hexdigest()  # noqa: S324


def read_metadata(filepath: str) -> dict[str, Any] | None:
    try:
        import mutagen
        from mutagen.easyid3 import EasyID3
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4
        from mutagen.oggvorbis import OggVorbis
        from mutagen.wave import WAVE
    except ImportError:
        return None

    path = Path(filepath)
    if not path.exists() or path.suffix.lower() not in AUDIO_EXTS:
        return None

    try:
        audio = mutagen.File(filepath)
        if audio is None:
            return None

        info: dict[str, Any] = {
            "id": file_id(filepath),
            "path": filepath.replace("\\", "/"),
            "filename": path.name,
            "title": path.stem,
            "artist": "Unknown Artist",
            "album": "Unknown Album",
            "duration": 0,
            "track_number": 0,
            "year": "",
            "genre": "",
            "format": path.suffix.lstrip(".").upper(),
            "sample_rate": 0,
            "bit_depth": 0,
            "bitrate": 0,
            "channels": 0,
            "has_cover": False,
            "lossless": path.suffix.lower()
            in {".flac", ".wav", ".aiff", ".alac", ".ape", ".dsf", ".dff"},
        }

        if audio.info:
            info["duration"] = round(audio.info.length, 2) if hasattr(audio.info, "length") else 0
            info["sample_rate"] = getattr(audio.info, "sample_rate", 0)
            info["channels"] = getattr(audio.info, "channels", 0)
            info["bitrate"] = getattr(audio.info, "bitrate", 0)
            info["bit_depth"] = getattr(audio.info, "bits_per_sample", 0)

        ext = path.suffix.lower()
        if ext == ".flac":
            flac = FLAC(filepath)
            info["title"] = (flac.get("title") or [path.stem])[0]
            info["artist"] = (flac.get("artist") or ["Unknown Artist"])[0]
            info["album"] = (flac.get("album") or ["Unknown Album"])[0]
            info["track_number"] = int((flac.get("tracknumber") or ["0"])[0].split("/")[0] or 0)
            info["year"] = (flac.get("date") or [""])[0]
            info["genre"] = (flac.get("genre") or [""])[0]
            info["has_cover"] = len(flac.pictures) > 0
        elif ext == ".mp3":
            try:
                tags = EasyID3(filepath)
                info["title"] = (tags.get("title") or [path.stem])[0]
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
                info["has_cover"] = any(key.startswith("APIC") for key in id3.keys())
            except Exception:
                logger.debug("Music: MP3 ID3 cover check error", exc_info=True)
        elif ext in (".m4a", ".aac"):
            try:
                mp4 = MP4(filepath)
                tags = mp4.tags if mp4.tags is not None else {}  # type: ignore[assignment]
                info["title"] = (tags.get("\xa9nam") or [path.stem])[0]
                info["artist"] = (tags.get("\xa9ART") or ["Unknown Artist"])[0]
                info["album"] = (tags.get("\xa9alb") or ["Unknown Album"])[0]
                track_number = tags.get("trkn")
                info["track_number"] = track_number[0][0] if track_number else 0
                info["year"] = (tags.get("\xa9day") or [""])[0]
                info["genre"] = (tags.get("\xa9gen") or [""])[0]
                info["has_cover"] = "covr" in tags
            except Exception:
                logger.debug("Music: M4A tag read error", exc_info=True)
        elif ext == ".ogg":
            try:
                ogg = OggVorbis(filepath)
                info["title"] = (ogg.get("title") or [path.stem])[0]
                info["artist"] = (ogg.get("artist") or ["Unknown Artist"])[0]
                info["album"] = (ogg.get("album") or ["Unknown Album"])[0]
                info["track_number"] = int((ogg.get("tracknumber") or ["0"])[0].split("/")[0] or 0)
                info["year"] = (ogg.get("date") or [""])[0]
                info["genre"] = (ogg.get("genre") or [""])[0]
            except Exception:
                logger.debug("Music: OGG tag read error", exc_info=True)
        elif ext == ".wav":
            try:
                wav = WAVE(filepath)
                if wav.tags:
                    info["title"] = str(wav.tags.get("TIT2", path.stem))
            except Exception:
                logger.debug("Music: WAV tag read error", exc_info=True)

        return info
    except Exception:
        return None


def get_cover_bytes(filepath: str) -> tuple[bytes, str] | None:
    try:
        from mutagen.flac import FLAC
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4
    except ImportError:
        return None

    path = Path(filepath)
    ext = path.suffix.lower()

    try:
        if ext == ".flac":
            flac = FLAC(filepath)
            if flac.pictures:
                picture = flac.pictures[0]
                return picture.data, picture.mime
        elif ext == ".mp3":
            id3 = ID3(filepath)
            for key in id3.keys():
                if key.startswith("APIC"):
                    frame = id3[key]
                    return frame.data, frame.mime
        elif ext in (".m4a", ".aac"):
            mp4 = MP4(filepath)
            mp4_tags: dict[str, Any] = cast("dict[str, Any]", mp4.tags or {})
            cover = mp4_tags.get("covr")
            if cover:
                return bytes(cover[0]), "image/jpeg"
    except Exception:
        logger.debug("Music: embedded cover extraction error", exc_info=True)

    for name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png"):
        cover_file = path.parent / name
        if cover_file.exists():
            mime = "image/jpeg" if name.endswith(".jpg") else "image/png"
            return cover_file.read_bytes(), mime

    return None


def find_lyrics(filepath: str) -> str | None:
    path = Path(filepath)
    for ext in (".lrc", ".LRC"):
        lyrics_path = path.with_suffix(ext)
        if lyrics_path.exists():
            try:
                return lyrics_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                logger.debug("Music: lyrics file read error", exc_info=True)

    try:
        ext = path.suffix.lower()
        if ext == ".mp3":
            from mutagen.id3 import ID3

            id3 = ID3(filepath)
            for key in id3.keys():
                if key.startswith("USLT"):
                    return str(id3[key])
        elif ext == ".flac":
            from mutagen.flac import FLAC

            flac = FLAC(filepath)
            lyrics = flac.get("lyrics") or flac.get("LYRICS") or flac.get("unsyncedlyrics")
            if lyrics:
                return str(lyrics[0])
    except Exception:
        logger.debug("Music: embedded lyrics read error", exc_info=True)

    return None


def scan_music_directories(music_dirs: list[str]) -> list[dict[str, Any]]:
    songs: list[dict[str, Any]] = []
    seen: set[str] = set()

    for directory in music_dirs:
        directory_path = Path(directory)
        if not directory_path.exists() or not directory_path.is_dir():
            continue
        for root, _, files in os.walk(directory_path):
            for filename in files:
                filepath = os.path.join(root, filename)
                if Path(filename).suffix.lower() not in AUDIO_EXTS:
                    continue
                song_id = file_id(filepath)
                if song_id in seen:
                    continue
                seen.add(song_id)
                metadata = read_metadata(filepath)
                if metadata:
                    songs.append(metadata)

    return sorted(
        songs,
        key=lambda song: (
            str(song["artist"]).lower(),
            str(song["album"]).lower(),
            song["track_number"],
            str(song["title"]).lower(),
        ),
    )
