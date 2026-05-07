"""File creation / overwrite, workspace-constrained."""

from typing import Any

from .base import Tool
from .edit import _unified_diff


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new file or completely overwrite an existing one. "
        "For small edits to existing files, prefer edit_file instead."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path for the file (relative to workspace)",
            },
            "content": {"type": "string", "description": "Full file content to write"},
        },
        "required": ["file_path", "content"],
    }

    def execute(self, file_path: str, content: str, **kwargs: Any) -> str:
        try:
            p = self.resolve_path(file_path)
            old_content = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            diff = _unified_diff(old_content, content, str(p))
            return f"Wrote {n_lines} lines to {file_path}\n{diff}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"
