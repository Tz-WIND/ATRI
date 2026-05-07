"""Batch find and replace across multiple files."""

import re
from pathlib import Path
from typing import Any

from ._constants import SKIP_DIRS, TEXT_EXTS
from .base import Tool


class FindReplaceTool(Tool):
    name = "find_replace"
    description = (
        "Find and replace text across multiple files in the workspace. "
        "Supports regex patterns. Returns a summary of all changes made. "
        "Use with caution -- always search first to preview matches."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "find": {"type": "string", "description": "Text or regex pattern to find"},
            "replace": {"type": "string", "description": "Replacement text"},
            "path": {
                "type": "string",
                "description": "Directory to search in (default: workspace root)",
            },
            "include": {
                "type": "string",
                "description": "Only process files matching this glob (e.g. '*.py')",
            },
            "is_regex": {
                "type": "boolean",
                "description": "Treat 'find' as regex (default: false)",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview changes without applying (default: false)",
            },
        },
        "required": ["find", "replace"],
    }

    def execute(
        self,
        find: str,
        replace: str,
        path: str = ".",
        include: str | None = None,
        is_regex: bool = False,
        dry_run: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            base = self.resolve_path(path)
        except PermissionError as e:
            return f"Error: {e}"

        if not base.exists():
            return f"Error: {path} not found"

        if is_regex:
            try:
                pattern = re.compile(find)
            except re.error as e:
                return f"Invalid regex: {e}"
        else:
            pattern = None

        ws = Path(self._workspace).resolve()
        results = []
        total_replacements = 0

        files = self._collect_files(base, include)

        for fp in files:
            try:
                content = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            if is_regex and pattern:
                count = len(pattern.findall(content))
                if count == 0:
                    continue
                new_content = pattern.sub(replace, content)
            else:
                count = content.count(find)
                if count == 0:
                    continue
                new_content = content.replace(find, replace)

            rel = fp.relative_to(ws)
            total_replacements += count

            if dry_run:
                results.append(f"  {rel}: {count} match(es)")
            else:
                fp.write_text(new_content, encoding="utf-8")
                results.append(f"  {rel}: {count} replacement(s)")

        if not results:
            return f"No matches found for '{find}'"

        prefix = "[DRY RUN] " if dry_run else ""
        header = f"{prefix}{total_replacements} total replacement(s) in {len(results)} file(s):\n"
        return header + "\n".join(results[:50])

    @staticmethod
    def _collect_files(root: Path, include: str | None) -> list[Path]:
        results = []
        pattern = include or "*"
        for fp in root.rglob(pattern):
            if any(part in SKIP_DIRS for part in fp.parts):
                continue
            if fp.is_file() and fp.suffix.lower() in TEXT_EXTS:
                results.append(fp)
            if len(results) >= 5000:
                break
        return results
