from types import SimpleNamespace
from typing import Any, cast

import pytest
from quart import Quart

from core.platform.message import MessageEvent, MessageType, Sender
from dashboard.routes import chat


class _TimeoutWebChat:
    def __init__(self):
        self.kwargs = []
        self.cancelled = []

    def create_event(self, message, session_id, **kwargs):
        self.kwargs.append(kwargs)
        event = MessageEvent(
            message_str=message,
            message_type=MessageType.FRIEND_MESSAGE,
            sender=Sender(user_id="webui", nickname="WebUI"),
            session_id=session_id,
            self_id="atri",
            platform_name="webchat",
        )
        event._extras["_request_id"] = "web-request-1"

        async def fail():
            raise TimeoutError

        return event, fail()

    def cancel_request(self, event):
        self.cancelled.append(event)
        return True


class _Lifecycle:
    def __init__(self):
        self.config = {
            "workspace": ".",
            "agent_mode": "deepresearch",
            "agent_timeout_seconds": 12.5,
            "deep_research": {"timeout_seconds": 123},
            "mcp_servers": {},
        }
        self.webchat = _TimeoutWebChat()
        self.process_stage = SimpleNamespace(
            agent_mode="deepresearch",
            research_services=object(),
        )
        self.cancelled = []

    def cancel_operation(self, session_id=None, request_id=None):
        self.cancelled.append((session_id, request_id))
        return True


class _Dashboard:
    def __init__(self):
        self.app = Quart(__name__)
        self.lifecycle = _Lifecycle()
        self.broadcasts = []

    async def broadcast(self, payload):
        self.broadcasts.append(payload)


def test_research_timeout_uses_deepresearch_policy_only_for_that_mode():
    config = {
        "agent_timeout_seconds": 12.5,
        "deep_research": {"timeout_seconds": 900},
    }

    assert chat.research_timeout_seconds(config, "deepresearch") == 900
    assert chat.research_timeout_seconds(config, "agent") == 12.5
    assert chat.research_timeout_seconds(config, "plan") == 12.5


@pytest.mark.asyncio
async def test_chat_timeout_uses_frozen_research_mode_and_cancels_operation():
    dashboard = _Dashboard()
    chat.register(cast(Any, dashboard))

    response = await dashboard.app.test_client().post(
        "/api/chat", json={"message": "research this", "session_id": "topic-a"}
    )

    assert response.status_code == 504
    assert await response.get_json() == {"error": "Agent timed out (123s)"}
    assert dashboard.lifecycle.webchat.cancelled
    assert dashboard.lifecycle.cancelled == [("webchat:friend:topic-a", "web-request-1")]
    assert dashboard.lifecycle.webchat.kwargs == [
        {
            "images": [],
            "display_user_input": "research this",
            "file_attachments": [],
            "agent_mode": "deepresearch",
        }
    ]


@pytest.mark.asyncio
async def test_tools_route_reuses_process_stage_research_services(monkeypatch):
    dashboard = _Dashboard()
    chat.register(cast(Any, dashboard))
    captured = {}

    class _Tool:
        name = "rag_search"
        description = "rag"

        @staticmethod
        def metadata():
            return {"read_only": True}

    def fake_create_tools(workspace, **kwargs):
        captured.update(kwargs)
        return [_Tool()]

    monkeypatch.setattr("core.tools.create_tools", fake_create_tools)

    response = await dashboard.app.test_client().get("/api/tools")

    assert response.status_code == 200
    assert captured["research_services"] is dashboard.lifecycle.process_stage.research_services
    assert (await response.get_json())[0]["name"] == "rag_search"
