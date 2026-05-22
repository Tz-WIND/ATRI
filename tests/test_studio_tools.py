import json
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest
from quart import Quart

from core.pipeline.stages.process import _extract_confirmation_command
from core.tools import studio
from core.tools.automation import (
    AutomationGlobalWriteTool,
    AutomationRetargetTool,
    AutomationWriteTool,
    VstParamQueryTool,
)
from core.tools.bash import CONFIRM_MARKER
from core.tools.studio import (
    StudioAudioImportTool,
    StudioHostControlTool,
    StudioPluginTool,
    StudioProjectQueryTool,
    StudioSyncTool,
    StudioTrackTool,
)
from dashboard.routes import chat as chat_routes
from dashboard.server import Dashboard


def test_studio_project_query_returns_summary_without_full_project(monkeypatch, tmp_path):
    calls = []

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append((method, path, payload, timeout))
        return {
            "project": {"tracks": [{"id": 1, "notes": [1, 2, 3]}]},
            "summary": {"track_count": 1, "note_count": 3},
            "host": {"running": True},
        }

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)

    result = json.loads(StudioProjectQueryTool(str(tmp_path)).execute())

    assert calls == [("GET", "/api/music/studio/project", None, 3)]
    assert result == {
        "summary": {"track_count": 1, "note_count": 3},
        "host": {"running": True},
    }


def test_studio_host_stop_requires_approval_then_uses_existing_pending_flow(
    monkeypatch,
    tmp_path,
):
    calls = []

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append((method, path, payload, timeout))
        return {"ok": True, "host": {"running": False}}

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)
    tool = StudioHostControlTool(str(tmp_path))

    result = tool.execute(action="stop")

    assert CONFIRM_MARKER in result
    assert "Command: studio_host_control stop" in result
    assert tool.has_pending is True
    assert tool.pending_info is not None
    assert tool.pending_info["command"] == "studio_host_control stop"
    assert tool.pending_info["reason"] == "stop the running audio host"
    assert tool.pending_info["approval_id"]
    assert calls == []

    approved_text = tool.approve_pending()
    assert approved_text is not None
    approved = json.loads(approved_text)

    assert approved["ok"] is True
    assert calls == [("POST", "/api/music/studio/host/stop", {}, 3)]
    assert tool.has_pending is False


def test_studio_track_delete_requires_approval_and_executes_delete_after_approval(
    monkeypatch,
    tmp_path,
):
    calls = []

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append((method, path, payload, timeout))
        return {"ok": True, "track": {"id": 7, "name": "Old Lead"}}

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)
    tool = StudioTrackTool(str(tmp_path))

    result = tool.execute(action="delete", track_id=7)

    assert CONFIRM_MARKER in result
    assert "Command: studio_track delete track_id=7" in result
    assert "Approval ID: " in result
    assert calls == []

    approved_text = tool.approve_pending()
    assert approved_text is not None
    approved = json.loads(approved_text)

    assert approved["track"]["id"] == 7
    assert calls == [("DELETE", "/api/music/studio/tracks/7", None, 3)]


def test_studio_tool_keeps_multiple_pending_approvals_addressable(monkeypatch, tmp_path):
    calls = []

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append((method, path, payload, timeout))
        return {"ok": True, "path": path}

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)
    tool = StudioTrackTool(str(tmp_path))

    first = tool.execute(action="delete", track_id=7)
    second = tool.execute(action="delete", track_id=8)
    first_id = _extract_approval_id(first)
    second_id = _extract_approval_id(second)

    assert first_id
    assert second_id
    assert first_id != second_id

    first_result_text = tool.approve_pending(first_id)

    assert first_result_text is not None
    first_result = json.loads(first_result_text)

    assert first_result["path"] == "/api/music/studio/tracks/7"
    assert calls == [("DELETE", "/api/music/studio/tracks/7", None, 3)]
    assert tool.has_pending is True
    assert tool.pending_info is not None
    assert tool.pending_info["approval_id"] == second_id


def test_studio_approval_tool_uses_requested_approval_id(monkeypatch, tmp_path):
    calls = []

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append(path)
        return {"ok": True, "path": path}

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)
    tool = StudioTrackTool(str(tmp_path))

    first_id = _extract_approval_id(tool.execute(action="delete", track_id=7))
    second_id = _extract_approval_id(tool.execute(action="delete", track_id=8))

    result_text = tool.approve_pending(second_id)

    assert result_text is not None
    result = json.loads(result_text)
    assert first_id
    assert result["path"] == "/api/music/studio/tracks/8"
    assert calls == ["/api/music/studio/tracks/8"]


def test_approval_action_can_run_in_worker_thread(monkeypatch, tmp_path):
    def slow_action(*args, **kwargs):
        time.sleep(0.01)
        return {"ok": True}

    monkeypatch.setattr("core.tools.studio._dashboard_json", slow_action)
    tool = StudioHostControlTool(str(tmp_path))
    result = tool.execute(action="stop")
    approval_id = _extract_approval_id(result)

    assert approval_id
    assert tool.approve_pending(approval_id) == '{\n  "ok": true\n}'


def test_studio_track_create_result_tells_agent_sync_is_automatic(monkeypatch, tmp_path):
    def fake_dashboard_json(method, path, payload=None, timeout=3):
        return {
            "ok": True,
            "track": {"id": 3, "name": payload["name"]},
            "sync": {"ok": True, "host": {"running": True}},
        }

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)

    result = json.loads(StudioTrackTool(str(tmp_path)).execute(action="create", name="Lead"))

    assert result["track"] == {"id": 3, "name": "Lead"}
    assert result["agent_sync_hint"] == (
        "This operation already requested project-to-host sync. Do not call studio_sync "
        "again unless sync reports an error, sync is missing, or the user asks to force resync."
    )


def test_studio_plugin_clear_requires_approval(monkeypatch, tmp_path):
    calls = []

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append((method, path, payload, timeout))
        return {"ok": True, "plugin": {"type": "empty", "name": "Empty"}}

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)
    tool = StudioPluginTool(str(tmp_path))

    result = tool.execute(action="clear", track_id=2, slot_id="insert_1")

    assert CONFIRM_MARKER in result
    assert "Command: studio_plugin clear track_id=2 slot_id=insert_1" in result
    assert calls == []

    approved_text = tool.approve_pending()
    assert approved_text is not None
    approved = json.loads(approved_text)

    assert approved["ok"] is True
    assert calls == [
        (
            "POST",
            "/api/music/studio/tracks/2/plugin",
            {"plugin": {"id": "insert_1", "type": "empty", "name": "Empty"}, "slot_id": "insert_1"},
            3,
        )
    ]


def test_studio_audio_import_accepts_ai_friendly_aliases(monkeypatch, tmp_path):
    calls = []
    sample_dir = tmp_path / "samples"
    sample_dir.mkdir()
    (sample_dir / "kick.wav").write_bytes(b"RIFF....WAVE")

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append((method, path, payload, timeout))
        return {
            "ok": True,
            "clip": {"id": "clip_kick", "name": payload["original_name"]},
            "sync": {"host_running": False},
        }

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)

    result = json.loads(
        StudioAudioImportTool(str(tmp_path)).execute(
            path="samples/kick.wav",
            name="Kick Layer",
            start_beat=8,
            duration_seconds=1.25,
            waveform=[0.2],
        )
    )

    assert calls == [
        (
            "POST",
            "/api/music/studio/audio/import-file",
            {
                "file_path": "samples/kick.wav",
                "start": 8.0,
                "duration_seconds": 1.25,
                "waveform": [0.2],
                "original_name": "Kick Layer",
            },
            30,
        )
    ]
    assert result["clip"] == {"id": "clip_kick", "name": "Kick Layer"}
    assert result["agent_sync_hint"] == (
        "This operation already requested project-to-host sync. Do not call studio_sync "
        "again unless sync reports an error, sync is missing, or the user asks to force resync."
    )


def test_studio_audio_import_rejects_unsupported_format_before_dashboard(monkeypatch, tmp_path):
    calls = []
    (tmp_path / "loop.ogg").write_bytes(b"not playable")

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append((method, path, payload, timeout))
        return {"ok": True}

    monkeypatch.setattr("core.tools.studio._dashboard_json", fake_dashboard_json)

    result = StudioAudioImportTool(str(tmp_path)).execute(path="loop.ogg")

    assert result == (
        "Error: unsupported audio file type. Supported formats: AAC, FLAC, M4A, MP3, WAV"
    )
    assert calls == []


def test_studio_audio_import_schema_exposes_file_path_aliases_without_top_level_anyof(tmp_path):
    schema = StudioAudioImportTool(str(tmp_path)).parameters

    assert "anyOf" not in schema
    assert set(schema["properties"]) >= {"file_path", "path"}


def test_studio_audio_import_rejects_missing_file_path_or_path(tmp_path):
    result = StudioAudioImportTool(str(tmp_path)).execute()

    assert result == "Error: file_path or path is required"


def test_studio_sync_description_marks_tool_as_manual_repair_path():
    assert "force or repair project-to-host sync" in StudioSyncTool.description
    assert "Most studio write tools already return a sync result" in StudioSyncTool.description


def test_dashboard_finds_any_pending_approval_tool():
    pending_tool = SimpleNamespace(
        has_pending=True,
        pending_info={
            "approval_id": "approval-1",
            "command": "studio_track delete track_id=7",
            "reason": "delete track 7",
        },
    )
    agent = SimpleNamespace(tools=[SimpleNamespace(has_pending=False), pending_tool])
    process_stage = SimpleNamespace(get_agent=lambda session_id: agent)
    dashboard = Dashboard.__new__(Dashboard)
    dashboard.lifecycle = SimpleNamespace(process_stage=process_stage)  # type: ignore[assignment]

    assert dashboard._find_approval_tool("session-1") is pending_tool


def test_dashboard_finds_pending_approval_by_id():
    first_tool = SimpleNamespace(
        has_pending=True,
        pending_info={
            "approval_id": "approval-1",
            "command": "studio_track delete track_id=7",
            "reason": "delete track 7",
        },
    )
    second_tool = SimpleNamespace(
        has_pending=True,
        pending_info={
            "approval_id": "approval-2",
            "command": "studio_plugin clear track_id=2 slot_id=insert_1",
            "reason": "clear plugin slot",
        },
    )
    agent = SimpleNamespace(tools=[first_tool, second_tool])
    process_stage = SimpleNamespace(get_agent=lambda session_id: agent)
    dashboard = Dashboard.__new__(Dashboard)
    dashboard.lifecycle = SimpleNamespace(process_stage=process_stage)  # type: ignore[assignment]

    assert dashboard._find_approval_tool("session-1", approval_id="approval-2") is second_tool


@pytest.mark.asyncio
async def test_approve_command_route_runs_pending_action_in_worker_thread(monkeypatch):
    calls: list[tuple[Any, ...]] = []

    class ApprovalTool:
        has_pending = True

        @property
        def pending_info(self):
            return {
                "approval_id": "approval-1",
                "command": "studio_host_control stop",
                "reason": "stop host",
            }

        def approve_pending(self, approval_id=""):
            calls.append(("approve", approval_id))
            return "approved"

    tool = ApprovalTool()
    app = Quart(__name__)

    async def fake_broadcast(message):
        calls.append(("broadcast", message["type"], message["result"]))

    def fake_find_approval_tool(session_id, approval_id=""):
        calls.append(("find", session_id, approval_id))
        return tool

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append(("to_thread", args))
        return fn(*args, **kwargs)

    monkeypatch.setattr("dashboard.routes.chat.asyncio.to_thread", fake_to_thread)
    dashboard = SimpleNamespace(
        app=app,
        lifecycle=SimpleNamespace(process_stage=None, config={}, webchat=None),
        _find_approval_tool=fake_find_approval_tool,
        broadcast=fake_broadcast,
    )
    chat_routes.register(dashboard)  # type: ignore[arg-type]

    response = await app.test_client().post(
        "/api/approve-command",
        json={"session_id": "session-1", "approval_id": "approval-1"},
    )

    assert response.status_code == 200
    assert await response.get_json() == {"ok": True, "result": "approved"}
    assert ("find", "webchat:friend:session-1", "approval-1") in calls
    assert any(
        call[0] == "to_thread" and call[1][-2:] == ("approve_pending", "approval-1")
        for call in calls
    )
    assert ("approve", "approval-1") in calls


def test_dashboard_publishes_session_token_to_studio_tools(monkeypatch):
    tokens: list[str] = []
    monkeypatch.setattr("core.tools.studio.set_dashboard_session_token", tokens.append)
    dashboard = Dashboard.__new__(Dashboard)
    dashboard.auth_tool_session_token = "dashboard-tool-session"  # noqa: S105

    dashboard._publish_auth_token_to_tools()

    assert tokens == ["dashboard-tool-session"]


def test_confirmation_command_parser_reads_non_bash_tool_result():
    result = (
        "CONFIRMATION REQUIRED: delete Music Studio track 7\n"
        "Command: studio_track delete track_id=7\n"
        "This Music Studio operation may be destructive or disruptive."
    )

    assert _extract_confirmation_command(result) == "studio_track delete track_id=7"


def test_dashboard_json_sends_configured_dashboard_session(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(get=fake_get))
    studio.set_dashboard_session_token("session-token")

    try:
        assert studio._dashboard_json("GET", "/api/music/studio/project") == {"ok": True}
    finally:
        studio.set_dashboard_session_token("")

    assert calls == [
        (
            "http://127.0.0.1:6185/api/music/studio/project",
            {"headers": {"X-ATRI-Session": "session-token"}, "timeout": 3},
        )
    ]


def test_vst_param_query_sends_configured_dashboard_session(monkeypatch, tmp_path):
    calls = []

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"ok": True}

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(get=fake_get))
    studio.set_dashboard_session_token("session-token")

    try:
        result = json.loads(
            VstParamQueryTool(str(tmp_path)).execute(track_id=4, slot_id="instrument")
        )
    finally:
        studio.set_dashboard_session_token("")

    assert result == {"ok": True}
    assert calls == [
        (
            "http://127.0.0.1:6185/api/music/studio/tracks/4/plugin/parameters?slot_id=instrument",
            {"headers": {"X-ATRI-Session": "session-token"}, "timeout": 3},
        )
    ]


def test_automation_global_write_posts_ai_friendly_payload(monkeypatch, tmp_path):
    calls = []

    def fake_dashboard_json(method, path, payload=None, timeout=3):
        calls.append((method, path, payload, timeout))
        return {
            "ok": True,
            "summary": {
                "track_id": 9,
                "target": {"kind": "tempo_bpm"},
                "points": 2,
            },
            "sync": {"host_running": False},
        }

    monkeypatch.setattr("core.tools.automation._dashboard_json", fake_dashboard_json)

    result = json.loads(
        AutomationGlobalWriteTool(str(tmp_path)).execute(
            kind="tempo_bpm",
            points=[{"beat": 0, "value": 90}, {"beat": 8, "value": 132}],
            name="Tempo Map",
            color="#58a7b8",
        )
    )

    assert calls == [
        (
            "POST",
            "/api/music/studio/automation/global",
            {
                "kind": "tempo_bpm",
                "points": [{"beat": 0, "value": 90}, {"beat": 8, "value": 132}],
                "name": "Tempo Map",
                "color": "#58a7b8",
            },
            3,
        )
    ]
    assert result["summary"]["track_id"] == 9
    assert result["sync"] == {"host_running": False}


def test_automation_write_schema_requires_track_id_for_track_targets(tmp_path):
    target_schema = AutomationWriteTool(str(tmp_path)).parameters["properties"]["target"]

    assert target_schema["anyOf"] == [
        {
            "properties": {"kind": {"enum": ["track_volume", "track_pan"]}},
            "required": ["kind", "track_id"],
        },
        {
            "properties": {"kind": {"enum": ["plugin_parameter"]}},
            "required": ["kind", "track_id", "param_index"],
        },
    ]
    assert target_schema["properties"]["kind"]["enum"] == [
        "plugin_parameter",
        "track_volume",
        "track_pan",
    ]


def test_automation_write_rejects_track_target_without_track_id(tmp_path):
    result = AutomationWriteTool(str(tmp_path)).execute(
        target={"kind": "track_volume"},
        points=[{"beat": 0, "value": 0.8}],
    )

    assert result == "Error: target.track_id is required for track automation targets"


def test_automation_write_rejects_global_targets(tmp_path):
    tempo_result = AutomationWriteTool(str(tmp_path)).execute(
        target={"kind": "tempo_bpm"},
        points=[{"beat": 0, "value": 120}],
    )
    meter_result = AutomationWriteTool(str(tmp_path)).execute(
        target={"kind": "time_signature_numerator"},
        points=[{"beat": 0, "value": 4}],
    )

    assert tempo_result == "Error: use automation_global_write for tempo automation"
    assert (
        meter_result == "Error: time_signature_numerator is not an automation target; "
        "use the piano roll meter track"
    )


def test_automation_retarget_schema_accepts_global_targets(tmp_path):
    target_schema = AutomationRetargetTool(str(tmp_path)).parameters["properties"]["target"]

    assert target_schema["properties"]["kind"]["enum"] == [
        "plugin_parameter",
        "track_volume",
        "track_pan",
        "tempo_bpm",
    ]
    assert {
        "properties": {"kind": {"enum": ["tempo_bpm"]}},
        "required": ["kind"],
    } in target_schema["anyOf"]


def _extract_approval_id(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Approval ID: "):
            return line.split(":", 1)[1].strip()
    return ""
