"""File pattern matching, workspace-constrained."""

from pathlib import Path
from typing import Any

from .base import Tool


class GlobTool(Tool):
    name = "glob"
    description = (
        "Find files matching a glob pattern. Supports ** for recursive matching (e.g. '**/*.py')."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'",
            },  # noqa: E501
            "path": {
                "type": "string",
                "description": "Directory to search in (relative to workspace, default: workspace root)",
            },  # noqa: E501
        },
        "required": ["pattern"],
    }

    def execute(self, pattern: str, path: str = ".", **kwargs: Any) -> str:
        try:
            base = self.resolve_path(path)
        except PermissionError as e:
            return f"Error: {e}"

        if not base.is_dir():
            return f"Error: {path} is not a directory"

        try:
            hits = list(base.glob(pattern))
            hits.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

            total = len(hits)
            shown = hits[:100]
            ws = Path(self._workspace).resolve()
            lines = []
            for h in shown:
                try:
                    lines.append(str(h.relative_to(ws)))
                except ValueError:
                    lines.append(str(h))
            result = "\n".join(lines)

            if total > 100:
                result += f"\n... ({total} matches, showing first 100)"
            return result or "No files matched."
        except Exception as e:
            return f"Error: {e}"
