from pathlib import Path

COMPONENT = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "components"
    / "chat"
    / "ChatMessage.vue"
)


def test_chat_message_allows_generated_svg_image_data_urls():
    text = COMPONENT.read_text(encoding="utf-8")

    assert "svg\\+xml" in text
    assert "data:image/svg+xml" in text


def test_chat_message_exposes_hover_copy_for_completed_assistant_messages():
    text = COMPONENT.read_text(encoding="utf-8")

    assert "assistant-copy-button" in text
    assert "getAssistantMessageCopyText" in text
    assert "navigator.clipboard.writeText(assistantCopyText.value)" in text
    assert ".message:hover .msg-head" in text
    assert ".message:focus-within .msg-head" in text
