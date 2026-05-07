"""List directory contents with metadata."""

import os
from pathlib import Path
from typing import Any

from ._constants import SKIP_DIRS
from .base import Tool


class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "List files and directories in a given path with size and type info. "
        "Useful for understanding project structure."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path (relative to workspace, default: workspace root)",
            },
            "show_hidden": {
                "type": "boolean",
                "description": "Show hidden files/dirs (default: false)",
            },
        },
        "required": [],
    }

    def execute(self, path: str = ".", show_hidden: bool = False, **kwargs: Any) -> str:
        try:
            base = self.resolve_path(path)
        except PermissionError as e:
            return f"Error: {e}"

        if not base.exists():
            return f"Error: {path} not found"
        if not base.is_dir():
            return f"Error: {path} is not a directory"

        entries = []
        try:
            for item in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                name = item.name
                if not show_hidden and name.startswith("."):
                    continue
                if name in SKIP_DIRS:
                    continue

                if item.is_dir():
                    child_count = (
                        sum(1 for _ in item.iterdir()) if os.access(str(item), os.R_OK) else 0
                    )
                    entries.append(f"📁 {name}/  ({child_count} items)")
                else:
                    size = item.stat().st_size
                    size_str = _fmt_size(size)
                    entries.append(f"   {name}  ({size_str})")
        except PermissionError:
            return f"Error: permission denied reading {path}"

        if not entries:
            return "(empty directory)"

        rel = base.relative_to(Path(self._workspace).resolve())
        header = f"Directory: {rel}/\n{'─' * 40}\n"
        return header + "\n".join(entries)


def _fmt_size(n: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
