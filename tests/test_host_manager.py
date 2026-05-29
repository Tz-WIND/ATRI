import asyncio
import json
from typing import Any, cast

import pytest

from core.host import HostManager


class _FakeStdin:
    def __init__(self):
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        pass


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()

    def poll(self):
        return None


@pytest.mark.asyncio
async def test_host_send_command_can_wait_without_response_timeout(monkeypatch):
    manager = HostManager()
    process = _FakeProcess()
    manager._process = cast(Any, process)
    manager._running = True
    await manager._response_queue.put({"type": "ack", "cmd": "bounce"})

    async def fail_wait_for(awaitable, **_kwargs):
        awaitable.close()
        raise AssertionError("response_timeout=None should not use asyncio.wait_for")

    monkeypatch.setattr(asyncio, "wait_for", fail_wait_for)

    response = await manager.send_command(
        "bounce",
        {"path": "export.wav"},
        response_timeout=None,
    )

    assert response == {"type": "ack", "cmd": "bounce"}
    assert json.loads(process.stdin.writes[0].decode()) == {
        "cmd": "bounce",
        "path": "export.wav",
    }
