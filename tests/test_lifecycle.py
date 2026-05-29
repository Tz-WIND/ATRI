import pytest

from core.lifecycle import Lifecycle
from core.platform.daw_agent import DawAgentAdapter


class _FakePluginManager:
    def __init__(self, _plugins_dir):
        self.plugins = []

    async def initialize(self, _config):
        return None


class _FakeKnowledgeManager:
    def __init__(self, config):
        self.config = config

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
