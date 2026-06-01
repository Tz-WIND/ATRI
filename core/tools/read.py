"""File and image reading with workspace constraints."""

import base64
import secrets
import threading
from io import BytesIO
from pathlib import Path
from typing import Any

from .base import Tool, ToolCapabilities

_READ_IMAGE_BATCH_MARKER = "ATRI_READ_IMAGE:"
_MAX_IMAGE_CONTEXT_BYTES = 5 * 1024 * 1024
_READ_IMAGE_BATCHES: dict[str, list[dict[str, Any]]] = {}
_READ_IMAGE_BATCH_LOCK = threading.Lock()
_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _store_read_images(images: list[dict[str, Any]]) -> str:
    batch_id = secrets.token_urlsafe(12)
    with _READ_IMAGE_BATCH_LOCK:
        if len(_READ_IMAGE_BATCHES) > 100:
            oldest_key = next(iter(_READ_IMAGE_BATCHES))
            _READ_IMAGE_BATCHES.pop(oldest_key, None)
        _READ_IMAGE_BATCHES[batch_id] = images
    return batch_id


def pop_read_images_from_result(result: str) -> list[dict[str, Any]]:
    batch_ids = []
    for line in str(result or "").splitlines():
        if line.startswith(_READ_IMAGE_BATCH_MARKER):
            batch_id = line.split(":", 1)[1].strip()
            if batch_id:
                batch_ids.append(batch_id)
    if not batch_ids:
        return []

    images: list[dict[str, Any]] = []
    with _READ_IMAGE_BATCH_LOCK:
        for batch_id in batch_ids:
            images.extend(_READ_IMAGE_BATCHES.pop(batch_id, []))
    return images


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a text file's contents with line numbers, or load a workspace image for visual "
        "model context with mode='image'. Always read a text file before editing it. "
        "When screenshot returns a large image path, call read_file with mode='image' "
        "to inspect it."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file (relative to workspace)",
            },
            "offset": {"type": "integer", "description": "Start line (1-based). Default 1."},
            "limit": {"type": "integer", "description": "Max lines to read. Default 2000."},
            "mode": {
                "type": "string",
                "enum": ["text", "image", "auto"],
                "description": (
                    "Read mode. Use text for line-numbered text, image for PNG/JPEG/WebP/GIF "
                    "visual context, or auto to choose image for supported image extensions."
                ),
                "default": "text",
            },
        },
        "required": ["file_path"],
    }
    capabilities = ToolCapabilities(
        capability="filesystem.read",
        read_only=True,
        supports_parallel=True,
    )

    def execute(
        self,
        file_path: str,
        offset: int = 1,
        limit: int = 2000,
        mode: str = "text",
        **kwargs: Any,
    ) -> str:
        try:
            p = self.resolve_path(file_path)
            if not p.exists():
                return f"Error: {file_path} not found"
            if not p.is_file():
                return f"Error: {file_path} is a directory, not a file"

            read_mode = str(mode or "text").strip().lower()
            if read_mode == "auto":
                read_mode = "image" if p.suffix.lower() in _IMAGE_MIME_BY_SUFFIX else "text"
            if read_mode == "image":
                return self._execute_image(file_path, p)
            if read_mode != "text":
                return "Error: mode must be one of: text, image, auto"

            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            total = len(lines)

            start = max(0, offset - 1)
            chunk = lines[start : start + limit]
            numbered = [f"{start + i + 1}\t{ln}" for i, ln in enumerate(chunk)]
            result = "\n".join(numbered)

            if total > start + limit:
                result += f"\n... ({total} lines total, showing {start + 1}-{start + len(chunk)})"
            return result or "(empty file)"
        except PermissionError as e:
            return f"Error: {e}"
        except OSError as e:
            return f"Error: {e}"

    def _execute_image(self, file_path: str, path: Path) -> str:
        try:
            image = _image_for_model_context(path)
            encoded = base64.b64encode(image["raw"]).decode("ascii")
            chat_image = {
                "url": f"data:{image['mime_type']};base64,{encoded}",
                "file": f"base64://{encoded}",
                "mime_type": image["mime_type"],
                "size": image["size"],
                "name": path.name,
            }
            batch_id = _store_read_images([chat_image])
        except (OSError, RuntimeError, ValueError) as e:
            return f"Error: {e}"

        relative = Path(file_path).as_posix()
        lines = [
            f"Loaded image from {relative}",
            f"{_READ_IMAGE_BATCH_MARKER} {batch_id}",
            f"MIME type: {image['mime_type']}",
            f"Dimensions: {image['width']}x{image['height']}",
            f"Bytes: {image['original_size']}",
        ]
        if image["resized"]:
            lines.append(
                "Resized image for model context: "
                f"{_format_bytes(image['original_size'])} -> {_format_bytes(image['size'])}"
            )
        return "\n".join(lines)


def _image_for_model_context(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    mime_type = _IMAGE_MIME_BY_SUFFIX.get(suffix)
    if not mime_type:
        raise ValueError("not a supported image file (expected PNG, JPEG, WebP, or GIF)")

    raw = path.read_bytes()
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as e:
        raise RuntimeError(
            "Pillow is required for image mode. Install project dependencies."
        ) from e

    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            width, height = image.size
            if len(raw) <= _MAX_IMAGE_CONTEXT_BYTES:
                return {
                    "raw": raw,
                    "mime_type": mime_type,
                    "size": len(raw),
                    "original_size": len(raw),
                    "width": width,
                    "height": height,
                    "resized": False,
                }
            resized_raw, resized_width, resized_height = _resize_image_to_context_bytes(
                image,
                _MAX_IMAGE_CONTEXT_BYTES,
            )
    except UnidentifiedImageError as e:
        raise ValueError("not a supported image file (expected PNG, JPEG, WebP, or GIF)") from e

    return {
        "raw": resized_raw,
        "mime_type": "image/jpeg",
        "size": len(resized_raw),
        "original_size": len(raw),
        "width": resized_width,
        "height": resized_height,
        "resized": True,
    }


def _resize_image_to_context_bytes(image, max_bytes: int) -> tuple[bytes, int, int]:
    from PIL import Image

    rgb = _to_rgb_image(image)
    max_side = max(rgb.size)
    side = min(max_side, 2048)
    candidates = []
    while side >= 256:
        candidates.append(side)
        side = int(side * 0.75)
    candidates.extend([192, 128])

    last_data = b""
    last_size = rgb.size
    for candidate_side in candidates:
        working = rgb.copy()
        working.thumbnail((candidate_side, candidate_side), Image.Resampling.LANCZOS)
        for quality in (85, 75, 65, 55, 45, 35):
            buffer = BytesIO()
            working.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()
            last_data = data
            last_size = working.size
            if len(data) <= max_bytes:
                return data, working.size[0], working.size[1]

    raise ValueError(
        "image is too large to fit model context after resizing "
        f"({_format_bytes(len(last_data))} > {_format_bytes(max_bytes)} at "
        f"{last_size[0]}x{last_size[1]})"
    )


def _to_rgb_image(image):
    from PIL import Image

    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image.copy()


def _format_bytes(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"
