"""ATRI Audio Host Manager.

Manages the Rust `atri-host` child process lifecycle, sends commands via stdin,
reads JSON responses and raw PCM audio from stdout, and forwards audio to the
frontend via WebSocket broadcast.
"""

import asyncio
import atexit
import json
import logging
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("atri.host")


class HostManager:
    """Manages the Rust audio engine child process."""

    def __init__(
        self,
        binary_path: str | None = None,
        sample_rate: int = 48000,
        buffer_size: int = 256,
        audio_engine: str = "default",
        bit_depth: str = "f32",
    ):
        self.binary_path = binary_path
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self.audio_engine = audio_engine
        self.bit_depth = bit_depth
        self._process: subprocess.Popen | None = None
        self._audio_callback: Callable[[bytes, int, int, int], None] | None = None
        self._running = False
        self._response_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._command_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def _resolve_binary(binary_path: str | None) -> str:
        if binary_path:
            return binary_path
        # Default: look in atri-host/target/release/ or target/debug/
        project_root = Path(__file__).parent.parent
        candidates = [
            project_root / "atri-host" / "target" / "release" / "atri-host.exe",
            project_root / "atri-host" / "target" / "release" / "atri-host",
            project_root / "atri-host" / "target" / "debug" / "atri-host.exe",
            project_root / "atri-host" / "target" / "debug" / "atri-host",
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        raise FileNotFoundError("atri-host binary not found. Build with: cargo build -p atri-host")

    def configure(
        self,
        *,
        binary_path: str | None = None,
        sample_rate: int | None = None,
        buffer_size: int | None = None,
        audio_engine: str | None = None,
        bit_depth: str | None = None,
    ) -> None:
        """Update host configuration while the process is stopped."""
        if self.is_running:
            return
        if binary_path is not None:
            self.binary_path = binary_path or None
        if sample_rate is not None:
            self.sample_rate = sample_rate
        if buffer_size is not None:
            self.buffer_size = buffer_size
        if audio_engine is not None:
            self.audio_engine = audio_engine or "default"
        if bit_depth is not None:
            self.bit_depth = bit_depth or "f32"

    def set_audio_callback(self, callback: Callable[[bytes, int, int, int], None]):
        """Set a callback that receives PCM audio chunks.
        Args:
            callback: fn(pcm_bytes, nframes, channels, sample_rate) -> None
        """
        self._audio_callback = callback

    @property
    def has_live_process(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def cleanup_orphaned_process(self) -> bool:
        """Synchronously terminate a live child process during interpreter shutdown."""
        process = self._process
        if process is None or process.poll() is not None:
            self._process = None
            self._running = False
            return False
        pid = process.pid
        logger.info("Cleaning up orphaned atri-host on interpreter exit (pid=%d)", pid)
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                logger.warning("Failed to clean up orphaned atri-host (pid=%d)", pid)
                return False
        finally:
            self._process = None
            self._running = False
        return True

    async def start(self) -> None:
        """Spawn the Rust host process and start reading its output."""
        binary_path = self._resolve_binary(self.binary_path)
        project_root = Path(__file__).parent.parent
        env = os.environ.copy()
        config_path = project_root / "config.yaml"
        if config_path.exists():
            env.setdefault("ATRI_CONFIG", str(config_path))

        async with self._lifecycle_lock:
            if self.has_live_process:
                logger.warning(
                    "Host process already running (pid=%d)",
                    self._process.pid if self._process else -1,
                )
                return
            if self._process is not None:
                await self._cleanup_reader_tasks()
                self._process = None
                self._running = False

            logger.info("Starting atri-host: %s", binary_path)
            try:
                self._process = subprocess.Popen(  # noqa: ASYNC220,S603
                    [binary_path],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=str(project_root),
                    env=env,
                )
            except FileNotFoundError:
                logger.error("Host binary not found at %s", binary_path)
                raise
            except OSError as e:
                logger.error("Failed to start host process: %s", e)
                raise

            self._running = True
            self._tasks = {
                asyncio.create_task(self._read_stdout()),
                asyncio.create_task(self._read_stderr()),
            }
            for task in self._tasks:
                task.add_done_callback(self._tasks.discard)
            logger.info("atri-host started (pid=%d)", self._process.pid)

    async def stop(self) -> None:
        """Stop the Rust host process gracefully, with terminate/kill fallback."""
        async with self._lifecycle_lock:
            process = self._process
            if process is None or process.poll() is not None:
                self._process = None
                self._running = False
                await self._cleanup_reader_tasks()
                return

            pid = process.pid
            logger.info("Stopping atri-host (pid=%d)...", pid)
            try:
                async with self._command_lock:
                    self._write_command_line("shutdown")
                    try:
                        await asyncio.wait_for(self._response_queue.get(), timeout=2.0)
                    except TimeoutError:
                        logger.debug("Timed out waiting for atri-host shutdown ack (pid=%d)", pid)
            except Exception as e:
                logger.debug("Host shutdown command failed (pid=%d): %s", pid, e)

            self._running = False
            try:
                if process.stdin:
                    process.stdin.close()
            except Exception as e:
                logger.debug("Host stdin close failed (pid=%d): %s", pid, e)

            await self._wait_for_process_exit(process, pid=pid)
            self._process = None
            await self._cleanup_reader_tasks()
            logger.info("atri-host stopped (pid=%d)", pid)

    def _write_command_line(self, cmd: str, params: dict | None = None) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Host process not running")

        message: dict[str, Any] = {"cmd": cmd}
        if params:
            message.update(params)
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._process.stdin.write(line.encode())
        self._process.stdin.flush()

    async def _wait_for_process_exit(self, process: subprocess.Popen, *, pid: int) -> None:
        try:
            await asyncio.to_thread(process.wait, timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("atri-host (pid=%d) did not exit after shutdown, terminating...", pid)
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning("atri-host (pid=%d) did not terminate, killing...", pid)
                process.kill()
                await asyncio.to_thread(process.wait)

        if process.poll() is None:
            logger.warning("atri-host (pid=%d) still alive after shutdown, forcing kill...", pid)
            process.kill()
            await asyncio.to_thread(process.wait)

    async def _cleanup_reader_tasks(self) -> None:
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def send_command(
        self,
        cmd: str,
        params: dict | None = None,
        *,
        response_timeout: float | None = 5.0,
    ) -> dict:
        """Send a JSON command to the host and wait for the response."""
        async with self._command_lock:
            if self._process is None or self._process.stdin is None or not self.is_running:
                raise RuntimeError("Host process not running")

            self._write_command_line(cmd, params)

            # Read response (non-audio JSON line)
            response = await self._read_response(response_timeout=response_timeout)
            self._sync_audio_config_from_response(response)
            return response

    def _sync_audio_config_from_response(self, response: dict) -> None:
        """Keep the Python-side snapshot aligned with host-reported audio config."""
        if response.get("type") not in {"status", "audio_config"}:
            return
        sample_rate = response.get("sample_rate")
        buffer_size = response.get("buffer_size")
        if isinstance(sample_rate, int) and sample_rate > 0:
            self.sample_rate = sample_rate
        if isinstance(buffer_size, int) and buffer_size > 0:
            self.buffer_size = buffer_size
        audio_engine = response.get("audio_engine")
        bit_depth = response.get("bit_depth")
        if isinstance(audio_engine, str) and audio_engine:
            self.audio_engine = audio_engine
        if isinstance(bit_depth, str) and bit_depth:
            self.bit_depth = bit_depth

    async def _read_response(self, *, response_timeout: float | None = 5.0) -> dict:
        """Read a single JSON response line from the response queue."""
        if response_timeout is None:
            return await self._response_queue.get()
        try:
            return await asyncio.wait_for(self._response_queue.get(), timeout=response_timeout)
        except TimeoutError:
            return {"type": "error", "message": "timeout waiting for host response"}

    async def _read_stdout(self) -> None:
        """Background task: read stdout lines, dispatch JSON responses and PCM audio."""
        if self._process is None or self._process.stdout is None:
            return

        reader = self._process.stdout
        while self._running:
            try:
                line = await asyncio.get_event_loop().run_in_executor(None, reader.readline)
                if not line:
                    logger.info("Host stdout closed")
                    break

                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.debug("Invalid JSON from host: %s", line_str[:100])
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "audio":
                    # Read raw PCM bytes following the header
                    nframes = msg.get("samples", 0)
                    channels = msg.get("channels", 2)
                    sample_rate = msg.get("sample_rate", self.sample_rate)
                    expected_bytes = nframes * channels * 4  # f32 = 4 bytes

                    if expected_bytes > 0:
                        pcm = await asyncio.get_event_loop().run_in_executor(
                            None, reader.read, expected_bytes
                        )
                        if len(pcm) == expected_bytes and self._audio_callback:
                            self._audio_callback(pcm, nframes, channels, sample_rate)
                elif isinstance(msg, dict):
                    # Regular JSON response (ack, error, status)
                    self._response_queue.put_nowait(msg)

            except (OSError, ValueError) as e:
                logger.error("Error reading host stdout: %s", e)
                break

    async def _read_stderr(self) -> None:
        """Background task: log stderr from the host process."""
        if self._process is None or self._process.stderr is None:
            return

        reader = self._process.stderr
        while self._running:
            try:
                line = await asyncio.get_event_loop().run_in_executor(None, reader.readline)
                if not line:
                    break
                logger.debug("[host] %s", line.decode(errors="replace").rstrip())
            except OSError:
                break

    @property
    def is_running(self) -> bool:
        return self._running and self.has_live_process


def _atexit_cleanup_host() -> None:
    manager = _host_manager
    if manager is None:
        return
    manager.cleanup_orphaned_process()


# Singleton
_host_manager: HostManager | None = None
atexit.register(_atexit_cleanup_host)


def get_host_manager() -> HostManager:
    global _host_manager
    if _host_manager is None:
        _host_manager = HostManager()
    return _host_manager


def configure_host_manager(
    *,
    binary_path: str | None = None,
    sample_rate: int | None = None,
    buffer_size: int | None = None,
    audio_engine: str | None = None,
    bit_depth: str | None = None,
) -> HostManager:
    host = get_host_manager()
    host.configure(
        binary_path=binary_path,
        sample_rate=sample_rate,
        buffer_size=buffer_size,
        audio_engine=audio_engine,
        bit_depth=bit_depth,
    )
    return host
