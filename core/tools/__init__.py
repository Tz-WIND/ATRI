"""Tool registry with workspace-constrained instances."""

from .agent_tool import AgentResultTool, AgentTool
from .automation import (
    AutomationDiffTool,
    AutomationGlobalWriteTool,
    AutomationQueryTool,
    AutomationRetargetTool,
    AutomationWriteTool,
    VstParamQueryTool,
    VstParamSetTool,
)
from .base import Tool
from .bash import BashTool
from .chemistry import ChemDrawTool
from .edit import EditFileTool
from .find_replace import FindReplaceTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .harmony import MusicHarmonyAnalyzeTool, MusicTransposeTool
from .lint import LintTool
from .list_dir import ListDirTool
from .mcp import create_mcp_tools
from .midi import (
    MidiBatchEditTool,
    MidiDiffTool,
    MidiInspectTool,
    MidiQueryTool,
    MidiWriteTool,
    PianoPlayabilityCheckTool,
)
from .mode import AgentModeTool
from .music import MusicTool
from .novelai_image import NovelAIImageTool
from .piano_lane import StudioPianoLaneDiffTool, StudioPianoLaneWriteTool
from .read import ReadFileTool
from .retrieve_tool_result import RetrieveToolResultTool
from .screenshot import ScreenshotTool
from .search import SearchTool
from .skill import LoadSkillTool
from .studio import (
    StudioAudioImportTool,
    StudioExportAudioTool,
    StudioHostControlTool,
    StudioPluginTool,
    StudioProjectQueryTool,
    StudioSyncTool,
    StudioTrackTool,
    StudioTransportTool,
)
from .task_result import TaskResultTool
from .terminal import TerminalTool
from .todo import AgentTodoTool
from .tree import TreeTool
from .web_search import WebFetchTool, WebSearchTool
from .write import WriteFileTool


def create_tools(
    workspace: str,
    skill_manager=None,
    tool_result_store=None,
    task_store=None,
    todo_store=None,
    todo_session_id: str = "",
    todo_on_change=None,
    mcp_servers: dict | None = None,
    mode_controller=None,
) -> list[Tool]:
    """Create a full set of tools bound to the given workspace."""
    tools: list[Tool] = [
        BashTool(workspace),
        TerminalTool(workspace),
        ReadFileTool(workspace),
        RetrieveToolResultTool(workspace, tool_result_store=tool_result_store),
        LoadSkillTool(workspace, skill_manager=skill_manager),
        WriteFileTool(workspace),
        EditFileTool(workspace),
        FindReplaceTool(workspace),
        GlobTool(workspace),
        GrepTool(workspace),
        SearchTool(workspace),
        ListDirTool(workspace),
        TreeTool(workspace),
        ChemDrawTool(workspace),
        ScreenshotTool(workspace),
        AgentTool(workspace, task_store=task_store),
        AgentResultTool(workspace, task_store=task_store),
        TaskResultTool(workspace, task_store=task_store),
        *(
            [
                AgentTodoTool(
                    workspace,
                    todo_store=todo_store,
                    session_id=todo_session_id,
                    on_change=todo_on_change,
                )
            ]
            if todo_store is not None and todo_session_id
            else []
        ),
        AgentModeTool(workspace, mode_controller=mode_controller),
        LintTool(workspace),
        MusicTool(workspace),
        NovelAIImageTool(workspace),
        MidiWriteTool(workspace),
        MidiDiffTool(workspace),
        MidiBatchEditTool(workspace),
        MidiQueryTool(workspace),
        MidiInspectTool(workspace),
        PianoPlayabilityCheckTool(workspace),
        MusicHarmonyAnalyzeTool(workspace),
        MusicTransposeTool(workspace),
        VstParamQueryTool(workspace),
        VstParamSetTool(workspace),
        AutomationQueryTool(workspace),
        AutomationWriteTool(workspace),
        AutomationGlobalWriteTool(workspace),
        AutomationDiffTool(workspace),
        AutomationRetargetTool(workspace),
        StudioProjectQueryTool(workspace),
        StudioHostControlTool(workspace),
        StudioTransportTool(workspace),
        StudioTrackTool(workspace),
        StudioPluginTool(workspace),
        StudioAudioImportTool(workspace),
        StudioExportAudioTool(workspace),
        StudioPianoLaneWriteTool(workspace),
        StudioPianoLaneDiffTool(workspace),
        StudioSyncTool(workspace),
        WebSearchTool(workspace),
        WebFetchTool(workspace),
    ]
    tools.extend(create_mcp_tools(workspace, mcp_servers))
    return tools


def get_tool_by_name(name: str, tools: list[Tool]) -> Tool | None:
    for t in tools:
        if t.name == name:
            return t
    return None
