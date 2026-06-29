import asyncio
import textwrap
import time
import uuid

import pytest

from core.plugin.manager import PluginManager


def _module_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _write_plugin(plugins_dir, module_name: str, source: str) -> None:
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / f"{module_name}.py").write_text(
        textwrap.dedent(source),
        encoding="utf-8",
    )


async def _count_event_loop_ticks(duration: float, interval: float = 0.01) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    ticks = 0
    while loop.time() < deadline:
        await asyncio.sleep(interval)
        ticks += 1
    return ticks


@pytest.mark.asyncio
async def test_initialize_skips_plugin_that_exceeds_startup_timeout(tmp_path):
    plugins_dir = tmp_path / "plugins"
    slow_module = _module_name("a_slow_plugin")
    fast_module = _module_name("b_fast_plugin")
    _write_plugin(
        plugins_dir,
        slow_module,
        """
        import asyncio

        from core.plugin.base import Plugin, PluginMetadata


        class SlowPlugin(Plugin):
            metadata = PluginMetadata(name="slow")

            async def on_load(self, ctx):
                await asyncio.sleep(0.3)
        """,
    )
    _write_plugin(
        plugins_dir,
        fast_module,
        """
        from core.plugin.base import Plugin, PluginMetadata


        class FastPlugin(Plugin):
            metadata = PluginMetadata(name="fast")

            async def on_load(self, ctx):
                self.ctx = ctx
        """,
    )
    manager = PluginManager(str(plugins_dir))
    manager.startup_timeout = 0.08

    started = time.perf_counter()
    await manager.initialize({"marker": "ctx"})

    assert time.perf_counter() - started < 0.25
    assert [plugin.metadata.name for plugin in manager.plugins] == ["fast"]
    assert manager.plugins[0].ctx == {"marker": "ctx"}


@pytest.mark.asyncio
async def test_blocking_plugin_on_load_does_not_starve_event_loop(tmp_path):
    plugins_dir = tmp_path / "plugins"
    blocking_module = _module_name("blocking_plugin")
    _write_plugin(
        plugins_dir,
        blocking_module,
        """
        import time

        from core.plugin.base import Plugin, PluginMetadata


        class BlockingPlugin(Plugin):
            metadata = PluginMetadata(name="blocking")

            async def on_load(self, ctx):
                time.sleep(0.15)
        """,
    )
    manager = PluginManager(str(plugins_dir))
    manager.startup_timeout = 0.05

    ticker = asyncio.create_task(_count_event_loop_ticks(0.12))
    initializer = asyncio.create_task(manager.initialize({}))
    await asyncio.gather(initializer, ticker)

    assert ticker.result() >= 3
    assert manager.plugins == []


@pytest.mark.asyncio
async def test_blocking_plugin_import_does_not_starve_event_loop(tmp_path):
    plugins_dir = tmp_path / "plugins"
    blocking_module = _module_name("blocking_import_plugin")
    _write_plugin(
        plugins_dir,
        blocking_module,
        """
        import time

        time.sleep(0.15)

        from core.plugin.base import Plugin, PluginMetadata


        class BlockingImportPlugin(Plugin):
            metadata = PluginMetadata(name="blocking-import")

            async def on_load(self, ctx):
                return None
        """,
    )
    manager = PluginManager(str(plugins_dir))
    manager.startup_timeout = 0.05

    ticker = asyncio.create_task(_count_event_loop_ticks(0.12))
    initializer = asyncio.create_task(manager.initialize({}))
    await asyncio.gather(initializer, ticker)

    assert ticker.result() >= 3
    assert manager.plugins == []
