from types import SimpleNamespace

from core.agent.agent import Agent
from core.tools import read


def test_agent_appends_read_image_as_next_model_input():
    batch_id = read._store_read_images(
        [
            {
                "url": "data:image/png;base64,aGVsbG8=",
                "file": "base64://aGVsbG8=",
                "mime_type": "image/png",
                "size": 5,
                "name": "large.png",
            }
        ]
    )
    result = "\n".join(
        [
            "Loaded image from screenshots/large.png",
            f"ATRI_READ_IMAGE: {batch_id}",
            "MIME type: image/png",
        ]
    )
    agent = Agent.__new__(Agent)
    agent.messages = []

    agent._append_tool_results(
        [SimpleNamespace(id="call_1", name="read_file")],
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
                "[Image loaded by a tool]\n"
                "The next image is visual context from the workspace."
            ),
        },
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}},
    ]
