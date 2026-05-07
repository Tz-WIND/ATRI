"""Directory tree visualization."""

from pathlib import Path
from typing import Any

from ._constants import SKIP_DIRS
from .base import Tool


class TreeTool(Tool):
    name = "tree"
    description = (
        "Show a tree view of directory structure. Useful for getting an overview of project layout."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Root directory (relative to workspace, default: workspace root)",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum depth to traverse (default: 3)",
            },
        },
        "required": [],
    }

    def execute(self, path: str = ".", max_depth: int = 3, **kwargs: Any) -> str:
        try:
            base = self.resolve_path(path)
        except PermissionError as e:
            return f"Error: {e}"

        if not base.is_dir():
            return f"Error: {path} is not a directory"

        lines = [str(base.relative_to(Path(self._workspace).resolve())) + "/"]
        self._count = 0
        self._walk_tree(base, "", max_depth, 0, lines)

        if self._count > 500:
            lines.append(f"\n... ({self._count} entries total, display capped)")
        return "\n".join(lines[:500])

    def _walk_tree(self, directory: Path, prefix: str, max_depth: int, depth: int, lines: list):
        if depth >= max_depth:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return

        entries = [e for e in entries if e.name not in SKIP_DIRS and not e.name.startswith(".")]
        total = len(entries)

        for i, entry in enumerate(entries):
            self._count += 1
            if self._count > 500:
                return

            is_last = i == total - 1
            connector = "└── " if is_last else "├── "
            ext_prefix = "    " if is_last else "│   "

            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                self._walk_tree(entry, prefix + ext_prefix, max_depth, depth + 1, lines)
            else:
                lines.append(f"{prefix}{connector}{entry.name}")
