"""Unified message models for cross-platform communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(Enum):
    FRIEND_MESSAGE = "friend"
    GROUP_MESSAGE = "group"


@dataclass
class Plain:
    text: str
    type: str = "plain"


@dataclass
class Image:
    url: str = ""
    file: str = ""
    mime_type: str = ""
    size: int = 0
    type: str = "image"


@dataclass
class At:
    qq: str = ""
    name: str = ""
    type: str = "at"


@dataclass
class Reply:
    id: str = ""
    text: str = ""
    type: str = "reply"


@dataclass
class Face:
    id: str = ""
    type: str = "face"


@dataclass
class File:
    name: str = ""
    url: str = ""
    type: str = "file"


MessageComponent = Plain | Image | At | Reply | Face | File
MessageChain = list[MessageComponent]


@dataclass
class Sender:
    user_id: str = ""
    nickname: str = ""


@dataclass
class MessageEvent:
    """Unified message event across all platforms."""

    message_str: str = ""
    message_chain: MessageChain = field(default_factory=list)
    message_type: MessageType = MessageType.FRIEND_MESSAGE
    sender: Sender = field(default_factory=Sender)
    session_id: str = ""
    group_id: str = ""
    self_id: str = ""
    platform_name: str = ""
    raw_event: Any = None

    # Pipeline control
    is_wake: bool = False

    def __post_init__(self):
        self._stopped = False
        self._result_text = ""
        self._result_chain = []
        self._extras: dict = {}

    @property
    def unified_msg_origin(self) -> str:
        return f"{self.platform_name}:{self.message_type.value}:{self.session_id}"

    def is_private(self) -> bool:
        return self.message_type == MessageType.FRIEND_MESSAGE

    def stop(self):
        self._stopped = True

    def is_stopped(self) -> bool:
        return self._stopped

    def set_result(self, text: str):
        self._result_text = text
        self._result_chain = [Plain(text=text)]

    def set_result_chain(self, chain: MessageChain):
        self._result_chain = chain
        texts = [c.text for c in chain if isinstance(c, Plain)]
        self._result_text = "".join(texts)

    def get_result_text(self) -> str:
        return self._result_text

    def get_result_chain(self) -> MessageChain:
        return self._result_chain

    def get_sender_name(self) -> str:
        return self.sender.nickname or self.sender.user_id

    def get_message_outline(self) -> str:
        parts = []
        for c in self.message_chain:
            if isinstance(c, Plain):
                parts.append(c.text)
            elif isinstance(c, Image):
                parts.append("[图片]")
            elif isinstance(c, At):
                parts.append(f"[@{c.name or c.qq}]")
            elif isinstance(c, Reply):
                parts.append("[回复]")
            elif isinstance(c, Face):
                parts.append(f"[表情:{c.id}]")
            elif isinstance(c, File):
                parts.append(f"[文件:{c.name}]")
        return " ".join(parts)


# Session ID helpers — shared between ProcessStage and Dashboard

WEBChat_SESSION_PREFIX = "webchat:friend:"
DAW_AGENT_SESSION_PREFIX = "daw_agent:friend:"

_BARE_SESSION_ID_PREFIXES = (
    DAW_AGENT_SESSION_PREFIX,
    WEBChat_SESSION_PREFIX,
)


def normalize_session_id(display_id: str) -> str:
    """Convert a display session ID to the internal unified_msg_origin key.

    If display_id already contains ':' it is assumed to be already
    in internal form and returned as-is.
    """
    if ":" in display_id:
        return display_id
    return f"{WEBChat_SESSION_PREFIX}{display_id}"


def resolve_session_id(
    display_id: str,
    known_ids: set[str] | frozenset[str] | None = None,
) -> str:
    """Resolve a bare or internal session ID to unified_msg_origin form.

    Internal IDs (containing ``:``) are returned unchanged. Bare display IDs
    prefer a matching entry in *known_ids* (daw_agent before webchat), then
    fall back to the webchat prefix.
    """
    if ":" in display_id:
        return display_id
    if known_ids:
        for prefix in _BARE_SESSION_ID_PREFIXES:
            candidate = f"{prefix}{display_id}"
            if candidate in known_ids:
                return candidate
    return normalize_session_id(display_id)


def display_session_id(internal_id: str) -> str:
    """Convert an internal unified_msg_origin key to a display session ID."""
    if internal_id.startswith(WEBChat_SESSION_PREFIX):
        return internal_id[len(WEBChat_SESSION_PREFIX) :]
    return internal_id
