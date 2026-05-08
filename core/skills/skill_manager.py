"""ATRI skill management.

Skills are instruction bundles stored as ``SKILL.md`` files. The manager
discovers them from the configured install directory plus workspace/global
interop locations, keeps the prompt listing compact, and exposes a higher
level load API for the ``load_skill`` tool.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any

import yaml

_SKILL_NAME_RE = re.compile(r"^[\w.-]+$")
_SKILL_ID_RE = re.compile(r"^[^\x00-\x1F\x7F/\\`]{1,128}$")

# Regex for sanitizing paths used in prompt examples -- only allow safe path
# characters to prevent prompt injection via crafted skill paths.
_SAFE_PATH_RE = re.compile(r"[^\w./ ,()'@:\-\[\]]", re.UNICODE)
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F]")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")

_WORKSPACE_SKILL_DIRS = (
    ".agents/skills",
    "skills",
    ".opencode/skills",
    ".claude/skills",
    ".cursor/skills",
)
_GLOBAL_SKILL_DIRS = (
    ".agents/skills",
    ".claude/skills",
    ".deepseek/skills",
    ".atri/skills",
)
_SKIP_DISCOVERY_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}
_SKIP_COMPANION_DIRS = _SKIP_DISCOVERY_DIRS | {".mypy_cache", ".ruff_cache"}
_SKILL_MD_NAMES = {"SKILL.md", "skill.md"}

MAX_DISCOVERY_DEPTH = 8
MAX_COMPANION_FILES = 200
MAX_COMPANION_DEPTH = 4
MAX_SKILL_DESCRIPTION_CHARS = 512
MAX_SKILLS_PROMPT_CHARS = 12_000
SKILL_DISCOVERY_CACHE_TTL_SECONDS = 2.0


def _is_ignored_zip_entry(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if not parts:
        return True
    return parts[0] == "__MACOSX"


def _find_skill_markdown_path(skill_dir: Path) -> Path | None:
    """Return the existing SKILL.md/skill.md path for a skill directory."""
    try:
        entries = {entry.name for entry in skill_dir.iterdir()}
    except OSError:
        return None
    if "SKILL.md" in entries:
        return skill_dir / "SKILL.md"
    if "skill.md" in entries:
        return skill_dir / "skill.md"
    return None


def _normalize_skill_markdown_path(skill_dir: Path) -> Path | None:
    """Return the canonical ``SKILL.md`` path for a skill directory.

    If only legacy ``skill.md`` exists, it is renamed to ``SKILL.md`` in-place.
    This mutating normalization is only used for skills ATRI installs into its
    configured install directory.
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


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, bool]:
    """Return YAML frontmatter metadata, body, and whether a block existed."""
    normalized = text.lstrip("\ufeff")
    if not normalized.startswith("---"):
        return {}, text, False

    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text, False

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text, False

    frontmatter = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    try:
        payload = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return {}, body, True
    if not isinstance(payload, dict):
        return {}, body, True
    return payload, body, True


def _parse_frontmatter_description(text: str) -> str:
    """Extract the ``description`` value from YAML frontmatter."""
    payload, _body, has_frontmatter = _split_frontmatter(text)
    if not has_frontmatter:
        return ""
    description = payload.get("description", "")
    if not isinstance(description, str):
        return ""
    return description.strip()


def _first_markdown_heading(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _first_markdown_description(text: str, heading: str = "") -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "---" or line.startswith("#"):
            continue
        if heading and line == heading:
            continue
        return line
    return ""


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
    """Return a prompt-safe display name."""
    display = str(name or "").replace("`", "")
    display = display.replace("/", " ").replace("\\", " ")
    display = _CONTROL_CHARS_RE.sub(" ", display)
    display = " ".join(display.split())
    return display or "<invalid_skill_name>"


def _validate_skill_name(name: str) -> str:
    """Validate a path-segment skill name used for local installs."""
    skill_name = str(name or "").strip()
    if not skill_name or skill_name in {".", ".."} or not _SKILL_NAME_RE.fullmatch(skill_name):
        raise ValueError("Invalid skill name.")
    return skill_name


def _validate_skill_id(name: str) -> str:
    """Validate a model/UI-visible skill id before using it as a config key."""
    skill_name = str(name or "").strip()
    if not skill_name or skill_name in {".", ".."} or not _SKILL_ID_RE.fullmatch(skill_name):
        raise ValueError("Invalid skill name.")
    return skill_name


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


def _truncate_for_prompt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _resolve_base_path(path: str | Path, base: Path | None = None) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (base or Path.cwd()) / p
    return p.resolve()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False


def _candidate_env_roots() -> list[str]:
    raw = os.environ.get("ATRI_SKILL_PATHS") or os.environ.get("ATRI_SKILLS_PATH") or ""
    return [part for part in raw.split(os.pathsep) if part.strip()]


def _normalize_companion_rel_path(path: str) -> PurePosixPath:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or _DRIVE_PATH_RE.match(raw):
        raise ValueError("Invalid skill file path.")
    rel = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError("Invalid skill file path.")
    return rel


def _list_companion_files(skill_dir: Path, skill_path: Path) -> list[str]:
    """List files shipped with a skill, excluding SKILL.md itself."""
    out: list[str] = []
    try:
        skill_dir = skill_dir.resolve()
        skill_path = skill_path.resolve()
    except OSError:
        return out

    for root, dirs, files in os.walk(skill_dir, topdown=True, followlinks=False):
        root_path = Path(root)
        try:
            rel_root = root_path.relative_to(skill_dir)
        except ValueError:
            continue

        depth = 0 if str(rel_root) == "." else len(rel_root.parts)
        if depth >= MAX_COMPANION_DEPTH:
            dirs[:] = []
        else:
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".") and d not in _SKIP_COMPANION_DIRS
            )

        for filename in sorted(files):
            if filename.startswith("."):
                continue
            path = root_path / filename
            try:
                if path.resolve() == skill_path:
                    continue
            except OSError:
                continue
            if depth == 0 and filename in _SKILL_MD_NAMES:
                continue
            try:
                rel = path.relative_to(skill_dir).as_posix()
            except ValueError:
                continue
            out.append(rel)
            if len(out) >= MAX_COMPANION_FILES:
                return out
    return out


def _parse_skill_file(skill_path: Path, content: str) -> tuple[str, str, str, list[str]]:
    """Parse a skill file into name, description, format, and warnings."""
    fallback_name = skill_path.parent.name
    metadata, body, has_frontmatter = _split_frontmatter(content)
    warnings: list[str] = []

    if has_frontmatter:
        raw_name = metadata.get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            name = raw_name.strip()
        else:
            name = fallback_name
            warnings.append("Missing frontmatter name; using directory name.")

        raw_description = metadata.get("description", "")
        description = raw_description.strip() if isinstance(raw_description, str) else ""
        if not description:
            description = _first_markdown_description(body)
        skill_format = "skill"
    else:
        heading = _first_markdown_heading(content)
        name = heading or fallback_name
        description = _first_markdown_description(content, heading=heading)
        skill_format = "markdown"

    try:
        name = _validate_skill_id(name)
    except ValueError:
        fallback_name = _validate_skill_id(fallback_name)
        warnings.append(f"Invalid skill name in metadata; using {fallback_name}.")
        name = fallback_name

    return name, description.strip(), skill_format, warnings


def _normalize_skill_name(name: str) -> str:
    raw = str(name or "")
    return re.sub(r"\s+", "_", raw.strip())


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


@dataclass
class SkillInfo:
    name: str
    description: str
    path: str  # absolute path to SKILL.md or skill.md
    active: bool
    root: str = ""
    source: str = ""
    format: str = "skill"
    companion_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    can_delete: bool = True


@dataclass
class LoadedSkill:
    info: SkillInfo
    content: str
    loaded_path: str
    companion_file: str = ""


def build_skills_prompt(skills: list[SkillInfo]) -> str:
    """Build the skills section of the system prompt.

    Only names, descriptions, and source paths are shown upfront. The LLM must
    load the specific skill body before using it.
    """
    if not skills:
        return ""

    skills_lines: list[str] = []
    example_path = ""
    omitted = 0
    current_size = 0

    for skill in skills:
        display_name = _sanitize_skill_display_name(skill.name)
        description = _sanitize_prompt_description(skill.description or "No description")
        description = _truncate_for_prompt(description, MAX_SKILL_DESCRIPTION_CHARS)
        rendered_path = _sanitize_prompt_path_for_prompt(skill.path)
        if not rendered_path:
            rendered_path = "<skills_root>/<skill_name>/SKILL.md"

        source = _sanitize_prompt_description(skill.source or "")
        source_suffix = f"\n  Source: {source}" if source else ""
        line = f"- **{display_name}**: {description}\n  File: `{rendered_path}`{source_suffix}"
        if current_size + len(line) > MAX_SKILLS_PROMPT_CHARS:
            omitted += 1
            continue
        skills_lines.append(line)
        current_size += len(line)
        if not example_path:
            example_path = rendered_path

    if omitted:
        skills_lines.append(f"- ... {omitted} additional skills omitted from this prompt budget.")

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
        "1. **Discovery** -- The list above is the complete active skill "
        "inventory for this session. Full instructions are in the referenced "
        "`SKILL.md` file.\n"
        "2. **When to trigger** -- Use a skill if the user names it "
        "explicitly, or if the task clearly matches the skill's description. "
        "*Never silently skip a matching skill* -- either use it or briefly "
        "explain why you chose not to.\n"
        "3. **Mandatory grounding** -- Before executing any skill you MUST "
        "first load its instructions. Prefer the `load_skill` tool when it is "
        "available (for example `load_skill` with `name` set to the skill "
        "name). If that tool is unavailable, read the absolute path shown "
        f"above with a shell command such as `{example_command}`. Never rely "
        "on memory or assumptions about a skill's content.\n"
        "4. **Progressive disclosure** -- Load only what is directly "
        "referenced from `SKILL.md`:\n"
        "   - If `scripts/` exist, prefer running or patching them over "
        "rewriting code from scratch.\n"
        "   - If `assets/` or templates exist, reuse them.\n"
        "   - Do NOT bulk-load every file in the skill directory.\n"
        "5. **Companion files** -- `load_skill` lists sibling files. Open a "
        "companion file only when the skill instructions directly require it.\n"
        "6. **Coordination** -- When multiple skills apply, pick the minimal "
        "set needed. Announce which skill(s) you are using and why "
        "(one short line).\n"
        "7. **Context hygiene** -- Avoid deep reference chasing; open only "
        "files that are directly linked from `SKILL.md`.\n"
        "8. **Failure handling** -- If a skill cannot be applied, state the "
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
        *,
        workspace: str | None = None,
        search_roots: list[str] | tuple[str, ...] | None = None,
        include_global: bool = True,
    ) -> None:
        self._project_root = Path.cwd().resolve()
        self.skills_root = str(skills_root)
        self.skills_config: dict = skills_config if skills_config is not None else {}
        self.workspace = str(workspace) if workspace else None
        self._workspace_root = (
            _resolve_base_path(self.workspace, self._project_root) if self.workspace else None
        )
        self.search_roots = list(search_roots or [])
        self.include_global = include_global
        self._skills_cache: list[SkillInfo] | None = None
        self._skills_cache_at = 0.0
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        self._install_root().mkdir(parents=True, exist_ok=True)

    def invalidate_cache(self) -> None:
        """Clear cached filesystem discovery results."""
        self._skills_cache = None
        self._skills_cache_at = 0.0

    def _resolve_path(self, path: str | Path) -> Path:
        return _resolve_base_path(path, self._project_root)

    def _install_root(self) -> Path:
        return self._resolve_path(self.skills_root)

    def resolve_skill_dir(self, name: str) -> Path:
        """Return the primary install directory for a validated install name."""
        skill_name = _validate_skill_name(name)
        root = self._install_root()
        target = (root / skill_name).resolve()
        try:
            target.relative_to(root)
        except ValueError as e:
            raise ValueError("Invalid skill name.") from e
        return target

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------

    def skill_roots(self) -> list[tuple[Path, str, bool]]:
        """Return existing skill roots in precedence order.

        The bool marks whether ATRI should treat the root as writable for
        destructive operations such as deleting a skill.
        """
        candidates: list[tuple[Path, str, bool]] = []
        candidates.append((self._install_root(), "configured", True))

        for root in self.search_roots:
            candidates.append((self._resolve_path(root), "custom", False))
        for root in _candidate_env_roots():
            candidates.append((self._resolve_path(root), "env", False))

        bases: list[tuple[Path, str]] = []
        if self._workspace_root:
            bases.append((self._workspace_root, "workspace"))
        if not self._workspace_root or _path_key(self._workspace_root) != _path_key(
            self._project_root
        ):
            bases.append((self._project_root, "project"))

        for base, label in bases:
            for rel in _WORKSPACE_SKILL_DIRS:
                candidates.append((base / rel, f"{label}:{rel}", False))

        if self.include_global:
            home = Path.home()
            for rel in _GLOBAL_SKILL_DIRS:
                candidates.append((home / rel, f"global:{rel}", False))

        out: list[tuple[Path, str, bool]] = []
        seen: set[str] = set()
        for raw_path, source, can_delete in candidates:
            path = self._resolve_path(raw_path)
            key = _path_key(path)
            if key in seen or not path.is_dir():
                continue
            seen.add(key)
            out.append((path, source, can_delete))
        return out

    def _discover_root(self, root: Path, source: str, root_can_delete: bool) -> list[SkillInfo]:
        out: list[SkillInfo] = []
        visited: set[str] = set()
        self._discover_recursive(root, root, source, root_can_delete, 0, visited, out)
        return out

    def _discover_recursive(
        self,
        root: Path,
        directory: Path,
        source: str,
        root_can_delete: bool,
        depth: int,
        visited: set[str],
        out: list[SkillInfo],
    ) -> None:
        if depth > MAX_DISCOVERY_DEPTH:
            return
        try:
            key = _path_key(directory)
        except OSError:
            return
        if key in visited:
            return
        visited.add(key)

        skill_md = _find_skill_markdown_path(directory)
        if skill_md is not None:
            info = self._read_skill_info(root, directory, skill_md, source, root_can_delete)
            if info is not None:
                out.append(info)
            return

        if depth >= MAX_DISCOVERY_DEPTH:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return

        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in _SKIP_DISCOVERY_DIRS:
                continue
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            self._discover_recursive(
                root,
                entry,
                source,
                root_can_delete,
                depth + 1,
                visited,
                out,
            )

    def _read_skill_info(
        self,
        root: Path,
        skill_dir: Path,
        skill_md: Path,
        source: str,
        root_can_delete: bool,
    ) -> SkillInfo | None:
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            name, description, skill_format, warnings = _parse_skill_file(skill_md, content)
        except Exception:
            return None

        # ``can_delete`` intentionally resolves symlinks: a skill visible
        # through the install root but physically stored elsewhere is treated
        # as external, so deleting it cannot remove files outside skills_root.
        can_delete = root_can_delete and _path_within(skill_dir, self._install_root())
        path_str = str(skill_md.resolve()).replace("\\", "/")
        root_str = str(root.resolve()).replace("\\", "/")
        companion_files = _list_companion_files(skill_dir, skill_md)

        return SkillInfo(
            name=name,
            description=description,
            path=path_str,
            active=True,
            root=root_str,
            source=source,
            format=skill_format,
            companion_files=companion_files,
            warnings=warnings,
            can_delete=can_delete,
        )

    def _skill_active_state(self, name: str) -> bool:
        state = self.skills_config.get(name)
        if isinstance(state, dict):
            return bool(state.get("active", True))
        if isinstance(state, bool):
            return state
        return True

    def _with_current_state(self, skill: SkillInfo) -> SkillInfo:
        return replace(
            skill,
            active=self._skill_active_state(skill.name),
            companion_files=list(skill.companion_files),
            warnings=list(skill.warnings),
        )

    # ------------------------------------------------------------------
    # listing/loading
    # ------------------------------------------------------------------

    def _discover_skills_cached(self) -> list[SkillInfo]:
        now = monotonic()
        if (
            self._skills_cache is not None
            and now - self._skills_cache_at < SKILL_DISCOVERY_CACHE_TTL_SECONDS
        ):
            return self._skills_cache

        skills_by_name: dict[str, SkillInfo] = {}

        for root, source, root_can_delete in self.skill_roots():
            for skill in self._discover_root(root, source, root_can_delete):
                if skill.name in skills_by_name:
                    skills_by_name[skill.name].warnings.append(
                        f"Duplicate skill ignored from {skill.path}"
                    )
                    continue
                skills_by_name[skill.name] = skill

        self._skills_cache = [skills_by_name[n] for n in sorted(skills_by_name)]
        self._skills_cache_at = now
        return self._skills_cache

    def list_skills(self, active_only: bool = False) -> list[SkillInfo]:
        """List all discovered skills, merging filesystem state with config."""
        skills = [self._with_current_state(skill) for skill in self._discover_skills_cached()]
        if active_only:
            skills = [skill for skill in skills if skill.active]
        return skills

    def get_skill(self, name: str, *, active_only: bool = False) -> SkillInfo | None:
        skill_name = _validate_skill_id(name)
        for skill in self.list_skills(active_only=active_only):
            if skill.name == skill_name:
                return skill
        return None

    def load_skill(
        self,
        name: str,
        *,
        file: str | None = None,
        active_only: bool = False,
    ) -> LoadedSkill:
        """Load a skill's main instructions or one companion file."""
        skill = self.get_skill(name, active_only=active_only)
        if skill is None:
            raise KeyError(f"Skill not found: {name}")

        skill_dir = Path(skill.path).parent.resolve()
        target = Path(skill.path).resolve()
        companion_file = ""

        if file:
            rel = _normalize_companion_rel_path(file)
            candidate = (skill_dir / Path(*rel.parts)).resolve()
            try:
                candidate.relative_to(skill_dir)
            except ValueError as e:
                raise ValueError("Invalid skill file path.") from e
            if not candidate.is_file():
                raise FileNotFoundError(f"Skill companion file not found: {file}")
            target = candidate
            companion_file = rel.as_posix()

        content = target.read_text(encoding="utf-8", errors="replace")
        return LoadedSkill(
            info=skill,
            content=content,
            loaded_path=str(target).replace("\\", "/"),
            companion_file=companion_file,
        )

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------

    def set_skill_active(self, name: str, active: bool) -> None:
        """Toggle a skill's active flag in config."""
        skill_name = _validate_skill_id(name)
        self.skills_config.setdefault(skill_name, {})["active"] = bool(active)
        self.invalidate_cache()

    def delete_skill(self, name: str) -> None:
        """Delete a skill directory from ATRI's primary install root."""
        skill_name = _validate_skill_id(name)
        skill = self.get_skill(skill_name, active_only=False)
        if skill is None:
            raise ValueError("Skill not found.")
        if not skill.can_delete:
            raise PermissionError("Only skills in the configured skills_root can be deleted.")

        skill_dir = Path(skill.path).parent.resolve()
        install_root = self._install_root()
        try:
            skill_dir.relative_to(install_root)
        except ValueError as e:
            raise PermissionError(
                "Only skills in the configured skills_root can be deleted."
            ) from e

        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        self.skills_config.pop(skill_name, None)
        self.invalidate_cache()

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
        - root mode: ``SKILL.md`` at the zip root (uses zip filename as skill name)
        - dir mode: one or more subdirectories each containing ``SKILL.md``

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
                archive_skill_name = _validate_skill_name(_normalize_skill_name(skill_name_hint))

            # Security: validate all paths before extraction.
            for name in names:
                if not name:
                    continue
                if name.startswith("/") or _DRIVE_PATH_RE.match(name):
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
                    archive_hint = _normalize_skill_name(skill_name_hint or zip_path_obj.stem)
                    install_name = _validate_skill_name(archive_hint)

                    src_dir = Path(tmp_dir)
                    normalized_path = _normalize_skill_markdown_path(src_dir)
                    if normalized_path is None:
                        raise ValueError("SKILL.md not found in the root of the zip archive.")

                    dest_dir = self._install_root() / install_name
                    if dest_dir.exists() and overwrite:
                        shutil.rmtree(dest_dir)
                    elif dest_dir.exists() and not overwrite:
                        raise FileExistsError(f"Skill {install_name} already exists.")

                    shutil.move(str(src_dir), str(dest_dir))
                    installed_name = self._installed_skill_name(dest_dir, install_name)
                    self.set_skill_active(installed_name, True)
                    installed_skills.append(installed_name)

                else:
                    top_dirs = {PurePosixPath(n).parts[0] for n in file_names if n.strip()}

                    for archive_root_name in top_dirs:
                        archive_root_name_normalized = _normalize_skill_name(archive_root_name)

                        if (
                            f"{archive_root_name}/SKILL.md" not in file_names
                            and f"{archive_root_name}/skill.md" not in file_names
                        ):
                            continue

                        if archive_root_name in {".", "..", ""}:
                            continue
                        try:
                            _validate_skill_name(archive_root_name_normalized)
                        except ValueError:
                            continue

                        if archive_skill_name and len(top_dirs) == 1:
                            install_name = archive_skill_name
                        else:
                            install_name = archive_root_name_normalized

                        src_dir = Path(tmp_dir) / archive_root_name
                        normalized_path = _normalize_skill_markdown_path(src_dir)
                        if normalized_path is None:
                            continue

                        dest_dir = self._install_root() / install_name
                        if dest_dir.exists():
                            if not overwrite:
                                raise FileExistsError(f"Skill {install_name} already exists.")
                            shutil.rmtree(dest_dir)

                        shutil.move(str(src_dir), str(dest_dir))
                        installed_name = self._installed_skill_name(dest_dir, install_name)
                        self.set_skill_active(installed_name, True)
                        installed_skills.append(installed_name)

        if not installed_skills:
            raise ValueError("No valid SKILL.md found in any folder of the zip archive.")

        return ", ".join(installed_skills)

    def _installed_skill_name(self, skill_dir: Path, fallback: str) -> str:
        skill_md = _find_skill_markdown_path(skill_dir)
        if skill_md is None:
            return fallback
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            name, _description, _fmt, _warnings = _parse_skill_file(skill_md, content)
            return name
        except Exception:
            return fallback
