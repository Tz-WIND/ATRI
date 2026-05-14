"""ATRI Audio Host Manager.

Manages the Rust `atri-host` child process lifecycle, sends commands via stdin,
reads JSON responses and raw PCM audio from stdout, and forwards audio to the
frontend via WebSocket broadcast.
"""

import asyncio
import json
import logging
import os
import struct
import subprocess
import sys
from asyncio import StreamReader, StreamWriter
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("atri.host")


class HostManager:
    """Manages the Rust audio engine child process."""

    def __init__(
        self,
        binary_path: str | None = None,
        sample_rate: int = 48000,
        buffer_size: int = 256,
    ):
        self.binary_path = self._resolve_binary(binary_path)
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self._process: subprocess.Popen | None = None
        self._audio_callback: Callable[[bytes, int, int], None] | None = None
        self._running = False
        self._response_queue: asyncio.Queue = asyncio.Queue()

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
        raise FileNotFoundError(
            "atri-host binary not found. Build with: cargo build --release"
        )

    def set_audio_callback(self, callback: Callable[[bytes, int, int], None]):
        """Set a callback that receives PCM audio chunks.
        Args:
            callback: fn(pcm_bytes: bytes, nframes: int, channels: int) -> None
        """
        self._audio_callback = callback

    async def start(self) -> None:
        """Spawn the Rust host process and start reading its output."""
        if self._process is not None:
            logger.warning("Host process already running")
            return

        logger.info("Starting atri-host: %s", self.binary_path)
        try:
            self._process = subprocess.Popen(
                [self.binary_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("Host binary not found at %s", self.binary_path)
            raise
        except OSError as e:
            logger.error("Failed to start host process: %s", e)
            raise

        self._running = True
        # Start background tasks for reading stdout and stderr
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())
        logger.info("atri-host started (pid=%d)", self._process.pid)

    async def stop(self) -> None:
        """Stop the Rust host process."""
        if self._process is None:
            return

        logger.info("Stopping atri-host...")
        try:
            await self.send_command("shutdown")
        except Exception:
            pass

        self._running = False
        try:
            self._process.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass

        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Host process did not exit, killing...")
            self._process.kill()
            self._process.wait()

        self._process = None
        logger.info("atri-host stopped")

    async def send_command(self, cmd: str, params: dict | None = None) -> dict:
        """Send a JSON command to the host and wait for the response."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Host process not running")

        message = {"cmd": cmd}
        if params:
            message.update(params)

        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode())
        self._process.stdin.flush()

        # Read response (non-audio JSON line)
        return await self._read_response()

    async def _read_response(self) -> dict:
        """Read a single JSON response line from the response queue."""
        try:
            return await asyncio.wait_for(self._response_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return {"type": "error", "message": "timeout waiting for host response"}

    async def _read_stdout(self) -> None:
        """Background task: read stdout lines, dispatch JSON responses and PCM audio."""
        if self._process is None or self._process.stdout is None:
            return

        reader = self._process.stdout
        while self._running:
            try:
                line = await asyncio.get_event_loop().run_in_executor(
                    None, reader.readline
                )
                if not line:
                    logger.info("Host stdout closed")
                    break

                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from host: %s", line_str[:100])
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "audio":
                    # Read raw PCM bytes following the header
                    nframes = msg.get("samples", 0)
                    channels = msg.get("channels", 2)
                    expected_bytes = nframes * channels * 4  # f32 = 4 bytes

                    if expected_bytes > 0:
                        pcm = await asyncio.get_event_loop().run_in_executor(
                            None, reader.read, expected_bytes
                        )
                        if len(pcm) == expected_bytes and self._audio_callback:
                            self._audio_callback(pcm, nframes, channels)
                else:
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
                line = await asyncio.get_event_loop().run_in_executor(
                    None, reader.readline
                )
                if not line:
                    break
                logger.debug("[host] %s", line.decode(errors="replace").rstrip())
            except OSError:
                break

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None


# Singleton
_host_manager: HostManager | None = None


def get_host_manager() -> HostManager:
    global _host_manager
    if _host_manager is None:
        _host_manager = HostManager()
    return _host_manager
