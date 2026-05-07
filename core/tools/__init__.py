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
from .music import MusicTool
from .read import ReadFileTool
from .search import SearchTool
from .terminal import TerminalTool
from .tree import TreeTool
from .web_search import WebFetchTool, WebSearchTool
from .write import WriteFileTool


def create_tools(workspace: str) -> list[Tool]:
    """Create a full set of tools bound to the given workspace."""
    return [
        BashTool(workspace),
        TerminalTool(workspace),
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        EditFileTool(workspace),
        FindReplaceTool(workspace),
        GlobTool(workspace),
        GrepTool(workspace),
        SearchTool(workspace),
        ListDirTool(workspace),
        TreeTool(workspace),
        AgentTool(workspace),
        AgentResultTool(workspace),
        LintTool(workspace),
        MusicTool(workspace),
        WebSearchTool(workspace),
        WebFetchTool(workspace),
    ]


def get_tool_by_name(name: str, tools: list[Tool]) -> Tool | None:
    for t in tools:
        if t.name == name:
            return t
    return None
