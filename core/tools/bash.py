"""Shell command execution with safety checks and dangerous command interception.

Features:
- Output capture with truncation (head+tail preserved)
- Timeout support
- Two-tier danger detection: BLOCKED (irreversible) vs CONFIRM (needs approval)
- Working directory tracking
- Workspace-constrained execution
- Cancellable via Ctrl+C (SIGINT)
"""

import os
import re
import subprocess
import threading
from enum import Enum
from .base import Tool

# Shared marker string — also used by process.py for detection/parsing
CONFIRM_MARKER = "CONFIRMATION REQUIRED"


class DangerLevel(Enum):
    SAFE = "safe"
    CONFIRM = "confirm"      # needs user confirmation before executing
    BLOCKED = "blocked"      # hard-blocked, too dangerous

# ── Hard-blocked patterns: irreversible / catastrophic ──
_BLOCKED_PATTERNS = [
    (r"\brm\s+(-\w*)?-rf\s+(/|~|\$HOME)", "recursive force-delete on home/root"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "raw disk write"),
    (r">\s*/dev/sd[a-z]", "overwrite block device"),
    (r":\(\)\s*\{.*:\|:.*\}", "fork bomb"),
    (r"\bcurl\b.*\|\s*(sudo\s+)?ba?sh\b", "pipe curl to bash/sh"),
    (r"\bwget\b.*\|\s*(sudo\s+)?ba?sh\b", "pipe wget to bash/sh"),
    (r"\bbase64\s+.*\|\s*(sudo\s+)?(ba)?sh\b", "pipe base64 decode to shell"),
    (r"\bxxd\s+.*\|\s*(sudo\s+)?(ba)?sh\b", "pipe xxd decode to shell"),
    (r"`.*rm\s", "backtick rm injection"),
    (r"\$\(.*rm\s", "subshell rm injection"),
    (r"\bformat\s+[a-zA-Z]:", "format drive (Windows)"),
]

# ── Confirm-required patterns: destructive but sometimes intentional ──
_CONFIRM_PATTERNS = [
    # Unix
    (r"\brm\s+(-\w*)?-rf\b", "force recursive delete"),
    (r"\brm\s+(-\w*)?-r\b", "recursive delete"),
    (r"\brm\s+", "delete files"),
    (r"\bchmod\s+(-R\s+)?777\b", "chmod 777 (world-writable)"),
    (r"\bgit\s+push\s+.*--force", "force push"),
    (r"\bgit\s+reset\s+--hard", "hard reset"),
    (r"\bgit\s+clean\s+.*-fd", "git clean (remove untracked files)"),
    # Windows cmd
    (r"\bdel\s+/[sS]", "recursive delete (Windows)"),
    (r"\bdel\s+/[fF]", "force delete (Windows)"),
    (r"\bdel\b\s+(?!/)", "delete files (Windows)"),
    (r"\berase\b", "erase files (Windows)"),
    (r"\brd\s+/[sS]", "recursive remove directory (Windows)"),
    (r"\brd\b", "remove directory (Windows)"),
    (r"\brmdir\s+/[sS]", "recursive remove directory (Windows)"),
    (r"\brmdir\b", "remove directory (Windows)"),
    # PowerShell
    (r"\bRemove-Item\b", "Remove-Item (PowerShell)"),
    (r"\bri\s", "ri alias for Remove-Item (PowerShell)"),
    (r"(?:^|\|\||\||;|&&)\s*del\b", "del alias for Remove-Item (PowerShell)"),
    (r"\bClear-Content\b", "clear file contents (PowerShell)"),
    (r"\bStop-Process\b", "kill process (PowerShell)"),
    (r"\bkill\b", "kill process"),
    # Destructive data operations
    (r"\bDROP\s+(TABLE|DATABASE|INDEX|VIEW)\b", "SQL DROP statement"),
    (r"\bTRUNCATE\s+TABLE\b", "SQL TRUNCATE statement"),
    (r"\bnpm\s+cache\s+clean\b", "clear npm cache"),
    (r"\bpip\s+uninstall\b", "pip uninstall"),
]


class BashTool(Tool):
    name = "bash"
    description = (
        "Execute a shell command. Returns stdout, stderr, and exit code. "
        "Use this for running tests, installing packages, git operations, etc. "
        "Dangerous commands are blocked or require user confirmation. "
        "If a command needs confirmation, tell the user and wait for approval."
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
        self._on_confirm_request: callable = None  # callback for WebSocket broadcast

    def execute(self, command: str, timeout: int = 120) -> str:
        level, reason = _check_dangerous(command)

        if level == DangerLevel.BLOCKED:
            return (
                f"🚫 BLOCKED: {reason}\n"
                f"Command: {command}\n"
                f"This command is too dangerous and cannot be executed.\n"
                f"Please use a safer alternative."
            )

        if level == DangerLevel.CONFIRM:
            self._pending_approval = {
                "command": command,
                "reason": reason,
                "timeout": timeout,
            }
            if self._on_confirm_request:
                self._on_confirm_request(command, reason)
            return (
                f"⚠ {CONFIRM_MARKER}: {reason}\n"
                f"Command: {command}\n"
                f"This command is potentially destructive. "
                f"Please confirm execution via the WebUI approve button, "
                f"or tell the user to approve it."
            )

        return self._run_command(command, timeout)

    def _run_command(self, command: str, timeout: int = 120) -> str:
        """Actually execute a shell command (after safety checks pass)."""
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

    @property
    def has_pending(self) -> bool:
        return self._pending_approval is not None

    @property
    def pending_info(self) -> dict | None:
        if not self._pending_approval:
            return None
        return {
            "command": self._pending_approval["command"],
            "reason": self._pending_approval["reason"],
        }

    def approve_pending(self) -> str | None:
        """Execute a previously held command after user approval."""
        if not self._pending_approval:
            return None
        cmd = self._pending_approval["command"]
        timeout = self._pending_approval.get("timeout", 120)
        self._pending_approval = None
        return self._run_command(cmd, timeout)

    def reject_pending(self) -> str | None:
        """Reject and discard the pending command."""
        if not self._pending_approval:
            return None
        cmd = self._pending_approval["command"]
        self._pending_approval = None
        return f"Command rejected by user: {cmd}"

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


def _check_dangerous(cmd: str) -> tuple[DangerLevel, str]:
    """Check a command against two tiers of danger patterns.

    Returns (DangerLevel, reason). BLOCKED takes priority over CONFIRM.
    """
    for pattern, reason in _BLOCKED_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return DangerLevel.BLOCKED, reason
    for pattern, reason in _CONFIRM_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return DangerLevel.CONFIRM, reason
    return DangerLevel.SAFE, ""
