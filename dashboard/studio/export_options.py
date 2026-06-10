"""Export option normalization for Music Studio routes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

EXPORT_FORMATS = {"wav", "flac", "mp3", "midi", "dawproject"}
EXPORT_BIT_DEPTHS = {"i16", "i24", "f32"}
EXPORT_SAMPLE_RATES = {44100, 48000, 88200, 96000, 192000}
EXPORT_BITRATES = {"128k", "192k", "256k", "320k"}


class StudioExportError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def safe_export_stem(value: Any, fallback: str = "ATRI Export") -> str:
    stem = Path(_safe_audio_filename(str(value or fallback))).stem.strip(" ._")
    return stem or fallback


def normalize_export_format(value: Any) -> str:
    format_name = str(value or "wav").strip().lower().lstrip(".")
    if format_name not in EXPORT_FORMATS:
        raise StudioExportError("format is not supported", 400)
    return format_name


def normalize_export_mode(value: Any) -> str:
    mode = str(value or "mixdown").strip().lower()
    if mode not in {"mixdown", "stems"}:
        raise StudioExportError("mode must be mixdown or stems", 400)
    return mode


def normalize_export_target(value: Any) -> str:
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


def normalize_export_sample_rate(value: Any) -> int:
    try:
        sample_rate = int(value or 48000)
    except (TypeError, ValueError):
        raise StudioExportError(
            "sample_rate must be one of 44100, 48000, 88200, 96000, 192000"
        ) from None
    if sample_rate not in EXPORT_SAMPLE_RATES:
        raise StudioExportError("sample_rate must be one of 44100, 48000, 88200, 96000, 192000")
    return sample_rate


def normalize_export_bit_depth(value: Any, format_name: str) -> str:
    bit_depth = str(value or "i24").strip().lower()
    if bit_depth not in EXPORT_BIT_DEPTHS:
        raise StudioExportError("bit_depth must be i16, i24, or f32")
    if format_name == "flac" and bit_depth == "f32":
        raise StudioExportError("flac export requires i16 or i24 bit_depth")
    return bit_depth


def normalize_export_bitrate(value: Any) -> str:
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


def normalize_export_consumer(value: Any) -> str:
    consumer = str(value or "export").strip().lower()
    return consumer if consumer in {"export", "bridge"} else "export"


def project_length_seconds(project: dict[str, Any]) -> float:
    try:
        length_beats = max(0.0, float(project.get("length_beats", 16.0) or 0.0))
    except (TypeError, ValueError):
        length_beats = 16.0
    try:
        tempo = max(1.0, float(project.get("tempo", 120.0) or 120.0))
    except (TypeError, ValueError):
        tempo = 120.0
    return length_beats * 60.0 / tempo


def export_time_range(project: dict[str, Any], payload: dict[str, Any]) -> tuple[float, float]:
    try:
        start = float(payload.get("start", payload.get("start_seconds", 0.0)) or 0.0)
        end_raw = payload.get("end", payload.get("end_seconds"))
        end = float(end_raw) if end_raw is not None else project_length_seconds(project)
    except (TypeError, ValueError):
        raise StudioExportError("start and end must be numbers") from None
    if start < 0:
        raise StudioExportError("start must be non-negative")
    if end <= start:
        raise StudioExportError("end must be after start")
    return start, end


def export_midi_beat_range(payload: dict[str, Any]) -> tuple[float, float] | None:
    raw_range = payload.get("beat_range")
    if isinstance(raw_range, (list, tuple)) and len(raw_range) >= 2:
        start_raw, end_raw = raw_range[0], raw_range[1]
    elif "start_beat" in payload or "end_beat" in payload:
        start_raw, end_raw = payload.get("start_beat", 0.0), payload.get("end_beat")
    else:
        return None

    try:
        start = max(0.0, float(start_raw or 0.0))
        end = float(end_raw)
    except (TypeError, ValueError):
        raise StudioExportError("start_beat and end_beat must be numbers") from None
    if end <= start:
        raise StudioExportError("end_beat must be after start_beat")
    return start, end


def payload_has_explicit_midi_beat_range(payload: dict[str, Any]) -> bool:
    return "beat_range" in payload or "start_beat" in payload or "end_beat" in payload


def payload_has_explicit_time_range(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("start", "end", "start_seconds", "end_seconds"))


def _safe_audio_filename(filename: str) -> str:
    raw_name = Path(str(filename or "audio.wav").replace("\\", "/")).name
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw_name).strip(" ._")
    return safe or "audio.wav"
