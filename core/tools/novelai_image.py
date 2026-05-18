"""NovelAI image generation tool."""

from __future__ import annotations

import base64
import io
import json
import secrets
import string
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any

from .base import Tool, ToolCapabilities

DEFAULT_NOVELAI_BASE_URL = "https://image.novelai.net"
DEFAULT_NOVELAI_MODEL = "nai-diffusion-4-5-full"
DEFAULT_NOVELAI_SAMPLER = "k_euler_ancestral"
DEFAULT_NEGATIVE_PROMPT = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
    "fewer digits, cropped, worst quality, low quality, jpeg artifacts, signature, watermark"
)

_MIN_DIMENSION = 64
_MAX_DIMENSION = 2048
_MAX_SAMPLES = 4
_REQUEST_TIMEOUT = 180
_USER_AGENT = "ATRI/0.1 NovelAIImageTool"
_IMAGE_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_CONFIG: dict[str, str] = {
    "api_key": "",
    "base_url": DEFAULT_NOVELAI_BASE_URL,
    "model": DEFAULT_NOVELAI_MODEL,
}
_GENERATED_BATCH_MARKER = "ATRI_GENERATED_IMAGE_BATCH:"
_GENERATED_BATCHES: dict[str, list[dict[str, Any]]] = {}
_GENERATED_BATCH_LOCK = threading.Lock()


def set_novelai_config(cfg: dict | None) -> None:
    """Set NovelAI image API config from the application settings."""
    global _CONFIG
    if not isinstance(cfg, dict):
        cfg = {}
    _CONFIG = {
        "api_key": str(cfg.get("api_key") or "").strip(),
        "base_url": str(cfg.get("base_url") or DEFAULT_NOVELAI_BASE_URL).strip(),
        "model": str(cfg.get("model") or DEFAULT_NOVELAI_MODEL).strip(),
    }


def get_novelai_config() -> dict[str, str]:
    return dict(_CONFIG)


def pop_generated_images_from_result(result: str) -> list[dict[str, Any]]:
    """Consume image batches referenced by a NovelAI tool result."""
    batch_ids = []
    for line in str(result or "").splitlines():
        if line.startswith(_GENERATED_BATCH_MARKER):
            batch_id = line.split(":", 1)[1].strip()
            if batch_id:
                batch_ids.append(batch_id)
    if not batch_ids:
        return []

    images: list[dict[str, Any]] = []
    with _GENERATED_BATCH_LOCK:
        for batch_id in batch_ids:
            images.extend(_GENERATED_BATCHES.pop(batch_id, []))
    return images


def _store_generated_images(images: list[dict[str, Any]]) -> str:
    batch_id = secrets.token_urlsafe(12)
    with _GENERATED_BATCH_LOCK:
        if len(_GENERATED_BATCHES) > 100:
            oldest_key = next(iter(_GENERATED_BATCHES))
            _GENERATED_BATCHES.pop(oldest_key, None)
        _GENERATED_BATCHES[batch_id] = images
    return batch_id


def mask_novelai_config(cfg: dict | None) -> dict:
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "api_key": "***" if cfg.get("api_key") else "",
        "base_url": cfg.get("base_url") or DEFAULT_NOVELAI_BASE_URL,
        "model": cfg.get("model") or DEFAULT_NOVELAI_MODEL,
    }


def merge_novelai_config(existing: dict | None, incoming: object) -> dict:
    if not isinstance(incoming, dict):
        raise ValueError("novelai must be an object")
    merged = dict(existing or {})
    if "api_key" in incoming and incoming["api_key"] != "***":
        merged["api_key"] = str(incoming.get("api_key") or "").strip()
    if "base_url" in incoming:
        merged["base_url"] = str(incoming.get("base_url") or DEFAULT_NOVELAI_BASE_URL).strip()
    if "model" in incoming:
        merged["model"] = str(incoming.get("model") or DEFAULT_NOVELAI_MODEL).strip()
    merged.setdefault("api_key", "")
    merged.setdefault("base_url", DEFAULT_NOVELAI_BASE_URL)
    merged.setdefault("model", DEFAULT_NOVELAI_MODEL)
    return merged


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _clamp_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _normalize_dimension(value: Any, default: int) -> int:
    dimension = _clamp_int(value, default, _MIN_DIMENSION, _MAX_DIMENSION)
    return max(_MIN_DIMENSION, round(dimension / 64) * 64)


def _v4_condition(caption: str, *, legacy_uc: bool = False) -> dict:
    return {
        "caption": {
            "base_caption": caption,
            "char_captions": [],
        },
        "use_coords": False,
        "use_order": True,
        "legacy_uc": legacy_uc,
    }


def build_novelai_payload(data: dict, cfg: dict | None = None) -> tuple[dict, dict]:
    cfg = cfg or get_novelai_config()
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("prompt is required")

    model = str(data.get("model") or cfg.get("model") or DEFAULT_NOVELAI_MODEL).strip()
    if not model:
        raise ValueError("model is required")

    width = _normalize_dimension(data.get("width"), 1024)
    height = _normalize_dimension(data.get("height"), 1024)
    steps = _clamp_int(data.get("steps"), 28, 1, 50)
    scale = _clamp_float(data.get("scale"), 5.0, 1.0, 30.0)
    cfg_rescale = _clamp_float(data.get("cfg_rescale"), 0.0, 0.0, 1.0)
    n_samples = _clamp_int(data.get("n_samples"), 1, 1, _MAX_SAMPLES)
    uc_preset = _clamp_int(data.get("uc_preset"), 0, 0, 3)
    image_format = str(data.get("image_format") or "png").strip().lower()
    if image_format not in {"png", "webp"}:
        image_format = "png"
    seed = data.get("seed")
    seed = secrets.randbelow(2**32) if seed in (None, "") else _clamp_int(seed, 0, 0, 2**32 - 1)

    sampler = str(data.get("sampler") or DEFAULT_NOVELAI_SAMPLER).strip()
    if not sampler:
        sampler = DEFAULT_NOVELAI_SAMPLER
    negative_prompt = str(data.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT).strip()
    noise_schedule = str(data.get("noise_schedule") or "karras").strip()

    parameters: dict[str, Any] = {
        "params_version": 3,
        "width": width,
        "height": height,
        "scale": scale,
        "cfg_rescale": cfg_rescale,
        "sampler": sampler,
        "steps": steps,
        "n_samples": n_samples,
        "ucPreset": uc_preset,
        "qualityToggle": bool(data.get("quality_toggle", True)),
        "dynamic_thresholding": False,
        "legacy": False,
        "seed": seed,
        "negative_prompt": negative_prompt,
        "image_format": image_format,
    }
    if noise_schedule:
        parameters["noise_schedule"] = noise_schedule
    if "4" in model:
        parameters["v4_prompt"] = _v4_condition(prompt)
        parameters["v4_negative_prompt"] = _v4_condition(negative_prompt)

    payload = {
        "action": "generate",
        "input": prompt,
        "model": model,
        "parameters": parameters,
    }
    meta = {
        "model": model,
        "width": width,
        "height": height,
        "steps": steps,
        "scale": scale,
        "cfg_rescale": cfg_rescale,
        "sampler": sampler,
        "seed": seed,
        "n_samples": n_samples,
        "image_format": image_format,
    }
    return payload, meta


def _correlation_id() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _authorization_value(api_key: str) -> str:
    api_key = api_key.strip()
    if api_key.lower().startswith("bearer "):
        return api_key
    return f"Bearer {api_key}"


def _validated_base_url(base_url: str) -> str:
    cleaned = (base_url or DEFAULT_NOVELAI_BASE_URL).strip().rstrip("/")
    if "://" not in cleaned:
        cleaned = "https://" + cleaned
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("NovelAI base_url must be an http or https URL")
    if parsed.username or parsed.password:
        raise ValueError("NovelAI base_url must not contain credentials")
    return urllib.parse.urlunsplit(parsed)


def _post_novelai_request(payload: dict, cfg: dict) -> bytes:
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("NovelAI API key is not configured")
    url = _validated_base_url(str(cfg.get("base_url") or DEFAULT_NOVELAI_BASE_URL))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        f"{url}/ai/generate-image",
        data=body,
        headers={
            "Accept": "application/zip",
            "Authorization": _authorization_value(api_key),
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "x-correlation-id": _correlation_id(),
        },
        method="POST",
    )
    try:
        # S310 is suppressed after scheme, host, and credential validation above.
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as response:  # noqa: S310
            return bytes(response.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        message = _novelai_error_message(raw)
        raise RuntimeError(f"NovelAI request failed with HTTP {e.code}: {message}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"NovelAI request failed: {e.reason}") from e


def _novelai_error_message(raw: str) -> str:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return (raw or "request failed").strip()[:500]
    if isinstance(body, dict):
        for key in ("message", "error", "detail"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return (raw or "request failed").strip()[:500]


def _extract_images_from_zip(content: bytes) -> list[dict[str, Any]]:
    images = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            lower_name = item.filename.lower()
            suffix = next(
                (
                    candidate
                    for candidate in _IMAGE_MIME_BY_SUFFIX
                    if lower_name.endswith(candidate)
                ),
                "",
            )
            if not suffix:
                continue
            raw = archive.read(item)
            images.append(
                {
                    "raw": raw,
                    "extension": ".jpg" if suffix == ".jpeg" else suffix,
                    "mime_type": _IMAGE_MIME_BY_SUFFIX[suffix],
                    "size": len(raw),
                }
            )
    if not images:
        raise ValueError("NovelAI response did not contain images")
    return images


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


class NovelAIImageTool(Tool):
    name = "novelai_image"
    description = (
        "Generate images with NovelAI and attach them to the chat reply. "
        "Use only when the user explicitly asks to draw, illustrate, or generate an image."
    )
    parameters: dict[str, Any] = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "NovelAI prompt describing the image to generate.",
            },
            "negative_prompt": {
                "type": "string",
                "description": "Undesired content or quality tags to suppress.",
            },
            "model": {
                "type": "string",
                "description": "NovelAI image model. Defaults to the value in Settings.",
            },
            "width": {
                "type": "integer",
                "description": "Image width. Rounded to a multiple of 64.",
                "default": 1024,
            },
            "height": {
                "type": "integer",
                "description": "Image height. Rounded to a multiple of 64.",
                "default": 1024,
            },
            "sampler": {
                "type": "string",
                "description": "Sampler, for example k_euler_ancestral, k_euler, or k_dpmpp_2m.",
                "default": DEFAULT_NOVELAI_SAMPLER,
            },
            "steps": {
                "type": "integer",
                "description": "Sampling steps, 1-50.",
                "default": 28,
            },
            "scale": {
                "type": "number",
                "description": "Prompt guidance scale.",
                "default": 5,
            },
            "cfg_rescale": {
                "type": "number",
                "description": "CFG rescale, 0-1.",
                "default": 0,
            },
            "seed": {
                "type": "integer",
                "description": "Optional deterministic seed. Omit for random.",
            },
            "n_samples": {
                "type": "integer",
                "description": "Number of images to generate, 1-4.",
                "default": 1,
            },
            "image_format": {
                "type": "string",
                "enum": ["png", "webp"],
                "description": "Output image format.",
                "default": "png",
            },
        },
        "required": ["prompt"],
    }
    capabilities = ToolCapabilities(
        capability="image.generate",
        network=True,
    )

    def execute(
        self,
        prompt: str,
        negative_prompt: str = "",
        model: str = "",
        width: int = 1024,
        height: int = 1024,
        sampler: str = DEFAULT_NOVELAI_SAMPLER,
        steps: int = 28,
        scale: float = 5.0,
        cfg_rescale: float = 0.0,
        seed: int | None = None,
        n_samples: int = 1,
        image_format: str = "png",
        **kwargs: Any,
    ) -> str:
        cfg = get_novelai_config()
        if not cfg.get("api_key"):
            return "Error: NovelAI API key is not configured. Add it in Settings -> NovelAI."

        data = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "model": model,
            "width": width,
            "height": height,
            "sampler": sampler,
            "steps": steps,
            "scale": scale,
            "cfg_rescale": cfg_rescale,
            "seed": seed,
            "n_samples": n_samples,
            "image_format": image_format,
        }
        try:
            payload, meta = build_novelai_payload(data, cfg)
            content = _post_novelai_request(payload, cfg)
            images = _extract_images_from_zip(content)
            chat_images = []
            for index, image in enumerate(images, 1):
                encoded = base64.b64encode(image["raw"]).decode("ascii")
                chat_images.append(
                    {
                        "url": f"data:{image['mime_type']};base64,{encoded}",
                        "file": f"base64://{encoded}",
                        "mime_type": image["mime_type"],
                        "size": image["size"],
                        "name": f"novelai-{meta['seed']}-{index}{image['extension']}",
                    }
                )
            batch_id = _store_generated_images(chat_images)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as e:
            return f"Error: {e}"

        lines = [
            f"Generated {len(images)} NovelAI image(s) for the chat reply.",
            f"{_GENERATED_BATCH_MARKER} {batch_id}",
            (
                f"Model: {meta['model']} | Size: {meta['width']}x{meta['height']} | "
                f"Sampler: {meta['sampler']} | Steps: {meta['steps']} | Seed: {meta['seed']}"
            ),
            (
                "The image data is attached automatically; do not print or rewrite "
                "the internal batch id."
            ),
        ]
        lines.extend(
            f"- Image {index}: {_format_size(image['size'])}"
            for index, image in enumerate(images, 1)
        )
        return "\n".join(lines)
