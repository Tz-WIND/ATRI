from types import SimpleNamespace

from core.agent.prompt import build_system_prompt


def test_music_tools_add_harmony_first_generation_workflow_to_prompt():
    tools = [
        SimpleNamespace(name="studio_project_query", description="read project summary"),
        SimpleNamespace(name="midi_query", description="query midi summary"),
        SimpleNamespace(name="midi_inspect", description="inspect midi details"),
        SimpleNamespace(name="studio_piano_lane_write", description="write piano lanes"),
        SimpleNamespace(name="studio_piano_lane_diff", description="diff piano lanes"),
        SimpleNamespace(name="midi_write", description="write midi notes"),
        SimpleNamespace(name="midi_batch_edit", description="batch edit midi"),
        SimpleNamespace(name="midi_diff", description="diff midi"),
    ]

    prompt = build_system_prompt(tools, "/workspace")

    read_index = prompt.index("1. Read the current ATRI project first")
    inspect_index = prompt.index("2. Use `midi_inspect` before precise edits")
    harmony_index = prompt.index("3. Sketch the harmony lane")
    notes_index = prompt.index("4. Write notes")
    expression_index = prompt.index("5. Shape expression last")

    assert read_index < inspect_index < harmony_index < notes_index < expression_index
    assert "studio_project_query" in prompt
    assert "midi_query" in prompt
    assert "midi_inspect" in prompt
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
