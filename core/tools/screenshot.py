"""Cross-platform screen capture tool."""

from __future__ import annotations

import base64
import os
import platform
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .base import Tool, ToolCapabilities

SCREENSHOT_BATCH_MARKER = "ATRI_SCREENSHOT_IMAGE:"

_CAPTURE_TIMEOUT_SECONDS = 30
_MAX_CONTEXT_IMAGE_BYTES = 5 * 1024 * 1024
_SCREENSHOT_BATCHES: dict[str, list[dict[str, Any]]] = {}
_SCREENSHOT_BATCH_LOCK = threading.Lock()
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _store_screenshot_images(images: list[dict[str, Any]]) -> str:
    batch_id = secrets.token_urlsafe(12)
    with _SCREENSHOT_BATCH_LOCK:
        if len(_SCREENSHOT_BATCHES) > 100:
            oldest_key = next(iter(_SCREENSHOT_BATCHES))
            _SCREENSHOT_BATCHES.pop(oldest_key, None)
        _SCREENSHOT_BATCHES[batch_id] = images
    return batch_id


def pop_screenshot_images_from_result(result: str) -> list[dict[str, Any]]:
    batch_ids = []
    for line in str(result or "").splitlines():
        if line.startswith(SCREENSHOT_BATCH_MARKER):
            batch_id = line.split(":", 1)[1].strip()
            if batch_id:
                batch_ids.append(batch_id)
    if not batch_ids:
        return []

    images: list[dict[str, Any]] = []
    with _SCREENSHOT_BATCH_LOCK:
        for batch_id in batch_ids:
            images.extend(_SCREENSHOT_BATCHES.pop(batch_id, []))
    return images


def capture_screen(destination: Path) -> None:
    """Capture the current machine's full screen to *destination* as PNG."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    if system == "Windows":
        _capture_windows(destination)
    elif system == "Darwin":
        _run_capture(["screencapture", "-x", str(destination)], destination)
    elif system == "Linux":
        _capture_linux(destination)
    else:
        raise RuntimeError(f"screen capture is not supported on {system or 'this platform'}")


def _capture_windows(destination: Path) -> None:
    powershell = (
        shutil.which("powershell.exe")
        or shutil.which("powershell")
        or shutil.which("pwsh")
        or "powershell.exe"
    )
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$path = $env:ATRI_SCREENSHOT_PATH
if ([string]::IsNullOrWhiteSpace($path)) {
    throw 'ATRI_SCREENSHOT_PATH is not set'
}
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
    $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
"""
    _run_capture(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        destination,
        env={**os.environ, "ATRI_SCREENSHOT_PATH": str(destination)},
    )


def _capture_linux(destination: Path) -> None:
    candidates = [
        ("gnome-screenshot", ["gnome-screenshot", "-f", str(destination)]),
        ("grim", ["grim", str(destination)]),
        ("spectacle", ["spectacle", "-b", "-n", "-o", str(destination)]),
        ("maim", ["maim", str(destination)]),
        ("scrot", ["scrot", str(destination)]),
        ("import", ["import", "-window", "root", str(destination)]),
    ]
    errors = []
    for command_name, args in candidates:
        if not shutil.which(command_name):
            continue
        try:
            _run_capture(args, destination)
            return
        except RuntimeError as e:
            errors.append(f"{command_name}: {e}")

    if errors:
        detail = "; ".join(errors)
        raise RuntimeError(f"all available Linux screenshot backends failed: {detail}")
    raise RuntimeError(
        "no supported Linux screenshot backend found. Install one of: "
        "gnome-screenshot, grim, spectacle, maim, scrot, or ImageMagick import"
    )


def _run_capture(args: list[str], destination: Path, *, env: dict[str, str] | None = None) -> None:
    try:
        completed = subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            timeout=_CAPTURE_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"capture command timed out after {_CAPTURE_TIMEOUT_SECONDS}s") from e
    except OSError as e:
        raise RuntimeError(str(e)) from e

    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(stderr or f"capture command exited with {completed.returncode}")
    if not destination.exists() or destination.stat().st_size <= 0:
        raise RuntimeError("capture command did not produce an image")


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE):
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _format_bytes(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


class ScreenshotTool(Tool):
    name = "screenshot"
    description = (
        "Capture the current machine's full screen as a PNG file. "
        "The captured image is also supplied to the next model turn when small enough."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Optional workspace-relative PNG path. Defaults to "
                    "screenshots/screenshot-YYYYmmdd-HHMMSS.png."
                ),
            },
        },
        "required": [],
    }
    capabilities = ToolCapabilities(
        capability="screen.capture",
        writes_files=True,
        executes_shell=True,
    )

    def execute(self, file_path: str = "", **kwargs: Any) -> str:
        try:
            destination = self._destination(file_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            capture_screen(destination)
            raw = destination.read_bytes()
            size = len(raw)
            dimensions = _png_dimensions(raw)
            relative = os.path.relpath(destination, self.workspace).replace(os.sep, "/")
            lines = [f"Captured screenshot to {relative}"]

            if size <= _MAX_CONTEXT_IMAGE_BYTES:
                encoded = base64.b64encode(raw).decode("ascii")
                batch_id = _store_screenshot_images(
                    [
                        {
                            "url": f"data:image/png;base64,{encoded}",
                            "file": f"base64://{encoded}",
                            "mime_type": "image/png",
                            "size": size,
                            "name": destination.name,
                        }
                    ]
                )
                lines.append(f"{SCREENSHOT_BATCH_MARKER} {batch_id}")
            else:
                lines.append(
                    "Screenshot is too large to attach to the model context "
                    f"({_format_bytes(size)} > {_format_bytes(_MAX_CONTEXT_IMAGE_BYTES)}). "
                    f"Use `read_file(file_path='{relative}', mode='image')` to inspect it."
                )

            lines.append("MIME type: image/png")
            if dimensions:
                lines.append(f"Dimensions: {dimensions[0]}x{dimensions[1]}")
            lines.append(f"Bytes: {size}")
            return "\n".join(lines)
        except (OSError, PermissionError, RuntimeError, ValueError) as e:
            return f"Error: {e}"

    def _destination(self, file_path: str) -> Path:
        cleaned = str(file_path or "").strip()
        if not cleaned:
            cleaned = f"screenshots/screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png"
        return self.resolve_path(cleaned)
