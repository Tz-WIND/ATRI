import asyncio
import json
import subprocess
from typing import Any, cast

import pytest

from core import host
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


def test_atexit_cleanup_uses_host_manager_cleanup_method(monkeypatch):
    class _Manager:
        def __init__(self):
            self.cleaned = False

        @property
        def _process(self):
            raise AssertionError("atexit cleanup should not access private process state")

        def cleanup_orphaned_process(self):
            self.cleaned = True

    manager = _Manager()
    monkeypatch.setattr(host, "_host_manager", manager)

    host._atexit_cleanup_host()

    assert manager.cleaned is True


def test_host_manager_cleanup_orphaned_process_terminates_live_process():
    manager = HostManager()
    calls = []

    class _StubProcess:
        pid = 6262

        def poll(self):
            return None

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

        def terminate(self):
            calls.append(("terminate", None))

        def kill(self):
            calls.append(("kill", None))

    process = _StubProcess()
    manager._process = cast(Any, process)
    manager._running = True

    assert manager.has_live_process is True
    assert manager.cleanup_orphaned_process() is True

    assert calls == [("terminate", None), ("wait", 2)]
    assert manager._process is None
    assert manager._running is False


@pytest.mark.asyncio
async def test_host_stop_sends_shutdown_command():
    manager = HostManager()

    class _StubProcess:
        pid = 4242

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        stdin = _FakeStdin()

    process = _StubProcess()
    manager._process = cast(Any, process)
    manager._running = True

    await manager.stop()

    assert manager._process is None
    assert manager._running is False
    assert process.stdin.writes
    assert json.loads(process.stdin.writes[0].decode()) == {"cmd": "shutdown"}


@pytest.mark.asyncio
async def test_host_stop_uses_force_kill_after_timeout():
    manager = HostManager()
    wait_calls = {"count": 0}

    class _StubProcess:
        pid = 5150

        def poll(self):
            return None

        def wait(self, timeout=None):
            wait_calls["count"] += 1
            if wait_calls["count"] == 1:
                raise subprocess.TimeoutExpired("atri-host", timeout)
            return 0

        def terminate(self):
            pass

        def kill(self):
            pass

        stdin = _FakeStdin()

    process = _StubProcess()
    manager._process = cast(Any, process)
    manager._running = True

    await manager.stop()

    assert manager._process is None
    assert wait_calls["count"] >= 2


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
