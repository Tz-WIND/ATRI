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
