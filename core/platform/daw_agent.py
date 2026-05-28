"""DAW plugin platform adapter.

Messages created by an embedded DAW/VST UI go through the same pipeline as
WebChat and OneBot, but the session identity is project-scoped so multiple
plugin instances in the same DAW project share one Agent context.
"""

import asyncio
import json
import math
import uuid
from asyncio import Queue
from typing import Any

from core import logger

from .base import Platform, PlatformMeta, PlatformStatus
from .message import Image, MessageChain, MessageEvent, MessageType, Plain, Sender

DEFAULT_DAW_PROJECT_SESSION_ID = "default_project"
DEFAULT_DAW_WORKSPACE = "atri_studio"
DAW_HOST_CONTEXT_ALLOWED_KEYS = frozenset(
    {
        "host",
        "workspace",
        "tempo_bpm",
        "is_playing",
        "sample_rate",
        "block_size",
        "time_signature",
        "track",
    }
)
_DAW_HOST_CONTEXT_STRING_KEYS = {"host", "workspace", "track"}
_DAW_HOST_CONTEXT_NUMBER_KEYS = {"tempo_bpm", "sample_rate"}
_MAX_DAW_HOST_CONTEXT_KEYS = 12
_MAX_DAW_HOST_CONTEXT_DEPTH = 2
_MAX_DAW_HOST_CONTEXT_VALUES = 64
_MAX_DAW_HOST_CONTEXT_STRING_CHARS = 128
_MAX_DAW_HOST_CONTEXT_TOTAL_STRING_CHARS = 512
_MAX_DAW_HOST_CONTEXT_JSON_BYTES = 2048


class DawAgentAdapter(Platform):
    """Platform adapter for embedded DAW plugin chat surfaces."""

    def __init__(self, event_queue: Queue):
        super().__init__({}, event_queue)
        self._pending: dict[str, asyncio.Future] = {}
        self._status = PlatformStatus.RUNNING

    def create_event(
        self,
        message: str,
        project_session_id: str,
        *,
        instance_id: str = "",
        workspace: str = DEFAULT_DAW_WORKSPACE,
        host_context: dict[str, Any] | None = None,
        images: list[dict] | None = None,
        model: str = "",
        model_provider: str = "",
    ) -> tuple[MessageEvent, asyncio.Future]:
        """Create a project-scoped MessageEvent from a DAW plugin message."""
        chain: MessageChain = []
        if message:
            chain.append(Plain(text=message))
        for item in images or []:
            chain.append(
                Image(
                    url=str(item.get("url") or ""),
                    file=str(item.get("file") or ""),
                    mime_type=str(item.get("mime_type") or ""),
                    size=int(item.get("size") or 0),
                )
            )

        message_outline = message or (
            f"[{len(images or [])} image attachment(s)]" if images else ""
        )
        event = MessageEvent(
            message_str=message_outline,
            message_chain=chain,
            message_type=MessageType.FRIEND_MESSAGE,
            sender=Sender(user_id="daw_user", nickname="DAW"),
            session_id=normalize_daw_project_session_id(project_session_id),
            self_id="atri",
            platform_name="daw_agent",
        )

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        req_id = uuid.uuid4().hex
        event._extras["_daw_agent_req_id"] = req_id
        event._extras["daw_agent_instance_id"] = str(instance_id or "")
        event._extras["daw_agent_workspace"] = normalize_daw_workspace(workspace)
        event._extras["daw_agent_host_context"] = normalize_daw_host_context(host_context)
        selected_model = str(model or "").strip()
        if selected_model:
            event._extras["daw_agent_model"] = selected_model
            selected_provider = str(model_provider or "").strip()
            if selected_provider:
                event._extras["daw_agent_model_provider"] = selected_provider
        self._pending[req_id] = future

        self.commit_event(event)
        return event, future

    async def send_message(self, event: MessageEvent, text: str):
        req_id = event._extras.get("_daw_agent_req_id")
        logger.info(
            "DawAgent.send_message: req_id=%s, pending=%s, text=%dchars",
            req_id,
            list(self._pending.keys()),
            len(text),
        )
        if req_id and req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                fut.set_result({"text": text, "chain": None})

    async def send_message_chain(self, event: MessageEvent, chain: MessageChain):
        req_id = event._extras.get("_daw_agent_req_id")
        if req_id and req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                texts = [c.text for c in chain if isinstance(c, Plain)]
                fut.set_result({"text": "\n".join(texts), "chain": chain})

    def cancel_request(self, event: MessageEvent) -> bool:
        req_id = event._extras.get("_daw_agent_req_id")
        if not req_id:
            return False
        fut = self._pending.pop(req_id, None)
        if fut is None:
            return False
        if not fut.done():
            fut.cancel()
        return True

    async def run(self):
        logger.info("DAW Agent adapter ready (driven by dashboard HTTP/WebView).")
        self._shutdown = asyncio.Event()
        await self._shutdown.wait()

    async def terminate(self):
        if hasattr(self, "_shutdown"):
            self._shutdown.set()
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        await super().terminate()

    def meta(self) -> PlatformMeta:
        return PlatformMeta(
            name="daw_agent",
            description="Embedded DAW/VST agent chat adapter",
            id="daw_agent",
        )


def normalize_daw_project_session_id(project_session_id: str) -> str:
    session_id = str(project_session_id or "").strip()
    return session_id or DEFAULT_DAW_PROJECT_SESSION_ID


def normalize_daw_workspace(workspace: str) -> str:
    value = str(workspace or "").strip().lower()
    return value if value in {"atri_studio", "host_project"} else DEFAULT_DAW_WORKSPACE


def normalize_daw_host_context(
    host_context: object,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """Return bounded DAW metadata safe to include as untrusted prompt context."""
    if host_context in (None, ""):
        return {}
    if not isinstance(host_context, dict):
        if strict:
            raise ValueError("host_context must be an object")
        return {}

    if strict:
        _validate_daw_host_context_bounds(host_context)

    normalized: dict[str, Any] = {}
    for key, value in host_context.items():
        if key not in DAW_HOST_CONTEXT_ALLOWED_KEYS:
            if strict:
                raise ValueError(f"unsupported host_context field: {key}")
            continue
        try:
            normalized_value = _normalize_daw_host_context_value(key, value)
        except (TypeError, ValueError):
            if strict:
                raise
            continue
        if normalized_value is not None:
            normalized[key] = normalized_value

    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_DAW_HOST_CONTEXT_JSON_BYTES:
        if strict:
            raise ValueError("host_context is too large")
        return {}
    return normalized


def _validate_daw_host_context_bounds(host_context: dict[str, Any]) -> None:
    if len(host_context) > _MAX_DAW_HOST_CONTEXT_KEYS:
        raise ValueError("host_context has too many keys")

    value_count = 0
    total_string_chars = 0
    stack: list[tuple[object, int]] = [(host_context, 0)]
    while stack:
        value, depth = stack.pop()
        value_count += 1
        if value_count > _MAX_DAW_HOST_CONTEXT_VALUES:
            raise ValueError("host_context contains too many values")

        if isinstance(value, dict):
            if depth >= _MAX_DAW_HOST_CONTEXT_DEPTH:
                raise ValueError("host_context is too deeply nested")
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError("host_context keys must be strings")
                if depth == 0 and key not in DAW_HOST_CONTEXT_ALLOWED_KEYS:
                    raise ValueError(f"unsupported host_context field: {key}")
                total_string_chars += _checked_host_context_string_chars(key)
                stack.append((child, depth + 1))
            continue

        if isinstance(value, list):
            if depth >= _MAX_DAW_HOST_CONTEXT_DEPTH:
                raise ValueError("host_context is too deeply nested")
            stack.extend((child, depth + 1) for child in value)
            continue

        if isinstance(value, str):
            total_string_chars += _checked_host_context_string_chars(value)
            if total_string_chars > _MAX_DAW_HOST_CONTEXT_TOTAL_STRING_CHARS:
                raise ValueError("host_context is too large")


def _checked_host_context_string_chars(value: str) -> int:
    if len(value) > _MAX_DAW_HOST_CONTEXT_STRING_CHARS:
        raise ValueError("host_context string value is too long")
    if any(ch in value for ch in "\r\n") or any(ord(ch) < 32 for ch in value):
        raise ValueError("host_context string value contains control characters")
    return len(value)


def _normalize_daw_host_context_value(key: str, value: object) -> Any:
    if key in _DAW_HOST_CONTEXT_STRING_KEYS:
        text = _normalize_daw_host_context_string(value)
        if text is None:
            return None
        if key == "workspace":
            return normalize_daw_workspace(text)
        return text
    if key in _DAW_HOST_CONTEXT_NUMBER_KEYS:
        return _normalize_daw_host_context_number(value)
    if key == "block_size":
        return _normalize_daw_host_context_int(value, min_value=1, max_value=262_144)
    if key == "is_playing":
        if not isinstance(value, bool):
            raise ValueError("host_context is_playing must be a boolean")
        return value
    if key == "time_signature":
        return _normalize_daw_time_signature(value)
    raise ValueError(f"unsupported host_context field: {key}")


def _normalize_daw_host_context_string(value: object) -> str | None:
    if not isinstance(value, str):
        raise ValueError("host_context string value must be a string")
    text = str(value or "").strip()
    if not text:
        return None
    _checked_host_context_string_chars(text)
    return text


def _normalize_daw_host_context_number(value: object) -> int | float:
    if isinstance(value, bool):
        raise ValueError("host_context number must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("host_context number must be finite")
    return int(number) if number.is_integer() else number


def _normalize_daw_host_context_int(
    value: object,
    *,
    min_value: int,
    max_value: int,
) -> int:
    if isinstance(value, bool):
        raise ValueError("host_context integer must be numeric")
    number = int(value)
    if number < min_value or number > max_value:
        raise ValueError("host_context integer is out of range")
    return number


def _normalize_daw_time_signature(value: object) -> list[int]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("host_context time_signature must be a two-item list")
    numerator = _normalize_daw_host_context_int(value[0], min_value=1, max_value=64)
    denominator = _normalize_daw_host_context_int(value[1], min_value=1, max_value=64)
    return [numerator, denominator]
