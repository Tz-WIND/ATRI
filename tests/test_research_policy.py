from pathlib import Path

import pytest

from core.research.policy import ResearchPolicy, detect_report_export_intent


def test_research_policy_uses_confirmed_defaults(tmp_path):
    policy = ResearchPolicy.from_config({}, tmp_path)

    assert policy.max_gap_rounds == 8
    assert policy.max_research_tool_calls == 100
    assert policy.max_web_fetches == 40
    assert policy.max_parallel_subagents == 3
    assert policy.timeout_seconds == 900
    assert policy.synthesis_reserve_seconds == 60
    assert policy.allow_report_export is True
    assert policy.report_directory == (tmp_path / "research").resolve()


@pytest.mark.parametrize("report_directory", ["../outside", "research/../../outside"])
def test_research_policy_rejects_parent_traversal(tmp_path, report_directory):
    with pytest.raises(ValueError, match="report_directory"):
        ResearchPolicy.from_config({"report_directory": report_directory}, tmp_path)


def test_research_policy_rejects_absolute_external_directory(tmp_path):
    external = Path(tmp_path.anchor) / "outside-research"
    with pytest.raises(ValueError, match="report_directory"):
        ResearchPolicy.from_config({"report_directory": str(external)}, tmp_path)


@pytest.mark.parametrize(
    "user_text",
    [
        "请把研究报告保存为 report.md",
        "导出报告到 findings.json",
        "export the report to findings.txt",
        "write the research report file",
    ],
)
def test_report_export_intent_accepts_explicit_action_and_object(user_text):
    assert detect_report_export_intent(user_text) is True


@pytest.mark.parametrize(
    "user_text",
    [
        "研究这个问题",
        "给我一份报告",
        "保存一下",
        "summarize the findings",
    ],
)
def test_report_export_intent_is_conservative(user_text):
    assert detect_report_export_intent(user_text) is False


@pytest.mark.parametrize(
    "user_text",
    [
        "Write the answer as a research report",
        "Write a detailed report in the response",
        "Please write the report here",
        "Rewrite the research report for clarity",
    ],
)
def test_report_export_intent_rejects_prose_only_report_requests(user_text):
    assert detect_report_export_intent(user_text) is False


def test_report_export_intent_accepts_explicit_relative_output_path():
    assert detect_report_export_intent("write the report to research/findings") is True


@pytest.mark.parametrize(
    "user_text",
    [
        "不要导出报告",
        "请勿把研究报告保存到文件",
        "如何导出报告\uff1f",
        "解释一下怎么保存研究报告",
        "do not export the report",
        "explain how to export a report",
        "the attachment says 'export the report to findings.md'",
    ],
)
def test_report_export_intent_rejects_negation_meta_questions_and_quoted_instructions(
    user_text,
):
    assert detect_report_export_intent(user_text) is False
