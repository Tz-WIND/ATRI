"""Retrieve large tool outputs spilled outside the conversation context."""

from __future__ import annotations

from typing import Any

from core.agent.context import ToolResultStore

from .base import Tool


class RetrieveToolResultTool(Tool):
    name = "retrieve_tool_result"
    description = (
        "Retrieve a large tool output that was compressed in conversation context. "
        "Use the tool_result_id shown in a compressed tool result."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "result_id": {
                "type": "string",
                "description": "Tool result id, for example tr_0123abcd4567ef89.",
            },
            "mode": {
                "type": "string",
                "enum": ["summary", "head", "tail", "lines", "query"],
                "description": "Retrieval mode. Default: summary.",
            },
            "start_line": {
                "type": "integer",
                "description": "First 1-based line for mode='lines'.",
            },
            "end_line": {
                "type": "integer",
                "description": "Last 1-based line for mode='lines'.",
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum lines for head, tail, lines, or query. Default 120.",
            },
            "query": {
                "type": "string",
                "description": "Case-insensitive substring for mode='query'.",
            },
        },
        "required": ["result_id"],
    }

    def __init__(self, workspace: str = ".", tool_result_store: ToolResultStore | None = None):
        super().__init__(workspace)
        self.tool_result_store = tool_result_store or ToolResultStore()

    def execute(
        self,
        result_id: str,
        mode: str = "summary",
        start_line: int | None = None,
        end_line: int | None = None,
        max_lines: int = 120,
        query: str | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            return self.tool_result_store.retrieve(
                result_id,
                mode=mode,
                start_line=start_line,
                end_line=end_line,
                max_lines=max_lines,
                query=query,
            )
        except (FileNotFoundError, ValueError) as e:
            return f"Error: {e}"
        except OSError as e:
            return f"Error retrieving tool result: {e}"
