"""Search-and-replace file editing with unique match + diff.

The LLM specifies an exact substring to find and its replacement. The substring
must appear exactly once in the file, which eliminates ambiguity and makes edits
safe and reviewable. A unified diff is generated for every edit.
"""

import difflib
from pathlib import Path

from .base import Tool


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit a file by replacing an exact string match. "
        "old_string must appear exactly once in the file for safety. "
        "Include enough surrounding context to ensure uniqueness. "
        "Returns a unified diff showing exactly what changed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to edit (relative to workspace)"},
            "old_string": {"type": "string", "description": "Exact text to find (must be unique in file)"},
            "new_string": {"type": "string", "description": "Replacement text"},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def execute(self, file_path: str, old_string: str, new_string: str) -> str:
        if not old_string:
            return "Error: old_string must not be empty"
        try:
            p = self.resolve_path(file_path)
            if not p.exists():
                return f"Error: {file_path} not found"

            content = p.read_text(encoding="utf-8", errors="replace")
            occurrences = content.count(old_string)

            if occurrences == 0:
                preview = content[:500] + ("..." if len(content) > 500 else "")
                return (
                    f"Error: old_string not found in {file_path}.\n"
                    f"File starts with:\n{preview}"
                )
            if occurrences > 1:
                return (
                    f"Error: old_string appears {occurrences} times in {file_path}. "
                    f"Include more surrounding lines to make it unique."
                )

            new_content = content.replace(old_string, new_string, 1)
            p.write_text(new_content, encoding="utf-8")

            diff = _unified_diff(content, new_content, str(p))
            return f"Edited {file_path}\n{diff}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"


def _unified_diff(old: str, new: str, filename: str, context: int = 3) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context,
    )
    result = "".join(diff)
    if len(result) > 3000:
        result = result[:2500] + "\n... (diff truncated)\n"
    return result
