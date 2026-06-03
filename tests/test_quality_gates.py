from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_makefile_ci_runs_rust_clippy_gate():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "rust-clippy:" in makefile
    assert "cargo clippy --workspace --all-targets -- -D warnings" in makefile
    assert "ci: " in makefile
    assert "rust-clippy" in makefile.split("ci:", 1)[1].split("##", 1)[0]


def test_detect_secrets_baseline_exists_for_pre_commit_hook():
    config = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert '--baseline", ".secrets.baseline"' in config
    assert (ROOT / ".secrets.baseline").is_file()
