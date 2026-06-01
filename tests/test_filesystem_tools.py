from pathlib import Path

import pytest

from core.tools.base import Tool
from core.tools.edit import EditFileTool
from core.tools.find_replace import FindReplaceTool
from core.tools.glob_tool import GlobTool
from core.tools.grep import GrepTool
from core.tools.list_dir import ListDirTool
from core.tools.read import ReadFileTool, pop_read_images_from_result
from core.tools.search import SearchTool
from core.tools.write import WriteFileTool


class _ConcreteTool(Tool):
    name = "concrete"
    description = "concrete test tool"
    parameters: dict = {}  # noqa: RUF012

    def execute(self) -> str:
        return "ok"


def test_tool_resolve_path_allows_workspace_children_and_blocks_escape(tmp_path):
    tool = _ConcreteTool(str(tmp_path))

    resolved = tool.resolve_path("nested/file.txt")

    assert resolved == (tmp_path / "nested" / "file.txt").resolve()
    with pytest.raises(PermissionError):
        tool.resolve_path("../outside.txt")


def test_read_file_tool_supports_offsets_limits_and_errors(tmp_path):
    (tmp_path / "notes.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    tool = ReadFileTool(str(tmp_path))

    assert tool.execute("notes.txt", offset=2, limit=2) == (
        "2\ttwo\n3\tthree\n... (4 lines total, showing 2-3)"
    )
    assert tool.execute("missing.txt") == "Error: missing.txt not found"
    assert tool.execute("folder") == "Error: folder is a directory, not a file"


def test_read_file_tool_image_mode_attaches_small_image(tmp_path):
    from PIL import Image

    image_path = tmp_path / "screen.png"
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image_path)
    tool = ReadFileTool(str(tmp_path))

    result = tool.execute("screen.png", mode="image")
    images = pop_read_images_from_result(result)

    assert "Loaded image from screen.png" in result
    assert "ATRI_READ_IMAGE:" in result
    assert images[0]["url"].startswith("data:image/png;base64,")
    assert images[0]["mime_type"] == "image/png"


def test_read_file_tool_image_mode_resizes_large_image_for_context(monkeypatch, tmp_path):
    from PIL import Image

    image_path = tmp_path / "large.png"
    image = Image.effect_noise((640, 640), 100).convert("RGB")
    image.save(image_path)
    assert image_path.stat().st_size > 12_000

    monkeypatch.setattr("core.tools.read._MAX_IMAGE_CONTEXT_BYTES", 12_000)
    tool = ReadFileTool(str(tmp_path))

    result = tool.execute("large.png", mode="image")
    images = pop_read_images_from_result(result)

    assert "Resized image for model context" in result
    assert images[0]["mime_type"] == "image/jpeg"
    assert images[0]["size"] <= 12_000


def test_read_file_tool_image_mode_rejects_non_images(tmp_path):
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    tool = ReadFileTool(str(tmp_path))

    result = tool.execute("notes.txt", mode="image")

    assert result.startswith("Error:")
    assert "not a supported image file" in result


def test_write_file_tool_creates_parent_directories_and_reports_diff(tmp_path):
    tool = WriteFileTool(str(tmp_path))

    result = tool.execute("nested/out.txt", "hello\nworld\n")

    assert "Wrote 2 lines to nested/out.txt" in result
    assert "+hello" in result
    assert (tmp_path / "nested" / "out.txt").read_text(encoding="utf-8") == "hello\nworld\n"


def test_edit_file_tool_requires_unique_non_empty_match(tmp_path):
    target = tmp_path / "app.txt"
    target.write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
    tool = EditFileTool(str(tmp_path))

    assert tool.execute("app.txt", "", "x") == "Error: old_string must not be empty"
    assert "appears 2 times" in tool.execute("app.txt", "alpha", "gamma")
    assert "not found" in tool.execute("app.txt", "missing", "gamma")

    result = tool.execute("app.txt", "beta", "delta")

    assert "Edited app.txt" in result
    assert "-beta" in result
    assert "+delta" in result
    assert target.read_text(encoding="utf-8") == "alpha\ndelta\nalpha\n"


def test_list_dir_tool_filters_hidden_and_skipped_directories(tmp_path):
    (tmp_path / "visible").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    tool = ListDirTool(str(tmp_path))

    hidden_filtered = tool.execute(".")
    hidden_visible = tool.execute(".", show_hidden=True)

    assert "visible/" in hidden_filtered
    assert "file.txt" in hidden_filtered
    assert ".hidden/" not in hidden_filtered
    assert "__pycache__" not in hidden_filtered
    assert ".hidden/" in hidden_visible


def test_grep_tool_handles_invalid_regex_and_searches_files(tmp_path):
    (tmp_path / "a.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("return text\n", encoding="utf-8")
    tool = GrepTool(str(tmp_path))

    assert tool.execute("[").startswith("Invalid regex:")
    assert tool.execute("return", include="*.py") == "a.py:2:     return 1"
    assert tool.execute("missing") == "No matches found."


def test_glob_tool_lists_matches_relative_to_workspace(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "b.txt").write_text("", encoding="utf-8")
    tool = GlobTool(str(tmp_path))

    result = tool.execute("**/*.py")

    assert result == str(Path("pkg") / "a.py")
    assert tool.execute("*.py", path="missing") == "Error: missing is not a directory"


def test_find_replace_tool_dry_run_and_write_modes(tmp_path):
    target = tmp_path / "pkg" / "a.py"
    target.parent.mkdir()
    target.write_text("alpha beta alpha\n", encoding="utf-8")
    tool = FindReplaceTool(str(tmp_path))

    dry_run = tool.execute("alpha", "omega", path="pkg", include="*.py", dry_run=True)

    assert dry_run.startswith("[DRY RUN] 2 total replacement(s)")
    assert target.read_text(encoding="utf-8") == "alpha beta alpha\n"

    result = tool.execute("alpha", "omega", path="pkg", include="*.py")

    assert result.startswith("2 total replacement(s)")
    assert target.read_text(encoding="utf-8") == "omega beta omega\n"
    assert "Invalid regex:" in tool.execute("[", "x", is_regex=True)


def test_search_tool_finds_file_names_and_content(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "runtime_notes.txt").write_text(
        "timeline database\n",
        encoding="utf-8",
    )
    (tmp_path / "code.py").write_text("def timeline_store():\n    pass\n", encoding="utf-8")
    tool = SearchTool(str(tmp_path))

    name_result = tool.execute("runtime notes", file_only=True)
    content_result = tool.execute("timeline store")

    assert "docs" in name_result
    assert "runtime_notes.txt" in name_result
    assert "code.py:1" in content_result
    assert tool.execute("   ") == "Error: empty query"


def test_search_tool_walk_streams_files_and_prunes_skipped_directories(tmp_path):
    (tmp_path / "visible").mkdir()
    (tmp_path / "visible" / "keep.txt").write_text("keep", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.txt").write_text("ignored", encoding="utf-8")

    walked = SearchTool._walk(tmp_path)

    assert iter(walked) is walked
    assert [path.relative_to(tmp_path).as_posix() for path in walked] == ["visible/keep.txt"]
