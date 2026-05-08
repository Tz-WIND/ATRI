"""Lint tool for Python code quality and style checking.

Uses ruff (preferred) with automatic fallback to flake8, pylint, or basic
Python syntax checking. Supports auto-fix via ruff --fix.
"""

import os
import subprocess
from typing import Any

from .base import Tool

# Ordered by preference
_LINTERS: list[dict[str, Any]] = [
    {
        "name": "ruff",
        "check_cmd": ["ruff", "check", "--output-format", "concise", "{path}"],
        "fix_cmd": ["ruff", "check", "--fix", "--output-format", "concise", "{path}"],
        "install_hint": "pip install ruff",
    },
    {
        "name": "flake8",
        "check_cmd": ["flake8", "--max-line-length=120", "{path}"],
        "fix_cmd": None,
        "install_hint": "pip install flake8",
    },
    {
        "name": "pylint",
        "check_cmd": ["pylint", "--output-format=text", "{path}"],
        "fix_cmd": None,
        "install_hint": "pip install pylint",
    },
]


class LintTool(Tool):
    name = "lint"
    description = (
        "Run Python linter on a file or directory. Uses ruff (preferred), "
        "flake8, or pylint — whichever is available. "
        "Set fix=true to auto-fix issues with ruff. "
        "Returns linting errors/warnings with file paths and line numbers."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to lint, relative to workspace. Use '.' for all Python files.",  # noqa: E501
            },
            "fix": {
                "type": "boolean",
                "description": "Auto-fix fixable issues (ruff --fix). Default false.",
                "default": False,
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str, fix: bool = False, **kwargs: Any) -> str:
        target = self.resolve_path(path)
        if not os.path.exists(target):
            return f"Error: path not found: {target}"

        # If target is a file, only lint .py files
        if os.path.isfile(target) and not str(target).endswith(".py"):
            return f"Error: not a Python file: {target}"

        linter = _detect_linter()
        if linter is None:
            return (
                "No Python linter found. Install one:\n"
                "  pip install ruff       (recommended — fast, modern)\n"
                "  pip install flake8\n"
                "  pip install pylint\n\n"
                "Falling back to basic syntax check...\n"
                f"{_basic_syntax_check(target)}"
            )

        return _run_linter(linter, target, fix)


def _detect_linter() -> dict | None:
    """Return the first available linter config, or None."""
    for linter in _LINTERS:
        name = linter["name"]
        try:
            subprocess.run(  # noqa: S603
                [name, "--version"],
                capture_output=True,
                timeout=5,
            )
            return linter
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _run_linter(linter: dict, target, fix: bool) -> str:
    """Execute the linter and return formatted output."""
    name = linter["name"]

    if fix and linter["fix_cmd"]:
        cmd = [a.replace("{path}", str(target)) for a in linter["fix_cmd"]]
    else:
        cmd = [a.replace("{path}", str(target)) for a in linter["check_cmd"]]

    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return f"Error: {name} timed out (120s)"
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return f"Error running {name}: {e}"

    out = proc.stdout.strip()
    if proc.stderr:
        err = proc.stderr.strip()
        if err:
            out += f"\n[stderr]\n{err}"

    suffix = " (--fix applied)" if fix and linter["fix_cmd"] else ""

    if not out:
        return f"Lint passed ({name}){suffix} — no issues found."

    if len(out) > 10_000:
        out = out[:8000] + f"\n\n... truncated ({len(out)} chars total) ..."

    return f"[{name}]{suffix}\n{out}"


def _basic_syntax_check(target) -> str:
    """Compile Python files to catch syntax errors (fallback)."""
    import py_compile

    files = []
    if os.path.isfile(target):
        files = [target]
    else:
        for root, _dirs, filenames in os.walk(str(target)):
            for f in filenames:
                if f.endswith(".py"):
                    files.append(os.path.join(root, f))

    errors = []
    for f in files:
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(str(e))

    if not errors:
        return f"Syntax check passed ({len(files)} files)."

    return "\n".join(errors[:50])
