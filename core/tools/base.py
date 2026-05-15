"""Base class for all tools with workspace path constraint."""

import copy
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolCapabilities:
    capability: str = "general"
    read_only: bool = False
    writes_files: bool = False
    executes_shell: bool = False
    network: bool = False
    requires_approval: bool = False
    supports_parallel: bool = False


_SCHEMA_KEY_ORDER = {
    "type": 0,
    "function": 1,
    "name": 2,
    "description": 3,
    "parameters": 4,
    "properties": 5,
    "items": 6,
    "enum": 7,
    "required": 8,
    "default": 9,
}


class Tool(ABC):
    """Minimal tool interface. All file operations are sandboxed to workspace."""

    name: str
    description: str
    parameters: dict[str, Any]
    capabilities = ToolCapabilities()

    def __init__(self, workspace: str = "."):
        self._workspace = os.path.abspath(workspace)
        self._schema_cache: dict[bool, dict] = {}

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
    def execute(self, *args: Any, **kwargs: Any) -> str: ...

    def cancel(self):
        """Cancel any ongoing execution. Override in subclasses that support interruption."""
        return

    def metadata(self) -> dict:
        data = asdict(self.capabilities)
        data["name"] = self.name
        return data

    def schema(self, *, include_metadata: bool = False) -> dict:
        cached = self._schema_cache.get(include_metadata)
        if cached is not None:
            return copy.deepcopy(cached)

        schema = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
        if include_metadata:
            schema["metadata"] = self.metadata()
        schema = _stable_schema(schema)
        self._schema_cache[include_metadata] = schema
        return copy.deepcopy(schema)


def _stable_schema(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_schema(value[key])
            for key in sorted(value, key=lambda item: (_SCHEMA_KEY_ORDER.get(item, 100), item))
        }
    if isinstance(value, list):
        return [_stable_schema(item) for item in value]
    return value
