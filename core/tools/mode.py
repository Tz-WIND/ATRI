"""Tool for switching ATRI between PLAN and AGENT modes."""

from __future__ import annotations

from core.agent.mode import AgentModeController

from .base import Tool, ToolCapabilities


class AgentModeTool(Tool):
    name = "set_agent_mode"
    description = (
        "Switch ATRI's operating mode between PLAN and AGENT. "
        "Use PLAN for analysis/design without edits; use AGENT before executing changes."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["plan", "agent"],
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
    ):
        super().__init__(workspace)
        self.mode_controller = mode_controller or AgentModeController()

    def execute(self, mode: str, reason: str = "", **_: object) -> str:
        next_mode, changed = self.mode_controller.set_mode(
            mode,
            source="agent",
            reason=reason,
        )
        label = next_mode.upper()
        if changed:
            suffix = f" Reason: {reason.strip()}" if reason and reason.strip() else ""
            return f"Switched to {label} mode.{suffix}"
        return f"Already in {label} mode."
