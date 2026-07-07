from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_python_test_job_installs_frontend_dependencies_before_pytest():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["test"]["steps"]

    npm_ci_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("run") == "npm ci" and step.get("working-directory") == "frontend"
    )
    pytest_index = next(
        index for index, step in enumerate(steps) if "uv run pytest" in str(step.get("run", ""))
    )

    assert npm_ci_index < pytest_index
