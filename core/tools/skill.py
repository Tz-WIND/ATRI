"""Skill loading tool."""

from __future__ import annotations

from typing import Any

from core.skills import SkillInfo, SkillManager

from .base import Tool


class LoadSkillTool(Tool):
    name = "load_skill"
    description = (
        "Load a skill's SKILL.md body, or one companion file by relative path. "
        "Use this before applying any skill listed in the system prompt."
    )
    parameters = {  # noqa: RUF012
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name exactly as shown in the Skills section.",
            },
            "file": {
                "type": "string",
                "description": (
                    "Optional companion file path relative to the skill directory. "
                    "Omit this to load SKILL.md."
                ),
            },
        },
        "required": ["name"],
    }

    def __init__(self, workspace: str = ".", skill_manager: SkillManager | None = None):
        super().__init__(workspace)
        self.skill_manager = skill_manager or SkillManager(workspace=workspace)

    def execute(self, name: str, file: str | None = None, **kwargs: Any) -> str:
        try:
            loaded = self.skill_manager.load_skill(name, file=file, active_only=True)
        except KeyError:
            available = [skill.name for skill in self.skill_manager.list_skills(active_only=True)]
            if available:
                return f"Error: skill '{name}' not found. Available skills: {', '.join(available)}"
            return "Error: no active skills are available."
        except (FileNotFoundError, PermissionError, ValueError) as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"

        if loaded.companion_file:
            return self._format_companion_file(loaded.loaded_path, loaded.content)
        return self._format_skill_body(loaded.info, loaded.loaded_path, loaded.content)

    def _format_skill_body(self, info: SkillInfo, path: str, content: str) -> str:
        parts = [f"# Skill: {info.name}"]
        if info.description:
            parts.extend(["", f"> {info.description}"])
        parts.extend(["", f"Source: `{path}`", "", "## SKILL.md", "", content.strip()])

        if info.companion_files:
            parts.extend(
                [
                    "",
                    "## Companion files",
                    "",
                    "Use `load_skill` with the same `name` and one of these `file` values "
                    "only when the skill instructions require it.",
                    "",
                ]
            )
            for file in info.companion_files:
                parts.append(f"- `{file}`")

        return "\n".join(parts).rstrip() + "\n"

    def _format_companion_file(self, path: str, content: str) -> str:
        return f"# Skill Companion File\n\nSource: `{path}`\n\n```\n{content.rstrip()}\n```\n"
