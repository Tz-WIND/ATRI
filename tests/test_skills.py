import zipfile

import pytest

import core.skills.skill_manager as skill_manager_module
from core.skills.skill_manager import SkillManager, build_skills_prompt
from core.tools.skill import LoadSkillTool


def _write_skill(root, dirname="demo", *, name="demo", description="Demo skill"):
    skill_dir = root / dirname
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Body\n\nUse this skill.\n",
        encoding="utf-8",
    )
    (skill_dir / "notes.txt").write_text("companion notes", encoding="utf-8")
    (skill_dir / ".hidden").write_text("hidden", encoding="utf-8")
    return skill_dir


def test_skill_manager_discovers_loads_and_filters_active_skills(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    _write_skill(skills_root)
    config = {"demo": {"active": False}}
    manager = SkillManager(
        skills_root=str(skills_root),
        skills_config=config,
        include_global=False,
    )

    skills = manager.list_skills()

    assert len(skills) == 1
    assert skills[0].name == "demo"
    assert skills[0].description == "Demo skill"
    assert skills[0].active is False
    assert skills[0].companion_files == ["notes.txt"]
    assert manager.list_skills(active_only=True) == []

    manager.set_skill_active("demo", True)
    loaded = manager.load_skill("demo", active_only=True)
    companion = manager.load_skill("demo", file="notes.txt", active_only=True)

    assert config["demo"]["active"] is True
    assert "Use this skill." in loaded.content
    assert companion.companion_file == "notes.txt"
    assert companion.content == "companion notes"


def test_skill_manager_blocks_invalid_names_and_companion_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    _write_skill(skills_root)
    manager = SkillManager(skills_root=str(skills_root), include_global=False)

    with pytest.raises(ValueError, match="Invalid skill name"):
        manager.get_skill("../demo")
    with pytest.raises(ValueError, match="Invalid skill file path"):
        manager.load_skill("demo", file="../secret.txt")
    with pytest.raises(FileNotFoundError, match="Skill companion file not found"):
        manager.load_skill("demo", file="missing.txt")


def test_build_skills_prompt_sanitizes_names_descriptions_and_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: |\n  Use `carefully`\n  now\n---\n\n# Body\n",
        encoding="utf-8",
    )
    manager = SkillManager(skills_root=str(skills_root), include_global=False)

    prompt = build_skills_prompt(manager.list_skills())

    assert "## Skills" in prompt
    assert "**demo**" in prompt
    assert "Use carefully now" in prompt
    assert "`" in prompt
    assert "load_skill" in prompt


def test_load_skill_tool_formats_skill_body_and_companion_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    _write_skill(skills_root)
    manager = SkillManager(skills_root=str(skills_root), include_global=False)
    tool = LoadSkillTool(str(tmp_path), skill_manager=manager)

    body = tool.execute("demo")
    companion = tool.execute("demo", file="notes.txt")

    assert body.startswith("# Skill: demo")
    assert "## SKILL.md" in body
    assert "- `notes.txt`" in body
    assert companion.startswith("# Skill Companion File")
    assert "companion notes" in companion
    assert tool.execute("missing") == "Error: skill 'missing' not found. Available skills: demo"


def test_skill_manager_installs_root_zip_and_rejects_unsafe_zip_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    manager = SkillManager(skills_root=str(tmp_path / "skills"), include_global=False)

    zip_path = tmp_path / "root-skill.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: root_demo\ndescription: Root demo\n---\nBody")
        zf.writestr("asset.txt", "asset")

    assert manager.install_skill_from_zip(str(zip_path)) == "root_demo"
    assert manager.get_skill("root_demo") is not None

    unsafe_zip = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(unsafe_zip, "w") as zf:
        zf.writestr("../SKILL.md", "bad")

    with pytest.raises(ValueError, match="invalid relative paths"):
        manager.install_skill_from_zip(str(unsafe_zip))


def test_skill_manager_rejects_oversized_zip_member_before_extraction(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(skill_manager_module, "MAX_SKILL_ZIP_MEMBER_BYTES", 16)
    monkeypatch.setattr(skill_manager_module, "MAX_SKILL_ZIP_TOTAL_BYTES", 1024)

    def fail_copy(*args, **kwargs):
        raise AssertionError("zip extraction should not start")

    monkeypatch.setattr(skill_manager_module, "_copy_zip_member", fail_copy)
    manager = SkillManager(skills_root=str(tmp_path / "skills"), include_global=False)

    zip_path = tmp_path / "bomb.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", "A" * 128)

    with pytest.raises(ValueError, match="member exceeds maximum file size"):
        manager.install_skill_from_zip(str(zip_path))

    assert manager.list_skills() == []


def test_skill_manager_rejects_zip_with_too_much_total_uncompressed_data(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(skill_manager_module, "MAX_SKILL_ZIP_MEMBER_BYTES", 1024)
    monkeypatch.setattr(skill_manager_module, "MAX_SKILL_ZIP_TOTAL_BYTES", 80)

    def fail_copy(*args, **kwargs):
        raise AssertionError("zip extraction should not start")

    monkeypatch.setattr(skill_manager_module, "_copy_zip_member", fail_copy)
    manager = SkillManager(skills_root=str(tmp_path / "skills"), include_global=False)

    zip_path = tmp_path / "large-total.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", "name: demo\n")
        zf.writestr("asset.bin", "B" * 128)

    with pytest.raises(ValueError, match="exceeds maximum uncompressed size"):
        manager.install_skill_from_zip(str(zip_path))

    assert manager.list_skills() == []
