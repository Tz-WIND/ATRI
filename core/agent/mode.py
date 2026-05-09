"""Shared PLAN/AGENT mode state for ATRI agents."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Literal

AgentMode = Literal["plan", "agent"]

VALID_AGENT_MODES: tuple[AgentMode, ...] = ("plan", "agent")


def normalize_agent_mode(value: object) -> AgentMode:
    mode = str(value or "agent").strip().lower()
    if mode not in VALID_AGENT_MODES:
        raise ValueError("mode must be one of: plan, agent")
    return mode  # type: ignore[return-value]


class AgentModeController:
    """Thread-safe runtime mode shared by agents and the dashboard."""

    def __init__(
        self,
        mode: object = "agent",
        *,
        on_change: Callable[[AgentMode, str, str], None] | None = None,
    ):
        self._mode = normalize_agent_mode(mode)
        self._lock = threading.Lock()
        self._on_change = on_change

    @property
    def mode(self) -> AgentMode:
        with self._lock:
            return self._mode

    def set_mode(
        self,
        mode: object,
        *,
        source: str = "agent",
        reason: str = "",
    ) -> tuple[AgentMode, bool]:
        next_mode = normalize_agent_mode(mode)
        source = str(source or "agent").strip() or "agent"
        reason = str(reason or "").strip()
        changed = False
        with self._lock:
            if self._mode != next_mode:
                self._mode = next_mode
                changed = True
        if changed and self._on_change:
            self._on_change(next_mode, source, reason)
        return next_mode, changed
