from core.platform.message import (
    At,
    File,
    Image,
    MessageEvent,
    MessageType,
    Plain,
    Sender,
    display_session_id,
    normalize_session_id,
)


def test_message_event_tracks_result_text_and_chain():
    event = MessageEvent()

    event.set_result("hello")
    assert event.get_result_text() == "hello"
    assert event.get_result_chain() == [Plain(text="hello")]

    event.set_result_chain([Plain(text="a"), Image(url="x"), Plain(text="b")])
    assert event.get_result_text() == "ab"
    assert event.get_result_chain()[1] == Image(url="x")


def test_message_event_origin_sender_and_stop_state():
    event = MessageEvent(
        message_type=MessageType.GROUP_MESSAGE,
        sender=Sender(user_id="42", nickname="Tester"),
        session_id="group-1",
        platform_name="onebot11",
    )

    assert event.unified_msg_origin == "onebot11:group:group-1"
    assert event.is_private() is False
    assert event.get_sender_name() == "Tester"
    assert event.is_stopped() is False

    event.stop()

    assert event.is_stopped() is True


def test_message_outline_combines_plain_and_structured_components():
    event = MessageEvent(
        message_chain=[
            At(qq="123", name="atri"),
            Plain(text="hello"),
            Image(url="https://example.test/a.png"),
            File(name="report.txt"),
        ]
    )

    outline = event.get_message_outline()

    assert "[@atri]" in outline
    assert "hello" in outline
    assert "report.txt" in outline


def test_session_id_display_normalization_round_trip_for_webchat_ids():
    assert normalize_session_id("abc") == "webchat:friend:abc"
    assert normalize_session_id("onebot11:group:42") == "onebot11:group:42"
    assert display_session_id("webchat:friend:abc") == "abc"
    assert display_session_id("onebot11:group:42") == "onebot11:group:42"
