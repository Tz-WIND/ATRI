"""Agent tools for VST parameters and project automation tracks."""

from __future__ import annotations

import json
from typing import Any

from core.music_project import (
    automation_diff,
    automation_query,
    automation_retarget,
    automation_write,
    project_summary,
)

from .base import Tool, ToolCapabilities
from .midi import _request_dashboard_sync
from .studio import _dashboard_json


def _automation_track_target_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": [
                    "plugin_parameter",
                    "track_volume",
                    "track_pan",
                ],
            },
            "track_id": {"type": "integer"},
            "slot_id": {"type": "string"},
            "param_index": {"type": "integer", "minimum": 0},
            "param_id": {"type": "integer", "minimum": 0},
            "label": {"type": "string"},
        },
        "anyOf": [
            {
                "properties": {"kind": {"enum": ["track_volume", "track_pan"]}},
                "required": ["kind", "track_id"],
            },
            {
                "properties": {"kind": {"enum": ["plugin_parameter"]}},
                "required": ["kind", "track_id", "param_index"],
            },
        ],
    }


def _automation_retarget_schema() -> dict[str, Any]:
    schema = _automation_track_target_schema()
    schema["properties"]["kind"]["enum"] = [
        "plugin_parameter",
        "track_volume",
        "track_pan",
        "tempo_bpm",
    ]
    schema["anyOf"] = [
        *schema["anyOf"],
        {
            "properties": {"kind": {"enum": ["tempo_bpm"]}},
            "required": ["kind"],
        },
    ]
    return schema


def _automation_write_properties() -> dict[str, Any]:
    return {
        "track_id": {
            "type": "integer",
            "description": "Existing automation track id to replace; omit to create a new track.",
        },
        "name": {"type": "string"},
        "color": {"type": "string"},
        "target": _automation_track_target_schema(),
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "beat": {"type": "number", "minimum": 0},
                    "value": {"type": "number"},
                    "curve": {"type": "string", "enum": ["linear", "hold"]},
                },
                "required": ["beat", "value"],
            },
        },
    }


class VstParamQueryTool(Tool):
    name = "vst_param_query"
    description = (
        "Inspect live plugin parameters for a track and slot. Reads the running audio host "
        "and returns parameter names, units, current normalized values, and automation flags "
        "when available. This does not edit the project timeline."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_id": {"type": "integer", "description": "Project track id."},
            "slot_id": {
                "type": "string",
                "default": "instrument",
                "description": "Plugin slot id such as instrument, insert_1, insert_2.",
            },
        },
        "required": ["track_id"],
    }
    capabilities = ToolCapabilities(
        capability="music.vst.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(self, track_id: int, slot_id: str = "instrument", **kwargs: Any) -> str:
        return json.dumps(
            _dashboard_json(
                "GET",
                f"/api/music/studio/tracks/{track_id}/plugin/parameters?slot_id={slot_id}",
            ),
            ensure_ascii=False,
            indent=2,
        )


class VstParamSetTool(Tool):
    name = "vst_param_set"
    description = (
        "Set one live VST or plugin parameter immediately. This changes the current sound "
        "and autosaves the captured plugin state to the Music Studio project, but does not "
        "create or edit project automation."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_id": {"type": "integer", "description": "Project track id."},
            "slot_id": {"type": "string", "default": "instrument"},
            "param_index": {"type": "integer", "minimum": 0},
            "value": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Normalized parameter value from 0.0 to 1.0.",
            },
        },
        "required": ["track_id", "param_index", "value"],
    }
    capabilities = ToolCapabilities(
        capability="music.vst.write",
        writes_files=True,
        network=True,
    )

    def execute(
        self,
        track_id: int,
        param_index: int,
        value: float,
        slot_id: str = "instrument",
        **kwargs: Any,
    ) -> str:
        return json.dumps(
            _dashboard_json(
                "POST",
                "/api/music/studio/plugin/parameter",
                {
                    "track_id": track_id,
                    "slot_id": slot_id,
                    "param_index": param_index,
                    "value": value,
                },
            ),
            ensure_ascii=False,
            indent=2,
        )


class AutomationQueryTool(Tool):
    name = "automation_query"
    description = (
        "Read project automation tracks, targets, target status, beat ranges, and point "
        "counts. Use include_points=true for detailed point data."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_id": {"type": "integer", "description": "Optional automation track id."},
            "include_points": {"type": "boolean", "default": False},
        },
    }
    capabilities = ToolCapabilities(
        capability="music.automation.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(
        self,
        track_id: int | None = None,
        include_points: bool = False,
        **kwargs: Any,
    ) -> str:
        return json.dumps(
            automation_query(track_id=track_id, include_points=include_points),
            ensure_ascii=False,
            indent=2,
        )


class AutomationWriteTool(Tool):
    name = "automation_write"
    description = (
        "Create or replace a track-scoped automation track in the ATRI music project. "
        "Use automation_global_write for tempo BPM automation. "
        "This writes timeline automation and requests dashboard/host sync."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": _automation_write_properties(),
        "required": ["target", "points"],
    }
    capabilities = ToolCapabilities(capability="music.automation.write")

    def execute(
        self,
        target: dict[str, Any],
        points: list[dict[str, Any]],
        name: str = "",
        track_id: int | None = None,
        color: str | None = None,
        **kwargs: Any,
    ) -> str:
        normalized_kind = str((target or {}).get("kind") or "").strip().lower()
        if not normalized_kind:
            return "Error: target.kind is required"
        if normalized_kind == "tempo_bpm":
            return "Error: use automation_global_write for tempo automation"
        if normalized_kind == "time_signature_numerator":
            return (
                "Error: time_signature_numerator is not an automation target; "
                "use the piano roll meter track"
            )
        if normalized_kind in {"track_volume", "track_pan", "plugin_parameter"} and (
            target or {}
        ).get("track_id") in (None, ""):
            return "Error: target.track_id is required for track automation targets"
        if normalized_kind == "plugin_parameter" and (target or {}).get("param_index") in (
            None,
            "",
        ):
            return "Error: target.param_index is required for plugin parameter automation"
        project, summary = automation_write(
            target,
            points=points,
            name=name,
            track_id=track_id,
            color=color,
        )
        sync_note = _request_dashboard_sync()
        return _format_automation_result("Automation written", summary, project, sync_note)


class AutomationGlobalWriteTool(Tool):
    name = "automation_global_write"
    description = (
        "Create or replace global Music Studio automation for tempo BPM. Use this instead "
        "of automation_write when automating tempo so no track_id is required."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["tempo_bpm"],
                "description": "Global automation target to write.",
            },
            "points": _automation_write_properties()["points"],
            "track_id": {
                "type": "integer",
                "description": "Existing automation track id to replace; omit to create one.",
            },
            "name": {"type": "string"},
            "color": {"type": "string"},
        },
        "required": ["kind", "points"],
    }
    capabilities = ToolCapabilities(capability="music.automation.write", network=True)

    def execute(
        self,
        kind: str,
        points: list[dict[str, Any]],
        track_id: int | None = None,
        name: str = "",
        color: str = "",
        **kwargs: Any,
    ) -> str:
        normalized_kind = str(kind or "").strip().lower()
        if normalized_kind != "tempo_bpm":
            return "Error: kind must be tempo_bpm"
        payload = {
            "kind": normalized_kind,
            "points": points,
            **_compact_payload(
                {
                    "track_id": track_id,
                    "name": name,
                    "color": color,
                }
            ),
        }
        return json.dumps(
            _dashboard_json("POST", "/api/music/studio/automation/global", payload),
            ensure_ascii=False,
            indent=2,
        )


class AutomationDiffTool(Tool):
    name = "automation_diff"
    description = (
        "Apply atomic edits to an existing automation track. Supports add_point, "
        "update_point, delete_point, and replace_range."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_id": {"type": "integer", "description": "Automation track id."},
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["add_point", "update_point", "delete_point", "replace_range"],
                        },
                        "id": {"type": "string"},
                        "beat": {"type": "number", "minimum": 0},
                        "value": {"type": "number"},
                        "curve": {"type": "string", "enum": ["linear", "hold"]},
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                        "points": _automation_write_properties()["points"],
                    },
                    "required": ["op"],
                },
            },
        },
        "required": ["track_id", "operations"],
    }
    capabilities = ToolCapabilities(capability="music.automation.write")

    def execute(self, track_id: int, operations: list[dict[str, Any]], **kwargs: Any) -> str:
        project, summary = automation_diff(track_id, operations)
        sync_note = _request_dashboard_sync()
        return _format_automation_result("Automation diff applied", summary, project, sync_note)


class AutomationRetargetTool(Tool):
    name = "automation_retarget"
    description = "Rebind an existing automation track to a new target without deleting points."
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "track_id": {"type": "integer", "description": "Automation track id."},
            "target": _automation_retarget_schema(),
        },
        "required": ["track_id", "target"],
    }
    capabilities = ToolCapabilities(capability="music.automation.write")

    def execute(self, track_id: int, target: dict[str, Any], **kwargs: Any) -> str:
        project, summary = automation_retarget(track_id, target)
        sync_note = _request_dashboard_sync()
        return _format_automation_result("Automation retargeted", summary, project, sync_note)


def _format_automation_result(
    title: str,
    operation_summary: dict[str, Any],
    project: dict[str, Any],
    sync_note: str,
) -> str:
    summary = project_summary(project)
    return "\n".join(
        [
            title + ".",
            f"Operation: {operation_summary}",
            f"Project: {summary['track_count']} track(s), {summary['tempo']} BPM.",
            sync_note,
        ]
    )


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != "" and value != []
    }
