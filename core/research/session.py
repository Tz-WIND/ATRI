"""Composition root and lifecycle for one DeepResearch turn."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, ClassVar

from .budget import BudgetDecision, ResearchBudget
from .evidence import EvidenceItem, EvidenceLedger
from .policy import ResearchPolicy
from .report import ResearchReportValidator

ResearchEventCallback = Callable[[str, dict[str, Any]], None]


class ResearchSession:
    """Share policy, runtime accounting, evidence, and events for one turn."""

    VALID_PHASES: ClassVar[set[str]] = {
        "created",
        "planning",
        "gathering",
        "verifying",
        "synthesizing",
        "completed",
    }
    VALID_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        "created": {"planning", "synthesizing"},
        "planning": {"gathering", "synthesizing"},
        "gathering": {"verifying", "synthesizing"},
        "verifying": {"gathering", "synthesizing"},
        "synthesizing": {"completed"},
        "completed": set(),
    }
    TERMINAL_BRANCH_STATUSES: ClassVar[set[str]] = {"completed", "failed", "cancelled"}

    def __init__(
        self,
        *,
        policy: ResearchPolicy,
        turn_id: str,
        session_id: str,
        original_user_request: str,
        services: Any = None,
        report_export_allowed: bool = False,
        event_callback: ResearchEventCallback | None = None,
        budget: ResearchBudget | None = None,
        ledger: EvidenceLedger | None = None,
    ) -> None:
        self.policy = policy
        self.turn_id = str(turn_id)
        self.session_id = str(session_id)
        self.original_user_request = str(original_user_request)
        self.services = services
        self.report_export_allowed = bool(report_export_allowed and policy.allow_report_export)
        self.budget = budget or ResearchBudget(policy)
        self._event_callback = event_callback
        self._lock = threading.RLock()
        self.current_phase = "created"
        self.completed_questions: list[str] = []
        self.open_questions: list[str] = []
        self.conflicts: list[str] = []
        self.note = ""
        self.branches: dict[str, dict[str, Any]] = {}
        self._started = False
        self._cancelled_event_emitted = False
        self._web_cache: dict[str, Any] = {}
        self.ledger = ledger or EvidenceLedger(on_change=self._on_evidence_change)
        if ledger is not None:
            ledger.set_on_change(self._on_evidence_change)
        self.report_validator = ResearchReportValidator(self.ledger)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self.emit(
            "research_started",
            {
                "phase": self.current_phase,
                "report_export_allowed": self.report_export_allowed,
            },
        )
        self.emit_budget()

    def checkpoint(
        self,
        *,
        phase: str,
        completed_questions: list[str] | None = None,
        open_questions: list[str] | None = None,
        conflicts: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        target = str(phase or "").strip().lower()
        if target not in self.VALID_PHASES - {"created", "completed"}:
            return {"ok": False, "error": "invalid_phase", "phase": self.current_phase}

        with self._lock:
            previous = self.current_phase
            if target != previous and target not in self.VALID_TRANSITIONS.get(previous, set()):
                return {
                    "ok": False,
                    "error": "invalid_phase_transition",
                    "phase": previous,
                    "requested_phase": target,
                }
            if previous == "verifying" and target == "gathering":
                decision = self.budget.reserve_gap_round()
                if not decision.allowed:
                    self.current_phase = "synthesizing"
                    self.emit_phase(reason=decision.reason)
                    self.emit_budget()
                    return {
                        "ok": False,
                        "error": "budget_exhausted",
                        "reason": decision.reason,
                        "phase": self.current_phase,
                    }
            self.current_phase = target
            if completed_questions is not None:
                self.completed_questions = self._clean_list(completed_questions)
            if open_questions is not None:
                self.open_questions = self._clean_list(open_questions)
            if conflicts is not None:
                self.conflicts = self._clean_list(conflicts)
            self.note = str(note or "").strip()

        self.emit_phase()
        self.emit_budget()
        return {
            "ok": True,
            "phase": target,
            "budget": self.budget.snapshot(),
        }

    def reserve_tool_call(self, tool_name: str) -> BudgetDecision:
        decision = self.budget.reserve_tool_call(tool_name)
        self._after_budget_decision(decision)
        return decision

    def reserve_web_fetch(self, canonical_url: str) -> BudgetDecision:
        decision = self.budget.reserve_web_fetch(canonical_url)
        self._after_budget_decision(decision)
        return decision

    def reserve_subagents(self, count: int) -> BudgetDecision:
        decision = self.budget.reserve_subagents(count)
        self._after_budget_decision(decision)
        return decision

    def release_subagents(self, count: int) -> None:
        self.budget.release_subagents(count)
        self.emit_budget()

    def begin_synthesis(self, *, reason: str = "") -> None:
        with self._lock:
            if self.current_phase in {"synthesizing", "completed"}:
                return
            self.current_phase = "synthesizing"
        self.emit_phase(reason=reason)
        self.emit_budget()

    def complete(self) -> None:
        with self._lock:
            if self.current_phase == "completed":
                return
            self.current_phase = "completed"
        self.emit(
            "research_completed",
            {
                "phase": "completed",
                "evidence_count": len(self.ledger),
                "budget": self.budget.snapshot(),
            },
        )

    def cancel(self, *, reason: str = "user_cancelled") -> None:
        self.budget.cancel()
        services = self.services
        if services is not None and hasattr(services, "cancel_pending"):
            services.cancel_pending(owner_id=self.turn_id)
        with self._lock:
            if self._cancelled_event_emitted:
                return
            self._cancelled_event_emitted = True
        self.emit(
            "research_cancelled",
            {
                "phase": self.current_phase,
                "reason": reason,
                "budget": self.budget.snapshot(),
            },
        )

    def register_branch(self, branch_id: str, task: str) -> None:
        clean_id = str(branch_id or "").strip()
        with self._lock:
            self.branches[clean_id] = {"task": str(task or ""), "status": "running"}
        self.emit(
            "research_subagent_started",
            {"branch_id": clean_id, "task": str(task or "")[:240]},
        )

    def finish_branch(self, branch_id: str, *, status: str, error: str = "") -> bool:
        clean_id = str(branch_id or "").strip()
        with self._lock:
            branch = self.branches.setdefault(clean_id, {})
            current_status = branch.get("status")
            if current_status == "cancelled" or (
                current_status in self.TERMINAL_BRANCH_STATUSES and status != "cancelled"
            ):
                return False
            branch["status"] = status
            if error:
                branch["error"] = str(error)[:240]
        self.emit(
            "research_subagent_finished",
            {"branch_id": clean_id, "status": status, "error": str(error)[:240]},
        )
        return True

    def get_cached_web(self, canonical_url: str) -> Any | None:
        with self._lock:
            return self._web_cache.get(canonical_url)

    def cache_web(self, canonical_url: str, content: Any) -> None:
        with self._lock:
            self._web_cache[canonical_url] = content

    def emit_phase(self, *, reason: str = "") -> None:
        self.emit(
            "research_phase",
            {
                "phase": self.current_phase,
                "completed_questions": len(self.completed_questions),
                "open_questions": len(self.open_questions),
                "conflicts": len(self.conflicts),
                "note": self.note[:240],
                "reason": str(reason or ""),
            },
        )

    def emit_budget(self) -> None:
        self.emit("research_budget", self.budget.snapshot())

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self._event_callback:
            return
        safe_payload = {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            **dict(payload),
        }
        try:
            self._event_callback(event_type, safe_payload)
        except Exception:
            # Research correctness must not depend on UI/timeline availability.
            return

    def _after_budget_decision(self, decision: BudgetDecision) -> None:
        if not decision.allowed and decision.reason in {
            "synthesis_reserved",
            "deadline_exceeded",
            "tool_calls_exhausted",
            "web_fetches_exhausted",
            "gap_rounds_exhausted",
        }:
            self.begin_synthesis(reason=decision.reason)
        else:
            self.emit_budget()

    def _on_evidence_change(self, item: EvidenceItem, created: bool) -> None:
        self.emit(
            "research_evidence",
            {
                "citation_id": item.citation_id,
                "kind": item.kind,
                "title": item.title[:160],
                "locator": item.locator[:240],
                "preview": item.excerpt[:240],
                "strength": item.strength,
                "branch_id": item.branch_id,
                "created": created,
                "evidence_count": len(self.ledger),
            },
        )

    @staticmethod
    def _clean_list(values: list[str]) -> list[str]:
        return [str(value).strip() for value in values if str(value).strip()]
