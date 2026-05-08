from __future__ import annotations

import pytest

from core.skills import SkillManager, build_skills_prompt
from core.tools.skill import LoadSkillTool


@pytest.fixture(autouse=True)
def isolated_project_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def write_skill(root, name: str, description: str, body: str = "body") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n",
        encoding="utf-8",
    )


def test_discovers_multiple_roots_with_first_wins_and_companions(tmp_path):
    install_root = tmp_path / "install"
    workspace = tmp_path / "workspace"

    write_skill(install_root, "shared", "install wins", "# Steps\nUse install copy.")
    (install_root / "shared" / "helper.md").write_text("helper", encoding="utf-8")
    write_skill(workspace / ".agents" / "skills", "shared", "workspace loses")
    write_skill(workspace / ".claude" / "skills", "claude-only", "claude interop")

    config = {"claude-only": {"active": False}}
    manager = SkillManager(
        str(install_root),
        config,
        workspace=str(workspace),
        include_global=False,
    )

    skills = {skill.name: skill for skill in manager.list_skills()}
    assert set(skills) == {"shared", "claude-only"}
    assert skills["shared"].description == "install wins"
    assert skills["shared"].can_delete is True
    assert skills["shared"].companion_files == ["helper.md"]
    assert any("Duplicate skill ignored" in warning for warning in skills["shared"].warnings)
    assert skills["claude-only"].active is False

    active_names = [skill.name for skill in manager.list_skills(active_only=True)]
    assert active_names == ["shared"]


def test_load_skill_loads_main_body_and_companion_file(tmp_path):
    install_root = tmp_path / "skills"
    write_skill(install_root, "review-pr", "Review pull requests", "# Review\nCheck diffs.")
    (install_root / "review-pr" / "rubric.md").write_text("Focus on bugs.", encoding="utf-8")

    manager = SkillManager(str(install_root), {}, include_global=False)

    loaded = manager.load_skill("review-pr")
    assert "Check diffs." in loaded.content
    assert loaded.companion_file == ""

    companion = manager.load_skill("review-pr", file="rubric.md")
    assert companion.content == "Focus on bugs."
    assert companion.companion_file == "rubric.md"

    with pytest.raises(ValueError):
        manager.load_skill("review-pr", file="../outside.md")


def test_plain_markdown_skill_falls_back_to_heading(tmp_path):
    install_root = tmp_path / "skills"
    skill_dir = install_root / "plain"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "# Plain Skill\n\nUse this without YAML frontmatter.\n",
        encoding="utf-8",
    )

    manager = SkillManager(str(install_root), {}, include_global=False)
    skill = manager.get_skill("Plain Skill")

    assert skill is not None
    assert skill.format == "markdown"
    assert skill.description == "Use this without YAML frontmatter."


def test_build_prompt_points_to_load_skill(tmp_path):
    install_root = tmp_path / "skills"
    write_skill(install_root, "writer", "Write concise docs")
    manager = SkillManager(str(install_root), {}, include_global=False)

    prompt = build_skills_prompt(manager.list_skills(active_only=True))

    assert "load_skill" in prompt
    assert "writer" in prompt
    assert "Write concise docs" in prompt


def test_load_skill_tool_formats_body_and_companion_list(tmp_path):
    install_root = tmp_path / "skills"
    write_skill(install_root, "review-pr", "Review pull requests", "# Review\nCheck diffs.")
    (install_root / "review-pr" / "rubric.md").write_text("Focus on bugs.", encoding="utf-8")
    manager = SkillManager(str(install_root), {}, include_global=False)

    tool = LoadSkillTool(str(tmp_path), skill_manager=manager)
    output = tool.execute("review-pr")

    assert "# Skill: review-pr" in output
    assert "## Companion files" in output
    assert "`rubric.md`" in output


def test_delete_rejects_non_configured_roots(tmp_path):
    install_root = tmp_path / "install"
    workspace = tmp_path / "workspace"
    write_skill(workspace / ".claude" / "skills", "external", "from claude")

    manager = SkillManager(
        str(install_root),
        {},
        workspace=str(workspace),
        include_global=False,
    )

    with pytest.raises(PermissionError):
        manager.delete_skill("external")


def test_list_skills_does_not_mutate_config(tmp_path):
    install_root = tmp_path / "skills"
    write_skill(install_root, "writer", "Write docs")
    config = {}
    manager = SkillManager(str(install_root), config, include_global=False)

    assert [skill.name for skill in manager.list_skills()] == ["writer"]

    assert config == {}


def test_discovery_cache_is_invalidated_explicitly_and_on_mutation(tmp_path):
    install_root = tmp_path / "skills"
    write_skill(install_root, "alpha", "Alpha")
    manager = SkillManager(str(install_root), {}, include_global=False)

    assert [skill.name for skill in manager.list_skills()] == ["alpha"]
    write_skill(install_root, "beta", "Beta")

    assert [skill.name for skill in manager.list_skills()] == ["alpha"]

    manager.invalidate_cache()
    assert [skill.name for skill in manager.list_skills()] == ["alpha", "beta"]

    write_skill(install_root, "gamma", "Gamma")
    manager.set_skill_active("alpha", False)
    assert [skill.name for skill in manager.list_skills()] == ["alpha", "beta", "gamma"]


def test_project_root_is_skipped_when_workspace_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "skills").mkdir()

    manager = SkillManager("install", {}, workspace=".", include_global=False)
    sources = [source for _path, source, _can_delete in manager.skill_roots()]

    assert "workspace:skills" in sources
    assert "project:skills" not in sources
