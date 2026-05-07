"""Persistent terminal session tool.

Unlike bash (which runs one-shot commands), the terminal tool maintains
a stateful shell session where environment variables, directory changes,
and background processes persist across calls.
"""

import os
import subprocess
from typing import Any

from .base import Tool
from .bash import (
    CONFIRM_MARKER,
    DangerLevel,
    _check_dangerous,
    _check_workspace_escape,
    _is_within_workspace,
)


class TerminalTool(Tool):
    name = "terminal"
    description = (
        "Run commands in a persistent terminal session. Unlike bash, the terminal "
        "maintains state across calls: environment variables, working directory, "
        "and shell history all persist. Use for interactive workflows like "
        "activating virtual environments, setting env vars, then running commands."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to execute in the persistent session"},  # noqa: E501
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)"},
            "session_id": {"type": "string", "description": "Session identifier (default: 'default'). Use different IDs for parallel sessions."},  # noqa: E501
        },
        "required": ["command"],
    }

    _sessions: dict[str, "_ShellSession"] = {}  # noqa: RUF012

    def execute(self, command: str, timeout: int = 120, session_id: str = "default", **kwargs: Any) -> str:  # noqa: E501
        session = self._get_session(session_id)
        return session.run(command, timeout)

    def _get_session(self, session_id: str) -> "_ShellSession":
        if session_id not in self._sessions:
            self._sessions[session_id] = _ShellSession(self._workspace)
        return self._sessions[session_id]


class _ShellSession:
    """A persistent shell process that accepts commands via stdin."""

    def __init__(self, cwd: str):
        self._cwd = cwd
        self._workspace = cwd
        self._env = os.environ.copy()
        self._history: list[str] = []

    def run(self, command: str, timeout: int = 120) -> str:
        self._history.append(command)

        level, reason = _check_dangerous(command)
        if level == DangerLevel.BLOCKED:
            return (
                f"🚫 BLOCKED: {reason}\n"
                f"Command: {command}\n"
                f"This command is too dangerous and cannot be executed."
            )
        if level == DangerLevel.CONFIRM:
            return (
                f"⚠️ {CONFIRM_MARKER}: {reason}\n"
                f"Command: {command}\n"
                "Persistent terminal commands cannot be approved inline. "
                "Use the bash tool if this command is intentional."
            )

        workspace_level, workspace_reason = _check_workspace_escape(
            command,
            self._cwd,
            self._workspace,
        )
        if workspace_level == DangerLevel.BLOCKED:
            return (
                f"🚫 BLOCKED: {workspace_reason}\n"
                f"Command: {command}\n"
                f"Terminal commands must stay inside workspace '{self._workspace}'."
            )

        # Track cd commands to maintain working directory
        if command.strip().startswith("cd "):
            target = command.strip()[3:].strip().strip("'\"")
            new_dir = os.path.normpath(os.path.join(self._cwd, os.path.expanduser(target)))
            if os.path.isdir(new_dir) and _is_within_workspace(new_dir, self._workspace):
                self._cwd = new_dir
                return f"Changed directory to {self._cwd}"
            return f"Error: directory not found: {target}"

        # Handle export/set for env vars
        if command.strip().startswith("export "):
            parts = command.strip()[7:].split("=", 1)
            if len(parts) == 2:
                key, val = parts[0].strip(), parts[1].strip().strip("'\"")
                self._env[key] = val
                return f"Set {key}={val}"
            return "Error: invalid export syntax"

        try:
            shell = True
            if os.name == "nt":
                full_cmd = command
            else:
                full_cmd = command

            proc = subprocess.run(  # noqa: S603
                full_cmd,
                shell=shell,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=self._cwd,
                env=self._env,
            )

            out = proc.stdout
            if proc.stderr:
                out += f"\n[stderr]\n{proc.stderr}"
            if proc.returncode != 0:
                out += f"\n[exit code: {proc.returncode}]"

            if len(out) > 15_000:
                out = out[:6000] + f"\n\n... truncated ({len(out)} chars) ...\n\n" + out[-3000:]

            return out.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: timed out after {timeout}s"
        except Exception as e:
            return f"Error: {e}"
