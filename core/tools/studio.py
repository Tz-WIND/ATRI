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
            "sends": {"type": "array"},
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
        "Import an audio file from the workspace into Music Studio by uploading it through "
        "the existing audio import endpoint. Supported host formats are AAC, FLAC, M4A, "
        "MP3, and WAV. Successful imports already return a sync result; do not call "
        "studio_sync again unless sync failed, is missing, or the user asks."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Workspace-relative audio file path."},
            "start": {"type": "number", "minimum": 0, "default": 0},
            "duration_seconds": {"type": "number", "minimum": 0},
            "waveform": {"type": "array"},
            "original_name": {"type": "string"},
        },
        "required": ["file_path"],
    }
    capabilities = ToolCapabilities(
        capability="music.studio.audio",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        file_path: str,
        start: float = 0.0,
        duration_seconds: float | None = None,
        waveform: list[Any] | None = None,
        original_name: str = "",
        **kwargs: Any,
    ) -> str:
        path = self.resolve_path(file_path)
        if not path.exists() or not path.is_file():
            return f"Error: audio file not found: {file_path}"
        metadata = _compact_payload(
            {
                "start": start,
                "duration_seconds": duration_seconds,
                "waveform": waveform,
                "original_name": original_name or path.name,
            }
        )
        return _dump(_with_agent_sync_hint(_dashboard_audio_import(path, metadata)))


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
