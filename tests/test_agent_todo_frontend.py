from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_PAGE = ROOT / "frontend" / "src" / "components" / "chat" / "ChatPage.vue"
TODO_PANEL = ROOT / "frontend" / "src" / "components" / "chat" / "AgentTodoPanel.vue"
USE_CHAT = ROOT / "frontend" / "src" / "composables" / "useChat.js"
USE_SESSION = ROOT / "frontend" / "src" / "composables" / "useSession.js"


def test_chat_page_renders_agent_todo_panel():
    assert TODO_PANEL.exists()
    panel = TODO_PANEL.read_text(encoding="utf-8")
    chat_page = CHAT_PAGE.read_text(encoding="utf-8")

    assert "agent-todo-panel" in panel
    assert "Update Todos" in panel
    assert "todoSnapshot" in panel
    assert "AgentTodoPanel" in chat_page
    assert "item.type === 'todo'" in chat_page
    assert ':todo-snapshot="item.message.todoSnapshot"' in chat_page


def test_use_chat_tracks_todo_snapshots_from_websocket_and_transcript():
    use_chat = USE_CHAT.read_text(encoding="utf-8")
    use_session = USE_SESSION.read_text(encoding="utf-8")

    assert "todoSnapshot = ref" in use_chat
    assert "msg.type === 'todo_snapshot'" in use_chat
    assert "addTodoMessage(todoSnapshot.value)" in use_chat
    assert "role: 'todo'" in use_chat
    assert "msg.data.tool === 'todo'" in use_chat
    assert "transcript?.todoSnapshot" in use_chat
    assert "todo_snapshot" in use_session
