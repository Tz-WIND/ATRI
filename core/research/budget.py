"""Thread-safe, turn-scoped DeepResearch resource accounting."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .policy import ResearchPolicy


@dataclass(frozen=True)
class BudgetDecision:
    """Result of an atomic budget reservation."""

    allowed: bool
    reason: str = ""
    snapshot: dict[str, Any] = field(default_factory=dict)


class ResearchBudget:
    """Share research limits safely between the parent and all child agents."""

    def __init__(
        self,
        policy: ResearchPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self._clock = clock
        self._lock = threading.RLock()
        self.started_at = clock()
        self.deadline_at = self.started_at + policy.timeout_seconds
        self.synthesis_deadline_at = self.deadline_at - policy.synthesis_reserve_seconds
        self.gap_rounds = 0
        self.research_tool_calls = 0
        self.web_fetches = 0
        self.active_subagents = 0
        self.total_subagents = 0
        self.cancelled = False
        self._web_fetch_urls: set[str] = set()

    def reserve_tool_call(self, tool_name: str) -> BudgetDecision:
        """Atomically reserve one RAG, GraphRAG, or Web search call."""

        del tool_name  # Tool name is deliberately not persisted in the budget snapshot.
        with self._lock:
            blocked = self._blocked_reason_locked()
            if blocked:
                return self._decision_locked(False, blocked)
            if self.research_tool_calls >= self.policy.max_research_tool_calls:
                return self._decision_locked(False, "tool_calls_exhausted")
            self.research_tool_calls += 1
            return self._decision_locked(True)

    def reserve_web_fetch(self, canonical_url: str) -> BudgetDecision:
        """Reserve a fetch call and one unique page slot for a canonical URL."""

        with self._lock:
            blocked = self._blocked_reason_locked()
            if blocked:
                return self._decision_locked(False, blocked)
            if self.research_tool_calls >= self.policy.max_research_tool_calls:
                return self._decision_locked(False, "tool_calls_exhausted")

            repeated = canonical_url in self._web_fetch_urls
            if not repeated and self.web_fetches >= self.policy.max_web_fetches:
                return self._decision_locked(False, "web_fetches_exhausted")

            self.research_tool_calls += 1
            if not repeated:
                self._web_fetch_urls.add(canonical_url)
                self.web_fetches += 1
            return self._decision_locked(True, "repeat_url" if repeated else "")

    def reserve_gap_round(self) -> BudgetDecision:
        with self._lock:
            blocked = self._blocked_reason_locked()
            if blocked:
                return self._decision_locked(False, blocked)
            if self.gap_rounds >= self.policy.max_gap_rounds:
                return self._decision_locked(False, "gap_rounds_exhausted")
            self.gap_rounds += 1
            return self._decision_locked(True)

    def reserve_subagents(self, count: int) -> BudgetDecision:
        with self._lock:
            blocked = self._blocked_reason_locked()
            if blocked:
                return self._decision_locked(False, blocked)
            if count < 1:
                return self._decision_locked(False, "invalid_subagent_count")
            if self.active_subagents + count > self.policy.max_parallel_subagents:
                return self._decision_locked(False, "subagent_limit")
            self.active_subagents += count
            self.total_subagents += count
            return self._decision_locked(True)

    def release_subagents(self, count: int) -> None:
        with self._lock:
            self.active_subagents = max(0, self.active_subagents - max(0, int(count)))

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True

    @property
    def synthesis_required(self) -> bool:
        with self._lock:
            return self._clock() >= self.synthesis_deadline_at

    def seconds_until_synthesis(self) -> float:
        with self._lock:
            return max(0.0, self.synthesis_deadline_at - self._clock())

    def seconds_until_deadline(self) -> float:
        with self._lock:
            return max(0.0, self.deadline_at - self._clock())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _blocked_reason_locked(self) -> str:
        if self.cancelled:
            return "cancelled"
        now = self._clock()
        if now >= self.deadline_at:
            return "deadline_exceeded"
        if now >= self.synthesis_deadline_at:
            return "synthesis_reserved"
        return ""

    def _decision_locked(self, allowed: bool, reason: str = "") -> BudgetDecision:
        return BudgetDecision(
            allowed=allowed,
            reason=reason,
            snapshot=self._snapshot_locked(),
        )

    def _snapshot_locked(self) -> dict[str, Any]:
        now = self._clock()
        if self.cancelled:
            state = "cancelled"
        elif now >= self.deadline_at:
            state = "deadline_exceeded"
        elif now >= self.synthesis_deadline_at:
            state = "synthesizing"
        else:
            state = "researching"
        return {
            "state": state,
            "gap_rounds": self.gap_rounds,
            "max_gap_rounds": self.policy.max_gap_rounds,
            "research_tool_calls": self.research_tool_calls,
            "max_research_tool_calls": self.policy.max_research_tool_calls,
            "web_fetches": self.web_fetches,
            "max_web_fetches": self.policy.max_web_fetches,
            "active_subagents": self.active_subagents,
            "total_subagents": self.total_subagents,
            "max_parallel_subagents": self.policy.max_parallel_subagents,
            "remaining_seconds": max(0.0, self.deadline_at - now),
            "research_remaining_seconds": max(0.0, self.synthesis_deadline_at - now),
            "cancelled": self.cancelled,
        }
