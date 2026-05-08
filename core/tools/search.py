"""Semantic-style search across file names and contents.

Combines filename matching and content keyword search into one tool,
providing a higher-level "find anything related to X" capability.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ._constants import SKIP_DIRS, TEXT_EXTS
from .base import Tool


class SearchTool(Tool):
    name = "search"
    description = (
        "Search for files and content matching a query across the workspace. "
        "Searches both file names and file contents. Returns the most relevant results."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (keywords or pattern)"},
            "path": {
                "type": "string",
                "description": "Subdirectory to search in (default: workspace root)",
            },
            "file_only": {
                "type": "boolean",
                "description": "Only search file names, not contents (default: false)",
            },
        },
        "required": ["query"],
    }

    def execute(self, query: str, path: str = ".", file_only: bool = False, **kwargs: Any) -> str:
        try:
            base = self.resolve_path(path)
        except PermissionError as e:
            return f"Error: {e}"

        if not base.exists():
            return f"Error: {path} not found"

        ws = Path(self._workspace).resolve()
        keywords = query.lower().split()
        if not keywords:
            return "Error: empty query"

        name_matches = []
        content_matches = []

        for fp in self._walk(base):
            rel = str(fp.relative_to(ws))
            name_lower = fp.name.lower()

            # Filename match
            if all(kw in name_lower or kw in rel.lower() for kw in keywords):
                name_matches.append(f"📄 {rel}")

            # Content match
            if not file_only and fp.suffix.lower() in TEXT_EXTS and fp.stat().st_size < 1_000_000:
                try:
                    text = fp.read_text(encoding="utf-8", errors="ignore")
                    text_lower = text.lower()
                    if all(kw in text_lower for kw in keywords):
                        # Find first matching line
                        for lineno, line in enumerate(text.splitlines(), 1):
                            if any(kw in line.lower() for kw in keywords):
                                content_matches.append(f"   {rel}:{lineno}: {line.strip()[:120]}")
                                break
                except OSError:
                    pass

            if len(name_matches) + len(content_matches) >= 100:
                break

        parts = []
        if name_matches:
            parts.append(f"── File name matches ({len(name_matches)}) ──")
            parts.extend(name_matches[:30])
        if content_matches:
            parts.append(f"\n── Content matches ({len(content_matches)}) ──")
            parts.extend(content_matches[:70])

        return "\n".join(parts) if parts else "No matches found."

    @staticmethod
    def _walk(root: Path) -> Iterator[Path]:
        if root.name in SKIP_DIRS:
            return
        yielded = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
            for filename in filenames:
                yield Path(dirpath) / filename
                yielded += 1
                if yielded >= 10000:
                    return
