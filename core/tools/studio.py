"""Agent tools for safe, AI-friendly Music Studio operations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from .base import Tool, ToolCapabilities
from .bash import CONFIRM_MARKER

DASHBOARD_BASE_URL = "http://127.0.0.1:6185"
AUTO_SYNC_HINT = (
    "This operation already requested project-to-host sync. Do not call studio_sync "
    "again unless sync reports an error, sync is missing, or the user asks to force resync."
)
NO_SYNC_NEEDED_HINT = (
    "No project-to-host resync was needed for this operation. Call studio_sync only if "
    "host state looks stale or the user asks to force resync."
)
_dashboard_session_token = ""
STUDIO_AUDIO_EXTS = {".aac", ".flac", ".m4a", ".mp3", ".wav"}
STUDIO_AUDIO_FORMATS = "AAC, FLAC, M4A, MP3, WAV"
STUDIO_DAWPROJECT_EXT = ".dawproject"
STUDIO_EXPORT_FORMATS = {"wav", "flac", "mp3", "midi", "dawproject"}
STUDIO_EXPORT_SAMPLE_RATES = {44100, 48000, 88200, 96000, 192000}
STUDIO_EXPORT_BIT_DEPTHS = {"i16", "i24", "f32"}
STUDIO_EXPORT_BITRATES = {"128k", "192k", "256k", "320k"}


def set_dashboard_session_token(token: str | None) -> None:
    """Configure the dashboard session token used by in-process Agent tools."""
    global _dashboard_session_token
    _dashboard_session_token = str(token or "")


class _StudioDashboardTool(Tool):
    """Base helper for dashboard-backed Studio tools with pending approval support."""

    def __init__(self, workspace: str = "."):
        super().__init__(workspace)
        self._pending_approvals: dict[str, dict[str, Any]] = {}

    @property
    def has_pending(self) -> bool:
        return bool(self._pending_approvals)

    @property
    def pending_info(self) -> dict[str, str] | None:
        if not self._pending_approvals:
            return None
        approval_id, pending = next(iter(self._pending_approvals.items()))
        return {
            "approval_id": approval_id,
            "command": str(pending["command"]),
            "reason": str(pending["reason"]),
        }

    @property
    def pending_infos(self) -> list[dict[str, str]]:
        return [
            {
                "approval_id": approval_id,
                "command": str(pending["command"]),
                "reason": str(pending["reason"]),
            }
            for approval_id, pending in self._pending_approvals.items()
        ]

    def approve_pending(self, approval_id: str = "") -> str | None:
        pending = self._pop_pending(approval_id)
        if not pending:
            return None
        action = cast(Callable[[], str], pending["action"])
        return action()

    def reject_pending(self, approval_id: str = "") -> str | None:
        pending = self._pop_pending(approval_id)
        if not pending:
            return None
        command = str(pending["command"])
        return f"Command rejected by user: {command}"

    def _request_approval(
        self,
        *,
        command: str,
        reason: str,
        action: Callable[[], str],
    ) -> str:
        approval_id = uuid.uuid4().hex
        self._pending_approvals[approval_id] = {
            "command": command,
            "reason": reason,
            "action": action,
        }
        return (
            f"{CONFIRM_MARKER}: {reason}\n"
            f"Approval ID: {approval_id}\n"
            f"Command: {command}\n"
            "This Music Studio operation may be destructive or disruptive. "
            "Please confirm execution via the WebUI approve button."
        )

    def _pop_pending(self, approval_id: str = "") -> dict[str, Any] | None:
        if not self._pending_approvals:
            return None
        if approval_id:
            return self._pending_approvals.pop(approval_id, None)
        first_id = next(iter(self._pending_approvals))
        return self._pending_approvals.pop(first_id)


class StudioProjectQueryTool(_StudioDashboardTool):
    name = "studio_project_query"
    description = (
        "Read the current ATRI Music Studio project summary, host status, and optionally "
        "the full project JSON. Use this before making DAW or arrangement changes."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "include_project": {
                "type": "boolean",
                "default": False,
                "description": "Include the full project object instead of only summary and host.",
            },
        },
    }
    capabilities = ToolCapabilities(
        capability="music.studio.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(self, include_project: bool = False, **kwargs: Any) -> str:
        data = _dashboard_json("GET", "/api/music/studio/project")
        if not include_project:
            data = {
                "summary": data.get("summary"),
                "host": data.get("host"),
            }
        return _dump(data)


class StudioHostControlTool(_StudioDashboardTool):
    name = "studio_host_control"
    description = (
        "Inspect, start, or stop the Music Studio audio host. Stopping the host requires "
        "the existing WebUI approval flow before it runs."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "start", "stop"]},
            "sync": {
                "type": "boolean",
                "default": True,
                "description": "When starting the host, sync the current project to it.",
            },
        },
        "required": ["action"],
    }
    capabilities = ToolCapabilities(
        capability="music.studio.host",
        network=True,
        requires_approval=True,
    )

    def execute(self, action: str, sync: bool = True, **kwargs: Any) -> str:
        normalized = str(action or "").strip().lower()
        if normalized == "status":
            return _dump(_dashboard_json("GET", "/api/music/studio/host/status"))
        if normalized == "start":
            return _dump(
                _with_agent_sync_hint(
                    _dashboard_json("POST", "/api/music/studio/host/start", {"sync": sync})
                )
            )
        if normalized == "stop":
            return self._request_approval(
                command="studio_host_control stop",
                reason="stop the running audio host",
                action=lambda: _dump(_dashboard_json("POST", "/api/music/studio/host/stop", {})),
            )
        return "Error: action must be status, start, or stop"


class StudioTransportTool(_StudioDashboardTool):
    name = "studio_transport"
    description = "Control Music Studio transport playback: play, pause, stop, or seek."
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["play", "pause", "stop", "seek"]},
            "position": {
                "type": "number",
                "minimum": 0,
                "description": "Seek target in seconds. Required for action=seek.",
            },
        },
        "required": ["action"],
    }
    capabilities = ToolCapabilities(capability="music.studio.transport", network=True)

    def execute(self, action: str, position: float | None = None, **kwargs: Any) -> str:
        normalized = str(action or "").strip().lower()
        if normalized not in {"play", "pause", "stop", "seek"}:
            return "Error: action must be play, pause, stop, or seek"
        payload: dict[str, Any] = {"action": normalized}
        if normalized == "seek":
            payload["position"] = float(position or 0.0)
        return _dump(_dashboard_json("POST", "/api/music/studio/transport", payload))


class StudioTrackTool(_StudioDashboardTool):
    name = "studio_track"
    description = (
        "Create, update, or delete Music Studio tracks. Supports instrument, audio, bus, "
        "and automation tracks. Successful write actions already return a sync result; "
        "do not call studio_sync again unless sync failed, is missing, or the user asks. "
        "Deleting a track requires WebUI approval."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "update", "delete"]},
            "track_id": {"type": "integer", "description": "Required for update/delete."},
            "name": {"type": "string"},
            "track_type": {
                "type": "string",
                "enum": ["instrument", "audio", "bus", "automation"],
            },
            "color": {"type": "string"},
            "channel_type": {"type": "string", "enum": ["mono", "multichannel"]},
            "output_bus_id": {"type": "integer"},
            "sends": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "target_bus_id": {"type": "integer", "minimum": 0},
                        "target_track_id": {"type": "integer", "minimum": 0},
                        "level": {"type": "number", "minimum": 0, "maximum": 2},
                        "enabled": {"type": "boolean"},
                    },
                },
            },
            "updates": {
                "type": "object",
                "description": "Patch payload for update, such as volume, pan, mute, solo.",
            },
        },
        "required": ["action"],
    }
    capabilities = ToolCapabilities(
        capability="music.studio.track",
        writes_files=True,
        network=True,
        requires_approval=True,
    )

    def execute(
        self,
        action: str,
        track_id: int | None = None,
        name: str = "",
        track_type: str | None = None,
        color: str = "",
        channel_type: str | None = None,
        output_bus_id: int | None = None,
        sends: list[dict[str, Any]] | None = None,
        updates: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        normalized = str(action or "").strip().lower()
        if normalized == "create":
            payload = _compact_payload(
                {
                    "name": name or _default_track_name(track_type),
                    "type": track_type,
                    "color": color,
                    "channel_type": channel_type,
                    "output_bus_id": output_bus_id,
                    "sends": sends,
                }
            )
            return _dump(
                _with_agent_sync_hint(_dashboard_json("POST", "/api/music/studio/tracks", payload))
            )
        if normalized == "update":
            if track_id is None:
                return "Error: track_id is required for update"
            payload = dict(updates or {})
            payload.update(
                _compact_payload(
                    {
                        "name": name,
                        "type": track_type,
                        "color": color,
                        "channel_type": channel_type,
                        "output_bus_id": output_bus_id,
                        "sends": sends,
                    }
                )
            )
            if not payload:
                return "Error: update requires updates or editable track fields"
            return _dump(
                _with_agent_sync_hint(
                    _dashboard_json(
                        "PATCH",
                        f"/api/music/studio/tracks/{int(track_id)}",
                        payload,
                    )
                )
            )
        if normalized == "delete":
            if track_id is None:
                return "Error: track_id is required for delete"
            safe_track_id = int(track_id)
            return self._request_approval(
                command=f"studio_track delete track_id={safe_track_id}",
                reason=f"delete Music Studio track {safe_track_id}",
                action=lambda: _dump(
                    _with_agent_sync_hint(
                        _dashboard_json("DELETE", f"/api/music/studio/tracks/{safe_track_id}")
                    )
                ),
            )
        return "Error: action must be create, update, or delete"


class StudioPluginTool(_StudioDashboardTool):
    name = "studio_plugin"
    description = (
        "Scan plugins, load or clear a track plugin slot, open a native plugin editor, "
        "poll MIDI-learn captured plugin parameters, or rename learned parameters. "
        "Plugin set/clear may return sync when the host needed project resync; otherwise "
        "the slot is loaded live and no extra studio_sync call is needed. "
        "Clearing a slot requires WebUI approval."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "scan",
                    "set",
                    "clear",
                    "open_editor",
                    "poll_captured",
                    "rename_learned",
                ],
            },
            "track_id": {"type": "integer"},
            "slot_id": {"type": "string", "default": "instrument"},
            "plugin": {
                "type": "object",
                "description": (
                    "Plugin payload from studio_plugin scan, or builtin/empty slot payload."
                ),
            },
            "paths": {"type": "array", "items": {"type": "string"}},
            "vst2_paths": {"type": "array", "items": {"type": "string"}},
            "parameter_id": {"type": "string"},
            "name": {"type": "string"},
        },
        "required": ["action"],
    }
    capabilities = ToolCapabilities(
        capability="music.studio.plugin",
        writes_files=True,
        network=True,
        requires_approval=True,
    )

    def execute(
        self,
        action: str,
        track_id: int | None = None,
        slot_id: str = "instrument",
        plugin: dict[str, Any] | None = None,
        paths: list[str] | None = None,
        vst2_paths: list[str] | None = None,
        parameter_id: str = "",
        name: str = "",
        **kwargs: Any,
    ) -> str:
        normalized = str(action or "").strip().lower()
        safe_slot_id = str(slot_id or "instrument")
        if normalized == "scan":
            payload = _compact_payload({"paths": paths, "vst2_paths": vst2_paths})
            method = "POST" if payload else "GET"
            return _dump(_dashboard_json(method, "/api/music/studio/plugins", payload or None))
        if normalized == "set":
            if track_id is None:
                return "Error: track_id is required for set"
            return _dump(
                _with_agent_sync_hint(
                    _dashboard_json(
                        "POST",
                        f"/api/music/studio/tracks/{int(track_id)}/plugin",
                        {"plugin": plugin or {}, "slot_id": safe_slot_id},
                    )
                )
            )
        if normalized == "clear":
            if track_id is None:
                return "Error: track_id is required for clear"
            safe_track_id = int(track_id)
            clear_payload = {
                "plugin": {"id": safe_slot_id, "type": "empty", "name": "Empty"},
                "slot_id": safe_slot_id,
            }
            return self._request_approval(
                command=f"studio_plugin clear track_id={safe_track_id} slot_id={safe_slot_id}",
                reason=f"clear plugin slot {safe_slot_id} on track {safe_track_id}",
                action=lambda: _dump(
                    _with_agent_sync_hint(
                        _dashboard_json(
                            "POST",
                            f"/api/music/studio/tracks/{safe_track_id}/plugin",
                            clear_payload,
                        )
                    )
                ),
            )
        if normalized == "open_editor":
            if track_id is None:
                return "Error: track_id is required for open_editor"
            return _dump(
                _dashboard_json(
                    "POST",
                    f"/api/music/studio/tracks/{int(track_id)}/plugin/editor",
                    {"slot_id": safe_slot_id},
                )
            )
        if normalized == "poll_captured":
            return _dump(_dashboard_json("GET", "/api/music/studio/plugin/captured-parameters"))
        if normalized == "rename_learned":
            if not parameter_id:
                return "Error: parameter_id is required for rename_learned"
            return _dump(
                _dashboard_json(
                    "PATCH",
                    f"/api/music/studio/plugin/learned-parameters/{parameter_id}",
                    {"name": name},
                )
            )
        return (
            "Error: action must be scan, set, clear, open_editor, poll_captured, or rename_learned"
        )


class StudioAudioImportTool(_StudioDashboardTool):
    name = "studio_audio_import"
    description = (
        "Import a workspace audio file into Music Studio using an AI-friendly JSON "
        "request. Prefer path/name/start_beat for natural tool calls; file_path, "
        "original_name, and start are also accepted. Supported host formats are AAC, "
        "FLAC, M4A, MP3, and WAV. Successful imports already return a sync result; "
        "do not call studio_sync again unless sync failed, is missing, or the user asks."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Workspace-relative audio file path."},
            "path": {"type": "string", "description": "Alias for file_path."},
            "start": {"type": "number", "minimum": 0, "default": 0},
            "start_beat": {"type": "number", "minimum": 0, "description": "Alias for start."},
            "duration_seconds": {"type": "number", "minimum": 0},
            "waveform": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "number"},
                        {
                            "type": "object",
                            "properties": {
                                "min": {"type": "number"},
                                "max": {"type": "number"},
                                "rms": {"type": "number"},
                                "peak": {"type": "number"},
                            },
                        },
                    ],
                },
            },
            "original_name": {"type": "string"},
            "name": {"type": "string", "description": "Alias for original_name."},
        },
    }
    capabilities = ToolCapabilities(
        capability="music.studio.audio",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        file_path: str = "",
        path: str = "",
        start: float = 0.0,
        start_beat: float | None = None,
        duration_seconds: float | None = None,
        waveform: list[Any] | None = None,
        original_name: str = "",
        name: str = "",
        **kwargs: Any,
    ) -> str:
        requested_path = str(file_path or path or "").strip()
        if not requested_path:
            return "Error: file_path or path is required"
        try:
            resolved_path = self.resolve_path(requested_path)
        except PermissionError as e:
            return f"Error: {e}"
        if not resolved_path.exists() or not resolved_path.is_file():
            return f"Error: audio file not found: {requested_path}"
        if resolved_path.suffix.lower() not in STUDIO_AUDIO_EXTS:
            return f"Error: unsupported audio file type. Supported formats: {STUDIO_AUDIO_FORMATS}"

        start_value = float(start_beat if start_beat is not None else start)
        metadata = _compact_payload(
            {
                "file_path": requested_path,
                "start": start_value,
                "duration_seconds": duration_seconds,
                "waveform": waveform,
                "original_name": original_name or name or resolved_path.name,
            }
        )
        return _dump(_with_agent_sync_hint(_dashboard_audio_import_file(metadata)))


class StudioDawprojectImportTool(_StudioDashboardTool):
    name = "studio_dawproject_import"
    description = (
        "Import a workspace .dawproject file exported from a DAW into ATRI Music Studio. "
        "This replaces ATRI's current project snapshot and reads MIDI notes, clips, tempo, "
        "meter, and stable track context from the archive. It does not write back to the "
        "source DAW project. Successful imports already return a sync result; do not call "
        "studio_sync again unless sync failed, is missing, or the user asks."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Workspace-relative .dawproject path."},
            "path": {"type": "string", "description": "Alias for file_path."},
            "mode": {
                "type": "string",
                "enum": ["replace"],
                "default": "replace",
                "description": "Replace the current ATRI project with the imported snapshot.",
            },
            "sync": {
                "type": "boolean",
                "default": True,
                "description": "Sync the imported ATRI project to the ATRI host.",
            },
        },
    }
    capabilities = ToolCapabilities(
        capability="music.studio.import",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        file_path: str = "",
        path: str = "",
        mode: str = "replace",
        sync: bool | str = True,
        **kwargs: Any,
    ) -> str:
        requested_path = str(file_path or path or "").strip()
        if not requested_path:
            return "Error: file_path or path is required"
        try:
            resolved_path = self.resolve_path(requested_path)
        except PermissionError as e:
            return f"Error: {e}"
        if not resolved_path.exists() or not resolved_path.is_file():
            return f"Error: DAWproject file not found: {requested_path}"
        if resolved_path.suffix.lower() != STUDIO_DAWPROJECT_EXT:
            return "Error: unsupported DAWproject file type. Supported format: DAWproject"

        safe_mode = str(mode or "replace").strip().lower()
        if safe_mode != "replace":
            return "Error: mode must be replace"

        payload = {
            "file_path": requested_path,
            "mode": safe_mode,
            "sync": _truthy_sync(sync),
        }
        return _dump(
            _with_agent_sync_hint(
                _dashboard_json(
                    "POST",
                    "/api/music/studio/import/dawproject-file",
                    payload,
                    timeout=30,
                )
            )
        )


class StudioExportAudioTool(_StudioDashboardTool):
    name = "studio_export_audio"
    description = (
        "Export the current Music Studio project. Supports MIDI project export, audio "
        "mixdowns, per-track stems, WAV/FLAC/MP3 formats, and bridge-ready manifests."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": ["entire_project", "selected_tracks"],
                "default": "entire_project",
            },
            "track_ids": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Project track IDs. Required when target=selected_tracks.",
            },
            "mode": {
                "type": "string",
                "enum": ["mixdown", "stems"],
                "default": "mixdown",
            },
            "format": {
                "type": "string",
                "enum": ["wav", "flac", "mp3", "midi", "dawproject"],
                "default": "wav",
            },
            "consumer": {
                "type": "string",
                "enum": ["export", "bridge"],
                "default": "export",
            },
            "sample_rate": {
                "type": "integer",
                "enum": [44100, 48000, 88200, 96000, 192000],
                "default": 48000,
            },
            "bit_depth": {
                "type": "string",
                "enum": ["i16", "i24", "f32"],
                "default": "i24",
            },
            "bitrate": {
                "type": "string",
                "enum": ["128k", "192k", "256k", "320k"],
                "default": "320k",
                "description": "MP3 bitrate.",
            },
            "start": {"type": "number", "minimum": 0},
            "end": {"type": "number", "minimum": 0},
        },
    }
    capabilities = ToolCapabilities(
        capability="music.studio.export",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        target: str = "entire_project",
        track_ids: list[int] | None = None,
        mode: str = "mixdown",
        audio_format: str = "",
        sample_rate: int = 48000,
        bit_depth: str = "i24",
        bitrate: str | int | None = None,
        consumer: str = "export",
        start: float | None = None,
        end: float | None = None,
        **kwargs: Any,
    ) -> str:
        safe_target = str(target or "entire_project").strip().lower()
        safe_mode = str(mode or "mixdown").strip().lower()
        safe_format = str(audio_format or kwargs.get("format") or "wav").strip().lower().lstrip(".")
        safe_bit_depth = str(bit_depth or "i24").strip().lower()

        if safe_target not in {"entire_project", "selected_tracks"}:
            return "Error: target must be entire_project or selected_tracks"
        if safe_target == "selected_tracks" and not track_ids:
            return "Error: track_ids is required for selected_tracks export"
        if safe_mode not in {"mixdown", "stems"}:
            return "Error: mode must be mixdown or stems"
        if safe_format not in STUDIO_EXPORT_FORMATS:
            return "Error: format must be wav, flac, mp3, midi, or dawproject"
        safe_consumer = str(consumer or kwargs.get("consumer") or "export").strip().lower()
        if safe_consumer not in {"export", "bridge"}:
            return "Error: consumer must be export or bridge"

        if safe_format in {"midi", "dawproject"}:
            payload = _compact_payload(
                {
                    "target": safe_target,
                    "track_ids": track_ids or [],
                    "mode": safe_mode,
                    "format": safe_format,
                    "consumer": safe_consumer if safe_consumer == "bridge" else None,
                    "start": start,
                    "end": end,
                }
            )
            return _dump(
                _with_agent_sync_hint(
                    _dashboard_json("POST", "/api/music/studio/export", payload, timeout=120)
                )
            )

        try:
            safe_sample_rate = int(sample_rate)
        except (TypeError, ValueError):
            return "Error: sample_rate must be 44100, 48000, 88200, 96000, or 192000"
        if safe_sample_rate not in STUDIO_EXPORT_SAMPLE_RATES:
            return "Error: sample_rate must be 44100, 48000, 88200, 96000, or 192000"
        if safe_bit_depth not in STUDIO_EXPORT_BIT_DEPTHS:
            return "Error: bit_depth must be i16, i24, or f32"

        safe_bitrate: str | None = None
        if safe_format == "mp3":
            if bitrate is None or bitrate == "":
                safe_bitrate = "320k"
            elif isinstance(bitrate, int):
                safe_bitrate = f"{bitrate}k"
            else:
                safe_bitrate = str(bitrate).strip().lower()
                if safe_bitrate.isdigit():
                    safe_bitrate = f"{safe_bitrate}k"
            if safe_bitrate not in STUDIO_EXPORT_BITRATES:
                return "Error: bitrate must be 128k, 192k, 256k, or 320k"

        payload = _compact_payload(
            {
                "target": safe_target,
                "track_ids": track_ids or [],
                "mode": safe_mode,
                "format": safe_format,
                "sample_rate": safe_sample_rate,
                "bit_depth": safe_bit_depth,
                "bitrate": safe_bitrate,
                "consumer": safe_consumer if safe_consumer == "bridge" else None,
                "start": start,
                "end": end,
            }
        )
        return _dump(
            _with_agent_sync_hint(
                _dashboard_json("POST", "/api/music/studio/export", payload, timeout=120)
            )
        )


class StudioSyncTool(_StudioDashboardTool):
    name = "studio_sync"
    description = (
        "Manually force or repair project-to-host sync for Music Studio. Most studio write "
        "tools already return a sync result, so use this only when that sync reports an "
        "error, sync is missing, host state looks stale, or the user explicitly asks."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "broadcast": {"type": "boolean", "default": True},
        },
    }
    capabilities = ToolCapabilities(
        capability="music.studio.sync",
        writes_files=True,
        network=True,
    )

    def execute(self, broadcast: bool = True, **kwargs: Any) -> str:
        return _dump(
            _with_agent_sync_hint(
                _dashboard_json("POST", "/api/music/studio/sync", {"broadcast": broadcast})
            )
        )


def _dashboard_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 3,
) -> dict[str, Any]:
    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "dashboard HTTP client is unavailable"}

    url = f"{DASHBOARD_BASE_URL}{path}"
    headers = _dashboard_headers()
    try:
        if method == "GET":
            response = httpx.get(url, headers=headers, timeout=timeout)
        elif method == "POST":
            response = httpx.post(url, json=payload or {}, headers=headers, timeout=timeout)
        elif method == "PATCH":
            response = httpx.patch(url, json=payload or {}, headers=headers, timeout=timeout)
        elif method == "DELETE":
            response = httpx.delete(url, headers=headers, timeout=timeout)
        else:
            return {"ok": False, "error": f"unsupported dashboard method: {method}"}
        data = response.json()
        if not isinstance(data, dict):
            return {"ok": False, "error": "dashboard returned a non-object response"}
        if response.status_code >= 400:
            return {"ok": False, "status": response.status_code, **data}
        return data
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"dashboard request failed: {e}"}
    except ValueError:
        return {"ok": False, "error": "dashboard returned invalid JSON"}


def _dashboard_audio_import(path: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "dashboard HTTP client is unavailable"}

    data = {
        key: value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for key, value in metadata.items()
    }
    headers = _dashboard_headers()
    try:
        with path.open("rb") as audio_file:
            response = httpx.post(
                f"{DASHBOARD_BASE_URL}/api/music/studio/audio/import",
                data=data,
                files={"file": (path.name, audio_file)},
                headers=headers,
                timeout=30,
            )
        body = response.json()
        if not isinstance(body, dict):
            return {"ok": False, "error": "dashboard returned a non-object response"}
        if response.status_code >= 400:
            return {"ok": False, "status": response.status_code, **body}
        return body
    except OSError as e:
        return {"ok": False, "error": f"failed to read audio file: {e}"}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"dashboard request failed: {e}"}
    except ValueError:
        return {"ok": False, "error": "dashboard returned invalid JSON"}


def _dashboard_audio_import_file(metadata: dict[str, Any]) -> dict[str, Any]:
    return _dashboard_json("POST", "/api/music/studio/audio/import-file", metadata, timeout=30)


def _truthy_sync(value: bool | str) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != "" and value != []
    }


def _default_track_name(track_type: str | None) -> str:
    if track_type == "audio":
        return "Audio Track"
    if track_type == "bus":
        return "Bus"
    if track_type == "automation":
        return "Automation"
    return "Instrument"


def _with_agent_sync_hint(data: dict[str, Any]) -> dict[str, Any]:
    if "sync" not in data:
        return data
    result = dict(data)
    sync = result.get("sync")
    if sync is None:
        result["agent_sync_hint"] = NO_SYNC_NEEDED_HINT
    else:
        result["agent_sync_hint"] = AUTO_SYNC_HINT
    return result


def _dashboard_headers() -> dict[str, str]:
    if not _dashboard_session_token:
        return {}
    return {"X-ATRI-Session": _dashboard_session_token}


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
