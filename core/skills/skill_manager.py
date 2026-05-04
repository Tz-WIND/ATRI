"""ATRI skill management.

Local-skills-only SkillManager. Reads SKILL.md files from a configurable
skills_root directory, parses YAML frontmatter for name/description, and
generates the system-prompt skills section following the Anthropic
progressive-disclosure pattern.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

_SKILL_NAME_RE = re.compile(r"^[\w.-]+$")

# Regex for sanitizing paths used in prompt examples -- only allow
# safe path characters to prevent prompt injection via crafted skill paths.
_SAFE_PATH_RE = re.compile(r"[^\w./ ,()'\-]", re.UNICODE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F]")


def _is_ignored_zip_entry(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if not parts:
        return True
    return parts[0] == "__MACOSX"


def _normalize_skill_markdown_path(skill_dir: Path) -> Path | None:
    """Return the canonical ``SKILL.md`` path for a skill directory.

    If only legacy ``skill.md`` exists, it is renamed to ``SKILL.md`` in-place.
    """
    canonical = skill_dir / "SKILL.md"
    entries: set[str] = set()
    if skill_dir.exists():
        entries = {entry.name for entry in skill_dir.iterdir()}
    if "SKILL.md" in entries:
        return canonical
    legacy = skill_dir / "skill.md"
    if "skill.md" not in entries:
        return None
    try:
        tmp = skill_dir / f".{uuid.uuid4().hex}.tmp_skill_md"
        legacy.rename(tmp)
        tmp.rename(canonical)
    except OSError:
        return legacy
    return canonical


def _parse_frontmatter_description(text: str) -> str:
    """Extract the ``description`` value from YAML frontmatter.

    Expects the standard SKILL.md format::

        ---
        name: my-skill
        description: What this skill does and when to use it.
        ---
    """
    if not text.startswith("---"):
        return ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return ""

    frontmatter = "\n".join(lines[1:end_idx])
    try:
        payload = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return ""
    if not isinstance(payload, dict):
        return ""

    description = payload.get("description", "")
    if not isinstance(description, str):
        return ""
    return description.strip()


def _sanitize_prompt_path_for_prompt(path: str) -> str:
    """Remove control chars, backticks, and unsafe Unicode from a path."""
    if not path:
        return ""
    path = path.replace("`", "")
    path = _CONTROL_CHARS_RE.sub("", path)
    return _SAFE_PATH_RE.sub("", path)


def _sanitize_prompt_description(description: str) -> str:
    """Remove control chars and backticks; collapse whitespace."""
    description = description.replace("`", "")
    description = _CONTROL_CHARS_RE.sub(" ", description)
    return " ".join(description.split())


def _sanitize_skill_display_name(name: str) -> str:
    """Return the name if it matches the safe pattern, else a sentinel."""
    if _SKILL_NAME_RE.fullmatch(name):
        return name
    return "<invalid_skill_name>"


def _build_skill_read_command_example(path: str) -> str:
    """Generate a shell command to read SKILL.md on the current platform."""
    if path == "<skills_root>/<skill_name>/SKILL.md":
        return f"cat {path}"
    if os.name == "nt":
        command = "type"
        path_arg = f'"{os.path.normpath(path)}"'
    else:
        command = "cat"
        path_arg = shlex.quote(path)
    return f"{command} {path_arg}"


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


@dataclass
class SkillInfo:
    name: str
    description: str
    path: str        # absolute path to SKILL.md
    active: bool


def build_skills_prompt(skills: list[SkillInfo]) -> str:
    """Build the skills section of the system prompt.

    Only ``name`` and ``description`` are shown upfront; the LLM must read
    the full ``SKILL.md`` before execution (progressive disclosure).
    """
    if not skills:
        return ""
    skills_lines: list[str] = []
    example_path = ""
    for skill in skills:
        display_name = _sanitize_skill_display_name(skill.name)
        description = _sanitize_prompt_description(
            skill.description or "No description"
        )
        rendered_path = _sanitize_prompt_path_for_prompt(skill.path)
        if not rendered_path:
            rendered_path = "<skills_root>/<skill_name>/SKILL.md"

        skills_lines.append(
            f"- **{display_name}**: {description}\n  File: `{rendered_path}`"
        )
        if not example_path:
            example_path = rendered_path

    skills_block = "\n".join(skills_lines)

    if not example_path or example_path == "<skills_root>/<skill_name>/SKILL.md":
        example_path = "<skills_root>/<skill_name>/SKILL.md"
    else:
        example_path = _sanitize_prompt_path_for_prompt(example_path)
        example_path = example_path or "<skills_root>/<skill_name>/SKILL.md"

    example_command = _build_skill_read_command_example(example_path)

    return (
        "## Skills\n\n"
        "You have specialized skills -- reusable instruction bundles stored "
        "in `SKILL.md` files. Each skill has a **name** and a **description** "
        "that tells you what it does and when to use it.\n\n"
        "### Available skills\n\n"
        f"{skills_block}\n\n"
        "### Skill rules\n\n"
        "1. **Discovery** -- The list above is the complete skill inventory "
        "for this session. Full instructions are in the referenced "
        "`SKILL.md` file.\n"
        "2. **When to trigger** -- Use a skill if the user names it "
        "explicitly, or if the task clearly matches the skill's description. "
        "*Never silently skip a matching skill* -- either use it or briefly "
        "explain why you chose not to.\n"
        "3. **Mandatory grounding** -- Before executing any skill you MUST "
        "first read its `SKILL.md` by running a shell command compatible "
        "with the current runtime shell and using the **absolute path** "
        f"shown above (e.g. `{example_command}`). "
        "Never rely on memory or assumptions about a skill's content.\n"
        "4. **Progressive disclosure** -- Load only what is directly "
        "referenced from `SKILL.md`:\n"
        "   - If `scripts/` exist, prefer running or patching them over "
        "rewriting code from scratch.\n"
        "   - If `assets/` or templates exist, reuse them.\n"
        "   - Do NOT bulk-load every file in the skill directory.\n"
        "5. **Coordination** -- When multiple skills apply, pick the minimal "
        "set needed. Announce which skill(s) you are using and why "
        "(one short line).\n"
        "6. **Context hygiene** -- Avoid deep reference chasing; open only "
        "files that are directly linked from `SKILL.md`.\n"
        "7. **Failure handling** -- If a skill cannot be applied, state the "
        "issue clearly and continue with the best alternative.\n"
    )


class SkillManager:
    """Manages local skills stored as ``SKILL.md`` files on disk.

    Active/inactive state is persisted in the ``skills_config`` dict
    (typically ``config.yaml``'s ``skills`` key).
    """

    def __init__(
        self,
        skills_root: str = "skills",
        skills_config: dict | None = None,
    ) -> None:
        self.skills_root = str(skills_root)
        self.skills_config: dict = skills_config if skills_config is not None else {}
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        os.makedirs(self.skills_root, exist_ok=True)

    # ------------------------------------------------------------------
    # listing
    # ------------------------------------------------------------------

    def list_skills(self, active_only: bool = False) -> list[SkillInfo]:
        """List all skills, merging filesystem state with config."""
        skills_by_name: dict[str, SkillInfo] = {}
        modified = False
        root = Path(self.skills_root)

        if root.exists():
            for entry in sorted(root.iterdir()):
                if not entry.is_dir():
                    continue
                skill_name = entry.name
                skill_md = _normalize_skill_markdown_path(entry)
                if skill_md is None:
                    continue

                active = self.skills_config.get(skill_name, {}).get("active", True)
                if skill_name not in self.skills_config:
                    self.skills_config[skill_name] = {"active": active}
                    modified = True

                if active_only and not active:
                    continue

                description = ""
                try:
                    content = skill_md.read_text(encoding="utf-8", errors="replace")
                    description = _parse_frontmatter_description(content)
                except Exception:
                    description = ""

                path_str = str(skill_md.resolve()).replace("\\", "/")
                skills_by_name[skill_name] = SkillInfo(
                    name=skill_name,
                    description=description,
                    path=path_str,
                    active=active,
                )

        return [skills_by_name[n] for n in sorted(skills_by_name)]

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------

    def set_skill_active(self, name: str, active: bool) -> None:
        """Toggle a skill's active flag in config."""
        self.skills_config.setdefault(name, {})["active"] = bool(active)

    def delete_skill(self, name: str) -> None:
        """Delete a skill directory and its config entry."""
        skill_dir = Path(self.skills_root) / name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        self.skills_config.pop(name, None)

    # ------------------------------------------------------------------
    # zip install
    # ------------------------------------------------------------------

    def install_skill_from_zip(
        self,
        zip_path: str,
        *,
        overwrite: bool = True,
        skill_name_hint: str | None = None,
    ) -> str:
        """Install one or more skills from a zip archive.

        Supports two layouts:
        - **root mode**: ``SKILL.md`` at the zip root (uses zip filename as skill name)
        - **dir mode**: one or more subdirectories each containing ``SKILL.md``

        Returns a comma-separated list of installed skill names.
        """
        zip_path_obj = Path(zip_path)
        if not zip_path_obj.exists():
            raise FileNotFoundError(f"Zip file not found: {zip_path}")
        if not zipfile.is_zipfile(zip_path):
            raise ValueError("Uploaded file is not a valid zip archive.")

        installed_skills: list[str] = []

        with zipfile.ZipFile(zip_path) as zf:
            names = [
                name
                for name in (entry.replace("\\", "/") for entry in zf.namelist())
                if name and not _is_ignored_zip_entry(name)
            ]
            file_names = [name for name in names if name and not name.endswith("/")]
            if not file_names:
                raise ValueError("Zip archive is empty.")

            has_root_skill_md = any(
                len(parts := PurePosixPath(name).parts) == 1
                and parts[0] in {"SKILL.md", "skill.md"}
                for name in file_names
            )
            root_mode = has_root_skill_md

            archive_skill_name = None
            if skill_name_hint is not None:
                archive_skill_name = _normalize_skill_name(skill_name_hint)
                if archive_skill_name and not _SKILL_NAME_RE.fullmatch(
                    archive_skill_name
                ):
                    raise ValueError("Invalid skill name.")

            # Security: validate all paths before extraction
            for name in names:
                if not name:
                    continue
                if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                    raise ValueError("Zip archive contains absolute paths.")
                if ".." in PurePosixPath(name).parts:
                    raise ValueError("Zip archive contains invalid relative paths.")

            with tempfile.TemporaryDirectory() as tmp_dir:
                for member in zf.infolist():
                    member_name = member.filename.replace("\\", "/")
                    if not member_name or _is_ignored_zip_entry(member_name):
                        continue
                    zf.extract(member, tmp_dir)

                if root_mode:
                    archive_hint = _normalize_skill_name(
                        skill_name_hint or zip_path_obj.stem
                    )
                    if not archive_hint or not _SKILL_NAME_RE.fullmatch(archive_hint):
                        raise ValueError("Invalid skill name.")
                    skill_name = archive_hint

                    src_dir = Path(tmp_dir)
                    normalized_path = _normalize_skill_markdown_path(src_dir)
                    if normalized_path is None:
                        raise ValueError(
                            "SKILL.md not found in the root of the zip archive."
                        )

                    dest_dir = Path(self.skills_root) / skill_name
                    if dest_dir.exists() and overwrite:
                        shutil.rmtree(dest_dir)
                    elif dest_dir.exists() and not overwrite:
                        raise FileExistsError(f"Skill {skill_name} already exists.")

                    shutil.move(str(src_dir), str(dest_dir))
                    self.set_skill_active(skill_name, True)
                    installed_skills.append(skill_name)

                else:
                    top_dirs = {
                        PurePosixPath(n).parts[0] for n in file_names if n.strip()
                    }

                    for archive_root_name in top_dirs:
                        archive_root_name_normalized = _normalize_skill_name(
                            archive_root_name
                        )

                        if (
                            f"{archive_root_name}/SKILL.md" not in file_names
                            and f"{archive_root_name}/skill.md" not in file_names
                        ):
                            continue

                        if archive_root_name in {".", "..", ""} or not (
                            _SKILL_NAME_RE.fullmatch(archive_root_name_normalized)
                        ):
                            continue

                        if archive_skill_name and len(top_dirs) == 1:
                            skill_name = archive_skill_name
                        else:
                            skill_name = archive_root_name_normalized

                        src_dir = Path(tmp_dir) / archive_root_name
                        normalized_path = _normalize_skill_markdown_path(src_dir)
                        if normalized_path is None:
                            continue

                        dest_dir = Path(self.skills_root) / skill_name
                        if dest_dir.exists():
                            if not overwrite:
                                raise FileExistsError(
                                    f"Skill {skill_name} already exists."
                                )
                            shutil.rmtree(dest_dir)

                        shutil.move(str(src_dir), str(dest_dir))
                        self.set_skill_active(skill_name, True)
                        installed_skills.append(skill_name)

        if not installed_skills:
            raise ValueError(
                "No valid SKILL.md found in any folder of the zip archive."
            )

        return ", ".join(installed_skills)


def _normalize_skill_name(name: str) -> str:
    raw = str(name or "")
    return re.sub(r"\s+", "_", raw.strip())
