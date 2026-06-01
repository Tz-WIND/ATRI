import asyncio

import pytest

from core.lifecycle import Lifecycle


class _RecordingHost:
    def __init__(self):
        self.stopped = False

    @property
    def has_live_process(self) -> bool:
        return True

    async def stop(self) -> None:
        self.stopped = True


class _RecordingDashboard:
    def __init__(self):
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class _RecordingEventBus:
    def __init__(self):
        self.shutdown_called = asyncio.Event()
        self.shutdown_grace: float | None = None

    async def shutdown(self, grace_period: float = 5.0) -> None:
        self.shutdown_grace = grace_period
        self.shutdown_called.set()


class _RecordingProcessStage:
    def __init__(self):
        self.prepare_called = False
        self.shutdown_called = False

    def prepare_shutdown(self) -> None:
        self.prepare_called = True

    async def shutdown(self) -> None:
        self.shutdown_called = True


class _RecordingPluginManager:
    async def terminate(self) -> None:
        return None


class _RecordingGraphManager:
    async def close(self) -> None:
        return None


class _RecordingKnowledgeManager:
    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_lifecycle_stop_follows_graceful_shutdown_order(monkeypatch):
    lifecycle = Lifecycle.__new__(Lifecycle)
    lifecycle._stopped = False
    lifecycle._shutdown_event = asyncio.Event()
    lifecycle._tasks = []
    lifecycle.onebot11 = None
    lifecycle.webchat = None
    lifecycle.daw_agent = None

    host = _RecordingHost()
    dashboard = _RecordingDashboard()
    event_bus = _RecordingEventBus()
    process_stage = _RecordingProcessStage()

    lifecycle.dashboard = dashboard  # type: ignore[assignment]
    lifecycle.event_bus = event_bus  # type: ignore[assignment]
    lifecycle.process_stage = process_stage
    lifecycle.plugin_manager = _RecordingPluginManager()  # type: ignore[assignment]
    lifecycle.graph_manager = _RecordingGraphManager()
    lifecycle.knowledge_manager = _RecordingKnowledgeManager()

    order: list[str] = []

    async def record_dashboard_stop() -> None:
        order.append("dashboard")

    async def record_event_bus_shutdown(grace_period: float = 5.0) -> None:
        order.append("event_bus")
        event_bus.shutdown_grace = grace_period

    async def record_process_shutdown() -> None:
        order.append("process_stage")
        process_stage.shutdown_called = True

    async def record_host_stop() -> None:
        order.append("host")
        host.stopped = True

    monkeypatch.setattr("core.host.get_host_manager", lambda: host)
    monkeypatch.setattr(
        "core.tools.mcp.get_mcp_registry",
        lambda: type("MCP", (), {"close": lambda self: order.append("mcp")})(),
    )
    lifecycle.dashboard.stop = record_dashboard_stop  # type: ignore[method-assign]
    lifecycle.event_bus.shutdown = record_event_bus_shutdown  # type: ignore[method-assign]
    lifecycle.process_stage.shutdown = record_process_shutdown  # type: ignore[method-assign]
    host.stop = record_host_stop  # type: ignore[method-assign]

    await lifecycle.stop()

    assert process_stage.prepare_called is True
    assert process_stage.shutdown_called is True
    assert host.stopped is True
    assert order.index("dashboard") < order.index("event_bus")
    assert order.index("event_bus") < order.index("host")
    assert order.index("host") < order.index("process_stage")
    assert lifecycle._stopped is True


@pytest.mark.asyncio
async def test_lifecycle_stop_is_idempotent():
    lifecycle = Lifecycle.__new__(Lifecycle)
    lifecycle._stopped = False
    lifecycle._shutdown_event = asyncio.Event()
    lifecycle._tasks = []
    lifecycle.onebot11 = None
    lifecycle.webchat = None
    lifecycle.daw_agent = None
    lifecycle.plugin_manager = _RecordingPluginManager()  # type: ignore[assignment]
    lifecycle.graph_manager = None
    lifecycle.knowledge_manager = None
    lifecycle.process_stage = None
    lifecycle.event_bus = _RecordingEventBus()  # type: ignore[assignment]

    calls = {"count": 0}
    original_shutdown = lifecycle.event_bus.shutdown

    async def counting_shutdown(grace_period: float = 5.0) -> None:
        calls["count"] += 1
        await original_shutdown(grace_period)

    lifecycle.event_bus.shutdown = counting_shutdown  # type: ignore[method-assign]

    await lifecycle.stop()
    await lifecycle.stop()

    assert calls["count"] == 1
