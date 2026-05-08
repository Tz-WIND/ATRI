import pytest

import core.utils.files as files_module
from core.utils import atomic_write_text, format_bytes


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0B"),
        (512, "512B"),
        (1536, "1.5KB"),
        (2 * 1024 * 1024, "2.0MB"),
        (3 * 1024 * 1024 * 1024, "3.0GB"),
        (4 * 1024 * 1024 * 1024 * 1024, "4.0TB"),
    ],
)
def test_format_bytes(size, expected):
    assert format_bytes(size) == expected


def test_atomic_write_text_replaces_target_and_removes_temp_file(tmp_path):
    target = tmp_path / "data.txt"

    atomic_write_text(target, "hello", prefix=".test_")

    assert target.read_text(encoding="utf-8") == "hello"
    assert list(tmp_path.glob(".test_*")) == []


def test_atomic_write_text_cleans_temp_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "data.txt"
    target.write_text("old", encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(files_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(target, "new", prefix=".test_")

    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".test_*")) == []
