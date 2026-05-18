"""Tool registry with workspace-constrained instances."""

from .agent_tool import AgentResultTool, AgentTool
from .base import Tool
from .bash import BashTool
from .edit import EditFileTool
from .find_replace import FindReplaceTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .lint import LintTool
from .list_dir import ListDirTool
from .mcp import create_mcp_tools
from .midi import MidiBatchEditTool, MidiDiffTool, MidiInspectTool, MidiQueryTool, MidiWriteTool
from .mode import AgentModeTool
from .music import MusicTool
from .novelai_image import NovelAIImageTool
from .read import ReadFileTool
from .retrieve_tool_result import RetrieveToolResultTool
from .search import SearchTool
from .skill import LoadSkillTool
from .task_result import TaskResultTool
from .terminal import TerminalTool
from .tree import TreeTool
from .web_search import WebFetchTool, WebSearchTool
from .write import WriteFileTool


def create_tools(
    workspace: str,
    skill_manager=None,
    tool_result_store=None,
    task_store=None,
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
        AgentTool(workspace, task_store=task_store),
        AgentResultTool(workspace, task_store=task_store),
        TaskResultTool(workspace, task_store=task_store),
        AgentModeTool(workspace, mode_controller=mode_controller),
        LintTool(workspace),
        MusicTool(workspace),
        NovelAIImageTool(workspace),
        MidiWriteTool(workspace),
        MidiDiffTool(workspace),
        MidiBatchEditTool(workspace),
        MidiQueryTool(workspace),
        MidiInspectTool(workspace),
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
