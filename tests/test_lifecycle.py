import pytest

from core.lifecycle import Lifecycle
from core.platform.daw_agent import DawAgentAdapter


class _FakePluginManager:
    def __init__(self, _plugins_dir):
        self.plugins = []

    async def initialize(self, _config):
        return None


class _FakeKnowledgeManager:
    def __init__(self, config, **kwargs):
        self.config = config
        self.kwargs = kwargs

    async def initialize(self):
        return None


class _FakeGraphManager:
    latest_kwargs: dict | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeGraphManager.latest_kwargs = kwargs

    async def initialize(self):
        return None


class _FakeScheduler:
    latest_ctx: dict | None = None

    def __init__(self, ctx):
        self.ctx = ctx
        self.stages = []
        _FakeScheduler.latest_ctx = ctx

    async def initialize(self):
        return None


@pytest.mark.asyncio
async def test_lifecycle_registers_daw_agent_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Lifecycle,
        "_load_config",
        lambda self: {
            "workspace": str(tmp_path / "workspace"),
            "plugins_dir": str(tmp_path / "plugins"),
            "model": "test-model",
            "api_key": "test-key",
            "onebot11": {"enabled": False},
            "dashboard": {"enabled": False},
            "audio_host": {"auto_start": False},
        },
    )
    monkeypatch.setattr("core.lifecycle.PluginManager", _FakePluginManager)
    monkeypatch.setattr("core.knowledge.KnowledgeBaseManager", _FakeKnowledgeManager)
    monkeypatch.setattr("core.lifecycle.PipelineScheduler", _FakeScheduler)

    lifecycle = Lifecycle(str(tmp_path / "config.yaml"))

    await lifecycle.initialize()

    assert isinstance(lifecycle.daw_agent, DawAgentAdapter)
    assert lifecycle.platforms["daw_agent"] is lifecycle.daw_agent
    assert _FakeScheduler.latest_ctx is not None
    assert _FakeScheduler.latest_ctx["platforms"]["daw_agent"] is lifecycle.daw_agent


@pytest.mark.asyncio
async def test_lifecycle_shares_task_store_between_graph_and_process_stage(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        Lifecycle,
        "_load_config",
        lambda self: {
            "workspace": str(tmp_path / "workspace"),
            "runtime_dir": str(tmp_path / "runtime"),
            "plugins_dir": str(tmp_path / "plugins"),
            "model": "test-model",
            "api_key": "test-key",
            "onebot11": {"enabled": False},
            "dashboard": {"enabled": False},
            "audio_host": {"auto_start": False},
            "knowledge": {"graph": {"enabled": False}},
        },
    )
    monkeypatch.setattr("core.lifecycle.PluginManager", _FakePluginManager)
    monkeypatch.setattr("core.knowledge.GraphKnowledgeManager", _FakeGraphManager)
    monkeypatch.setattr("core.knowledge.KnowledgeBaseManager", _FakeKnowledgeManager)
    monkeypatch.setattr("core.lifecycle.PipelineScheduler", _FakeScheduler)

    lifecycle = Lifecycle(str(tmp_path / "config.yaml"))

    await lifecycle.initialize()

    assert _FakeGraphManager.latest_kwargs is not None
    assert _FakeScheduler.latest_ctx is not None
    assert _FakeGraphManager.latest_kwargs["task_store"] is lifecycle.task_store
    assert _FakeScheduler.latest_ctx["task_store"] is lifecycle.task_store
