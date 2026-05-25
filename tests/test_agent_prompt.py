from types import SimpleNamespace

from core.agent.prompt import build_system_prompt


def test_music_tools_add_harmony_first_generation_workflow_to_prompt():
    tools = [
        SimpleNamespace(name="studio_piano_lane_write", description="write piano lanes"),
        SimpleNamespace(name="studio_piano_lane_diff", description="diff piano lanes"),
        SimpleNamespace(name="midi_write", description="write midi notes"),
        SimpleNamespace(name="midi_batch_edit", description="batch edit midi"),
        SimpleNamespace(name="midi_diff", description="diff midi"),
    ]

    prompt = build_system_prompt(tools, "/workspace")

    harmony_index = prompt.index("1. Sketch the harmony lane first")
    notes_index = prompt.index("2. Write notes second")
    expression_index = prompt.index("3. Shape expression last")

    assert harmony_index < notes_index < expression_index
    assert "studio_piano_lane_write" in prompt
    assert "studio_piano_lane_diff" in prompt
    assert "midi_write" in prompt
    assert "midi_batch_edit" in prompt
    assert "midi_diff" in prompt


def test_music_workflow_prompt_is_omitted_when_any_workflow_tool_is_missing():
    tools = [
        SimpleNamespace(name="studio_piano_lane_write", description="write piano lanes"),
        SimpleNamespace(name="midi_write", description="write midi notes"),
        SimpleNamespace(name="midi_batch_edit", description="batch edit midi"),
        SimpleNamespace(name="midi_diff", description="diff midi"),
    ]

    prompt = build_system_prompt(tools, "/workspace")

    assert "# Music Studio Generation Workflow" not in prompt
