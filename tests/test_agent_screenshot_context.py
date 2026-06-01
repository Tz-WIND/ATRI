from types import SimpleNamespace

from core.agent.agent import Agent
from core.tools import screenshot


def test_agent_appends_captured_screenshot_as_next_model_input():
    batch_id = screenshot._store_screenshot_images(
        [
            {
                "url": "data:image/png;base64,aGVsbG8=",
                "file": "base64://aGVsbG8=",
                "mime_type": "image/png",
                "size": 5,
                "name": "screen.png",
            }
        ]
    )
    result = "\n".join(
        [
            "Captured screenshot to screenshots/screen.png",
            f"ATRI_SCREENSHOT_IMAGE: {batch_id}",
            "MIME type: image/png",
        ]
    )
    agent = Agent.__new__(Agent)
    agent.messages = []

    agent._append_tool_results(
        [SimpleNamespace(id="call_1", name="screenshot")],
        [result],
    )

    assert agent.messages[0] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": result,
    }
    assert agent.messages[1]["role"] == "user"
    assert agent.messages[1]["content"] == [
        {
            "type": "text",
            "text": (
                "[Screenshot captured by the `screenshot` tool]\n"
                "The next image is the current machine screen. Use it as visual context."
            ),
        },
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
    ]
