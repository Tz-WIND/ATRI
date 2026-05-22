from core.runtime.todos import TodoStore
from core.tools import create_tools
from core.tools.todo import AgentTodoTool


def test_agent_todo_tool_sets_items_and_marks_one_complete(tmp_path):
    store = TodoStore(tmp_path / "runtime")
    snapshots = []
    tool = AgentTodoTool(
        str(tmp_path),
        todo_store=store,
        session_id="webchat:friend:test",
        on_change=snapshots.append,
    )

    try:
        output = tool.execute(action="set", items=["Read the chat flow", "Patch the UI"])

        assert "2 todo item(s)" in output
        snapshot = store.snapshot("webchat:friend:test")
        assert [item["content"] for item in snapshot["items"]] == [
            "Read the chat flow",
            "Patch the UI",
        ]
        assert [item["status"] for item in snapshot["items"]] == ["pending", "pending"]

        output = tool.execute(action="complete", index=1)

        assert "Marked todo 1 complete" in output
        snapshot = store.snapshot("webchat:friend:test")
        assert [item["status"] for item in snapshot["items"]] == ["completed", "pending"]
        assert snapshot["completed"] == 1
        assert snapshot["total"] == 2
        assert snapshot["all_completed"] is False
        assert snapshots[-1] == snapshot
    finally:
        store.close()


def test_agent_todo_tool_marks_all_complete_and_persists(tmp_path):
    store = TodoStore(tmp_path / "runtime")
    tool = AgentTodoTool(str(tmp_path), todo_store=store, session_id="session-1")

    try:
        tool.execute(action="set", items=["One", "Two"])
        output = tool.execute(action="complete_all")

        assert "Marked all todo items complete" in output
        first_snapshot = store.snapshot("session-1")
        assert first_snapshot["all_completed"] is True

        reopened = TodoStore(tmp_path / "runtime")
        try:
            assert reopened.snapshot("session-1")["items"] == first_snapshot["items"]
        finally:
            reopened.close()
    finally:
        store.close()


def test_agent_todo_tool_rejects_missing_complete_target(tmp_path):
    store = TodoStore(tmp_path / "runtime")
    tool = AgentTodoTool(str(tmp_path), todo_store=store, session_id="session-1")

    try:
        tool.execute(action="set", items=["One"])

        assert tool.execute(action="complete", index=99).startswith("Error:")
    finally:
        store.close()


def test_create_tools_registers_session_bound_todo_tool(tmp_path):
    store = TodoStore(tmp_path / "runtime")

    try:
        tools = {
            tool.name: tool
            for tool in create_tools(str(tmp_path), todo_store=store, todo_session_id="session-1")
        }

        assert "todo" in tools
        assert tools["todo"].metadata()["capability"] == "agent.todo"
        assert tools["todo"].metadata()["read_only"] is True
    finally:
        store.close()
