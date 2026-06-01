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
from typing import Any, cast

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
        "project_time_beats",
        "bar_position_beats",
        "loop_active",
        "loop_range_beats",
        "selection",
        "host_project_sync",
        "track",
    }
)
_DAW_HOST_CONTEXT_STRING_KEYS = {"host", "workspace", "track"}
_DAW_HOST_CONTEXT_NUMBER_KEYS = {
    "tempo_bpm",
    "sample_rate",
    "project_time_beats",
    "bar_position_beats",
}
_MAX_DAW_HOST_CONTEXT_KEYS = 14
_MAX_DAW_HOST_CONTEXT_DEPTH = 3
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
    stack: list[tuple[object, int, str]] = [(host_context, 0, "")]
    while stack:
        value, depth, field = stack.pop()
        value_count += 1
        if value_count > _MAX_DAW_HOST_CONTEXT_VALUES:
            raise ValueError("host_context contains too many values")

        if isinstance(value, dict):
            if depth >= _MAX_DAW_HOST_CONTEXT_DEPTH:
                raise ValueError("host_context is too deeply nested")
            if depth > 0 and field not in {"selection", "host_project_sync"}:
                raise ValueError("host_context is too deeply nested")
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError("host_context keys must be strings")
                if depth == 0 and key not in DAW_HOST_CONTEXT_ALLOWED_KEYS:
                    raise ValueError(f"unsupported host_context field: {key}")
                total_string_chars += _checked_host_context_string_chars(key)
                stack.append((child, depth + 1, key if depth == 0 else field))
            continue

        if isinstance(value, list):
            if depth >= _MAX_DAW_HOST_CONTEXT_DEPTH:
                raise ValueError("host_context is too deeply nested")
            stack.extend((child, depth + 1, field) for child in value)
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
    if key == "loop_active":
        if not isinstance(value, bool):
            raise ValueError("host_context loop_active must be a boolean")
        return value
    if key == "time_signature":
        return _normalize_daw_time_signature(value)
    if key == "loop_range_beats":
        return _normalize_daw_beat_range(value, "loop_range_beats")
    if key == "selection":
        return _normalize_daw_selection(value)
    if key == "host_project_sync":
        return _normalize_daw_host_project_sync(value)
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
    number = float(cast(Any, value))
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
    number = int(cast(Any, value))
    if number < min_value or number > max_value:
        raise ValueError("host_context integer is out of range")
    return number


def _normalize_daw_time_signature(value: object) -> list[int]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("host_context time_signature must be a two-item list")
    numerator = _normalize_daw_host_context_int(value[0], min_value=1, max_value=64)
    denominator = _normalize_daw_host_context_int(value[1], min_value=1, max_value=64)
    return [numerator, denominator]


def _normalize_daw_beat_range(value: object, field: str) -> list[int | float]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError(f"host_context {field} must be a two-item list")
    start = _normalize_daw_host_context_number(value[0])
    end = _normalize_daw_host_context_number(value[1])
    if start < 0 or end <= start:
        raise ValueError(f"host_context {field} must have non-negative start and end after start")
    return [start, end]


def _normalize_daw_selection(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        raise ValueError("host_context selection must be an object")
    allowed = {"range_beats", "range", "project_track_ids", "host_track_ids"}
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key not in allowed:
            raise ValueError(f"unsupported host_context selection field: {key}")
        target_key = "range_beats" if key == "range" else key
        if target_key == "range_beats":
            normalized[target_key] = _normalize_daw_beat_range(item, "selection.range_beats")
        elif target_key == "project_track_ids":
            normalized[target_key] = _normalize_daw_host_context_int_list(
                item,
                f"selection.{target_key}",
                min_value=1,
            )
        elif target_key == "host_track_ids":
            normalized[target_key] = _normalize_daw_host_context_int_list(
                item,
                f"selection.{target_key}",
                min_value=0,
            )
    return normalized or None


def _normalize_daw_host_project_sync(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        raise ValueError("host_context host_project_sync must be an object")
    allowed = {"status", "format", "filename", "track_count", "midi_clip_count", "note_count"}
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key not in allowed:
            raise ValueError(f"unsupported host_context host_project_sync field: {key}")
        if key == "status":
            status = _normalize_daw_host_context_string(item)
            if status not in {"missing", "pending", "unchanged", "imported", "error"}:
                raise ValueError("host_context host_project_sync status is unsupported")
            normalized[key] = status
        elif key == "format":
            format_name = _normalize_daw_host_context_string(item)
            if format_name != "dawproject":
                raise ValueError("host_context host_project_sync format is unsupported")
            normalized[key] = format_name
        elif key == "filename":
            filename = _normalize_daw_host_context_string(item)
            if filename:
                normalized[key] = filename
        else:
            normalized[key] = _normalize_daw_host_context_int(
                item,
                min_value=0,
                max_value=1_000_000,
            )
    return normalized or None


def _normalize_daw_host_context_int_list(
    value: object,
    field: str,
    *,
    min_value: int,
) -> list[int]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"host_context {field} must be a list")
    if len(value) > 128:
        raise ValueError(f"host_context {field} has too many items")
    result = [
        _normalize_daw_host_context_int(item, min_value=min_value, max_value=1_000_000)
        for item in value
    ]
    return list(dict.fromkeys(result))
