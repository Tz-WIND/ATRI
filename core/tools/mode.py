"""Tool for switching ATRI between PLAN, AGENT, and DEEPRESEARCH modes."""

from __future__ import annotations

from collections.abc import Callable

from core.agent.mode import AgentModeController

from .base import Tool, ToolCapabilities


class AgentModeTool(Tool):
    name = "set_agent_mode"
    description = (
        "Switch ATRI's operating mode between PLAN, AGENT, and DEEPRESEARCH. "
        "Use PLAN for analysis/design, AGENT for execution, and DEEPRESEARCH "
        "for read-only evidence gathering and sourced reports."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["plan", "agent", "deepresearch"],
                "description": "Target operating mode.",
            },
            "reason": {
                "type": "string",
                "description": "Short reason shown to the user.",
            },
        },
        "required": ["mode"],
    }
    capabilities = ToolCapabilities(capability="agent.mode")

    def __init__(
        self,
        workspace: str = ".",
        *,
        mode_controller: AgentModeController | None = None,
        current_turn_mode_provider: Callable[[], str | None] | None = None,
    ):
        super().__init__(workspace)
        self.mode_controller = mode_controller or AgentModeController()
        self.current_turn_mode_provider = current_turn_mode_provider

    def execute(self, mode: str, reason: str = "", **_: object) -> str:
        current_turn_mode = (
            self.current_turn_mode_provider() if self.current_turn_mode_provider else None
        )
        next_mode, changed = self.mode_controller.set_mode(
            mode,
            source="agent",
            reason=reason,
        )
        label = next_mode.upper()
        suffix = f" Reason: {reason.strip()}" if reason and reason.strip() else ""
        if current_turn_mode:
            current_label = current_turn_mode.upper()
            if changed:
                return (
                    f"{label} mode scheduled for the next turn. "
                    f"Current turn remains {current_label} mode.{suffix}"
                )
            return (
                f"{label} mode is already scheduled for the next turn. "
                f"Current turn remains {current_label} mode."
            )
        if changed:
            return f"Switched to {label} mode.{suffix}"
        return f"Already in {label} mode."
