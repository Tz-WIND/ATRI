"""File reading with line numbers, workspace-constrained."""

from typing import Any

from .base import Tool, ToolCapabilities


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a file's contents with line numbers. Always read a file before editing it."
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file (relative to workspace)",
            },
            "offset": {"type": "integer", "description": "Start line (1-based). Default 1."},
            "limit": {"type": "integer", "description": "Max lines to read. Default 2000."},
        },
        "required": ["file_path"],
    }
    capabilities = ToolCapabilities(
        capability="filesystem.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(self, file_path: str, offset: int = 1, limit: int = 2000, **kwargs: Any) -> str:
        try:
            p = self.resolve_path(file_path)
            if not p.exists():
                return f"Error: {file_path} not found"
            if not p.is_file():
                return f"Error: {file_path} is a directory, not a file"

            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            total = len(lines)

            start = max(0, offset - 1)
            chunk = lines[start : start + limit]
            numbered = [f"{start + i + 1}\t{ln}" for i, ln in enumerate(chunk)]
            result = "\n".join(numbered)

            if total > start + limit:
                result += f"\n... ({total} lines total, showing {start + 1}-{start + len(chunk)})"
            return result or "(empty file)"
        except PermissionError as e:
            return f"Error: {e}"
        except OSError as e:
            return f"Error: {e}"
