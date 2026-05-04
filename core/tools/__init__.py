"""Tool registry with workspace-constrained instances."""

from .bash import BashTool
from .read import ReadFileTool
from .write import WriteFileTool
from .edit import EditFileTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .search import SearchTool
from .list_dir import ListDirTool
from .tree import TreeTool
from .terminal import TerminalTool
from .find_replace import FindReplaceTool
from .agent_tool import AgentTool, AgentResultTool
from .lint import LintTool
from .music import MusicTool
from .web_search import WebSearchTool, WebFetchTool
from .base import Tool


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
