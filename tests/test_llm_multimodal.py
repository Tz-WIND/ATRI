from core.agent.llm import LLM, _messages_to_anthropic


def test_messages_to_anthropic_converts_openai_image_url_data_url():
    system, messages = _messages_to_anthropic(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                    },
                ],
            }
        ]
    )

    assert system == ""
    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "aGVsbG8=",
                    },
                },
            ],
        }
    ]


def test_openai_non_stream_chat_parses_response(monkeypatch):
    class Usage:
        prompt_tokens = 3
        completion_tokens = 5

    class Function:
        name = "read_file"
        arguments = '{"path":"README.md"}'

    class ToolCall:
        id = "call_1"
        function = Function()

    class Message:
        def __init__(self):
            self.content = "done"
            self.tool_calls = [ToolCall()]

    class Choice:
        def __init__(self):
            self.message = Message()

    class Completion:
        def __init__(self):
            self.choices = [Choice()]
            self.usage = Usage()

    llm = LLM(model="vision-model", api_key="sk-test", base_url="https://example.test/v1")
    captured = {}

    def fake_call(params):
        captured["params"] = params
        return Completion()

    monkeypatch.setattr(llm, "_call_with_retry", fake_call)

    try:
        response = llm.chat(messages=[{"role": "user", "content": "hello"}], stream=False)
    finally:
        llm.close()

    assert captured["params"]["stream"] is False
    assert response.content == "done"
    assert response.prompt_tokens == 3
    assert response.completion_tokens == 5
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "README.md"}
