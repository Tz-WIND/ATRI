"""Shell command execution with safety checks and dangerous command interception.

Features:
- Output capture with truncation (head+tail preserved)
- Timeout support
- Dangerous command pattern detection and blocking
- Working directory tracking
- Workspace-constrained execution
"""

import os
import re
import subprocess
from .base import Tool

_DANGEROUS_PATTERNS = [
    (r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)", "recursive delete on home/root"),
    (r"\brm\s+(-\w*)?-rf\b", "force recursive delete (any target)"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "raw disk write"),
    (r">\s*/dev/sd[a-z]", "overwrite block device"),
    (r"\bchmod\s+(-R\s+)?777\b", "chmod 777 (world-writable)"),
    (r":\(\)\s*\{.*:\|:.*\}", "fork bomb"),
    (r"\bcurl\b.*\|\s*(sudo\s+)?ba?sh\b", "pipe curl to bash/sh"),
    (r"\bwget\b.*\|\s*(sudo\s+)?ba?sh\b", "pipe wget to bash/sh"),
    (r"\bgit\s+push\s+.*--force", "force push"),
    (r"\bgit\s+reset\s+--hard", "hard reset"),
    (r"\bformat\s+[a-zA-Z]:", "format drive (Windows)"),
    (r"\bdel\s+/[sS]\s+/[qQ]", "recursive delete (Windows)"),
    (r"\bbase64\s+.*\|\s*(sudo\s+)?(ba)?sh\b", "pipe base64 decode to shell"),
    (r"\bxxd\s+.*\|\s*(sudo\s+)?(ba)?sh\b", "pipe xxd decode to shell"),
    (r"`.*rm\s", "backtick rm injection"),
    (r"\$\(.*rm\s", "subshell rm injection"),
]


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command. Returns stdout, stderr, and exit code. "
        "Use this for running tests, installing packages, git operations, etc. "
        "Dangerous commands (rm -rf, format, etc.) are blocked automatically."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)"},
        },
        "required": ["command"],
    }

    def __init__(self, workspace: str = "."):
        super().__init__(workspace)
        self._cwd: str = self._workspace
        self._pending_approval: dict | None = None

    def execute(self, command: str, timeout: int = 120) -> str:
        warning = _check_dangerous(command)
        if warning:
            self._pending_approval = {"command": command, "reason": warning}
            return (
                f"⚠ BLOCKED: {warning}\n"
                f"Command: {command}\n"
                f"This command requires manual approval via WebUI or chat.\n"
                f"If intentional, modify the command to be more specific."
            )

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=self._cwd,
            )

            if proc.returncode == 0:
                self._update_cwd(command)

            out = proc.stdout
            if proc.stderr:
                out += f"\n[stderr]\n{proc.stderr}"
            if proc.returncode != 0:
                out += f"\n[exit code: {proc.returncode}]"

            if len(out) > 15_000:
                out = (
                    out[:6000]
                    + f"\n\n... truncated ({len(out)} chars total) ...\n\n"
                    + out[-3000:]
                )
            return out.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except Exception as e:
            return f"Error running command: {e}"

    def approve_pending(self) -> str | None:
        """Execute a previously blocked command after user approval."""
        if not self._pending_approval:
            return None
        cmd = self._pending_approval["command"]
        self._pending_approval = None
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                cwd=self._cwd,
            )
            out = proc.stdout
            if proc.stderr:
                out += f"\n[stderr]\n{proc.stderr}"
            return out.strip() or "(no output)"
        except Exception as e:
            return f"Error: {e}"

    def _update_cwd(self, command: str):
        parts = command.split("&&")
        for part in parts:
            part = part.strip()
            if part.startswith("cd "):
                target = part[3:].strip().strip("'\"")
                if target:
                    new_dir = os.path.normpath(
                        os.path.join(self._cwd, os.path.expanduser(target))
                    )
                    if os.path.isdir(new_dir):
                        self._cwd = new_dir


def _check_dangerous(cmd: str) -> str | None:
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd):
            return reason
    return None
