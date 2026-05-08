import ast
from pathlib import Path

import pytest

from core.tools.bash import BashTool
from core.tools.read import ReadFileTool


def test_core_tools_do_not_catch_plain_exception():
    tools_root = Path("core/tools")
    offenders = []
    for path in tools_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            caught = node.type
            if isinstance(caught, ast.Name) and caught.id in {"Exception", "BaseException"}:
                offenders.append(f"{path}:{node.lineno}")
            elif isinstance(caught, ast.Tuple):
                for item in caught.elts:
                    if isinstance(item, ast.Name) and item.id in {"Exception", "BaseException"}:
                        offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_read_tool_returns_os_errors_but_propagates_programming_errors(monkeypatch, tmp_path):
    tool = ReadFileTool(str(tmp_path))

    def raise_runtime_error(_file_path):
        raise RuntimeError("programmer bug")

    monkeypatch.setattr(tool, "resolve_path", raise_runtime_error)
    with pytest.raises(RuntimeError, match="programmer bug"):
        tool.execute("file.txt")

    class UnreadablePath:
        def exists(self):
            return True

        def is_file(self):
            return True

        def read_text(self, **_kwargs):
            raise OSError("disk unavailable")

    monkeypatch.setattr(tool, "resolve_path", lambda _file_path: UnreadablePath())
    assert tool.execute("file.txt") == "Error: disk unavailable"


def test_bash_tool_returns_subprocess_os_errors_but_propagates_runtime_errors(
    monkeypatch,
    tmp_path,
):
    tool = BashTool(str(tmp_path))

    def raise_os_error(*_args, **_kwargs):
        raise OSError("shell unavailable")

    monkeypatch.setattr("core.tools.bash.subprocess.Popen", raise_os_error)
    assert tool._run_command("echo hi").startswith("Error running command: shell unavailable")

    def raise_runtime_error(*_args, **_kwargs):
        raise RuntimeError("programmer bug")

    monkeypatch.setattr("core.tools.bash.subprocess.Popen", raise_runtime_error)
    with pytest.raises(RuntimeError, match="programmer bug"):
        tool._run_command("echo hi")
