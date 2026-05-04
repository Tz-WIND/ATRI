"""Base class for all tools with workspace path constraint."""

import os
from abc import ABC, abstractmethod
from pathlib import Path


class Tool(ABC):
    """Minimal tool interface. All file operations are sandboxed to workspace."""

    name: str
    description: str
    parameters: dict

    def __init__(self, workspace: str = "."):
        self._workspace = os.path.abspath(workspace)

    @property
    def workspace(self) -> str:
        return self._workspace

    def resolve_path(self, file_path: str) -> Path:
        """Resolve a path relative to workspace and ensure it stays within bounds."""
        p = Path(file_path).expanduser()
        if not p.is_absolute():
            p = Path(self._workspace) / p
        p = p.resolve()
        ws = Path(self._workspace).resolve()
        if os.path.commonpath([str(p), str(ws)]) != str(ws):
            raise PermissionError(
                f"Path '{file_path}' resolves to '{p}' which is outside workspace '{ws}'"
            )
        return p

    @abstractmethod
    def execute(self, **kwargs) -> str:
        ...

    def cancel(self):
        """Cancel any ongoing execution. Override in subclasses that support interruption."""

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
