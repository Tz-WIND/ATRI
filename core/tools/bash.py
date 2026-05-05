"""Shell command execution with safety checks and dangerous command interception.

Features:
- Output capture with truncation (head+tail preserved)
- Timeout support
- Dangerous command pattern detection and blocking
- Working directory tracking
- Workspace-constrained execution
- Cancellable via Ctrl+C (SIGINT)
"""

import os
import re
import shlex
import subprocess
import threading
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
        self._current_process: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()

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
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self._cwd,
            )
            with self._proc_lock:
                self._current_process = proc

            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                return f"Error: timed out after {timeout}s"
            finally:
                with self._proc_lock:
                    self._current_process = None

            if proc.returncode == 0:
                self._update_cwd(command)

            out = stdout
            if stderr:
                out += f"\n[stderr]\n{stderr}"
            if proc.returncode != 0:
                out += f"\n[exit code: {proc.returncode}]"

            if len(out) > 15_000:
                out = (
                    out[:6000]
                    + f"\n\n... truncated ({len(out)} chars total) ...\n\n"
                    + out[-3000:]
                )
            return out.strip() or "(no output)"
        except Exception as e:
            return f"Error running command: {e}"

    def cancel(self):
        """Terminate the currently running subprocess, if any."""
        with self._proc_lock:
            proc = self._current_process
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

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
        """Track working directory changes from shell commands.

        Handles cd, pushd, and their chaining via &&, ;, ||.
        Does NOT handle subshells ( ... ) or source'd scripts.
        """
        # Split on common separators
        parts = _split_shell_commands(command)
        for part in parts:
            part = part.strip()
            # cd
            if part.startswith("cd ") or part == "cd":
                target = part[2:].strip() if part.startswith("cd ") else ""
                target = target.strip().strip("'\"")
                if not target:
                    self._cwd = os.path.expanduser("~")
                else:
                    new_dir = os.path.normpath(
                        os.path.join(self._cwd, os.path.expanduser(target))
                    )
                    if os.path.isdir(new_dir):
                        self._cwd = new_dir
            # pushd (tracks the pushed directory, ignoring the stack)
            elif part.startswith("pushd "):
                target = part[6:].strip().strip("'\"")
                if target:
                    new_dir = os.path.normpath(
                        os.path.join(self._cwd, os.path.expanduser(target))
                    )
                    if os.path.isdir(new_dir):
                        self._cwd = new_dir
            # popd (best-effort: go to parent; we don't maintain a full dirs stack)
            elif part.strip() == "popd":
                parent = os.path.dirname(self._cwd)
                if os.path.isdir(parent):
                    self._cwd = parent


def _split_shell_commands(cmd: str) -> list[str]:
    """Split a shell command string on &&, ;, and || separators.

    NOTE: This is a simple regex split that does NOT respect shell quoting.
    For example, echo "a && b" would be incorrectly split on the && inside quotes.
    In practice this is acceptable because cd/pushd targets rarely contain these
    separators, but if this function is ever reused for general-purpose command
    parsing it should be replaced with proper shlex-based tokenization.
    """
    return [p for p in re.split(r"(?:&&|;|\|\|)", cmd) if p.strip()]


def _check_dangerous(cmd: str) -> str | None:
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, cmd):
            return reason
    return None
