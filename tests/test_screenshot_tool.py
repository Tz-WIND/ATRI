import base64
import platform
import subprocess
from pathlib import Path

from core.tools import create_tools
from core.tools.screenshot import ScreenshotTool

_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/6X6n9sAAAAASUVORK5CYII="
)


def test_screenshot_tool_saves_default_png_with_injected_backend(monkeypatch, tmp_path):
    calls = []

    def fake_capture(destination: Path) -> None:
        calls.append(destination)
        destination.write_bytes(_PNG_1X1)

    monkeypatch.setattr("core.tools.screenshot.capture_screen", fake_capture)

    result = ScreenshotTool(str(tmp_path)).execute()

    assert calls
    assert calls[0].parent == tmp_path / "screenshots"
    assert calls[0].suffix == ".png"
    assert calls[0].read_bytes() == _PNG_1X1
    assert "Captured screenshot to screenshots/" in result
    assert "MIME type: image/png" in result
    assert "ATRI_SCREENSHOT_IMAGE:" in result


def test_screenshot_tool_rejects_paths_outside_workspace(monkeypatch, tmp_path):
    def fail_capture(destination: Path) -> None:
        raise AssertionError("capture backend should not run")

    monkeypatch.setattr("core.tools.screenshot.capture_screen", fail_capture)

    result = ScreenshotTool(str(tmp_path)).execute(file_path="../screen.png")

    assert result.startswith("Error:")
    assert "outside workspace" in result


def test_screenshot_tool_metadata_is_registered(tmp_path):
    tools = {tool.name: tool for tool in create_tools(str(tmp_path))}

    metadata = tools["screenshot"].metadata()

    assert metadata["capability"] == "screen.capture"
    assert metadata["writes_files"] is True
    assert metadata["executes_shell"] is True


def test_linux_capture_uses_first_available_backend(monkeypatch, tmp_path):
    commands = []

    def fake_which(name: str) -> str | None:
        return "/usr/bin/grim" if name == "grim" else None

    def fake_run(args, **kwargs):
        commands.append(args)
        Path(args[-1]).write_bytes(_PNG_1X1)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("core.tools.screenshot.shutil.which", fake_which)
    monkeypatch.setattr("core.tools.screenshot.subprocess.run", fake_run)

    from core.tools.screenshot import capture_screen

    destination = tmp_path / "screen.png"
    capture_screen(destination)

    assert commands == [["grim", str(destination)]]


def test_windows_capture_passes_destination_via_environment(monkeypatch, tmp_path):
    captured = {}

    def fake_which(name: str) -> str | None:
        return "powershell.exe" if name == "powershell.exe" else None

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        screenshot_path = (kwargs.get("env") or {}).get("ATRI_SCREENSHOT_PATH")
        if not screenshot_path:
            return subprocess.CompletedProcess(args, 1, "", "missing screenshot path")
        Path(screenshot_path).write_bytes(_PNG_1X1)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr("core.tools.screenshot.shutil.which", fake_which)
    monkeypatch.setattr("core.tools.screenshot.subprocess.run", fake_run)

    from core.tools.screenshot import capture_screen

    destination = tmp_path / "screen.png"
    capture_screen(destination)

    assert captured["env"]["ATRI_SCREENSHOT_PATH"] == str(destination)
    assert "$env:ATRI_SCREENSHOT_PATH" in captured["args"][6]
