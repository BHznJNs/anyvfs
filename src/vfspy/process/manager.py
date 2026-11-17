"""Manage one native bridge subprocess, independently of the RPC server.

Example with a caller-owned, already started RPC server::

    from vfspy.process import BridgeProcess, BridgeProcessConfig
    from vfspy.server import RpcServer

    async def run_bridge(server: RpcServer, mountpoint: str) -> None:
        settings = BridgeProcessConfig(
            mountpoint=mountpoint,
            endpoint=server.endpoint,
            token=server.token,
            max_message_bytes=server.max_message_bytes,
            # Editable installs must also set executable to the built bridge path.
        )
        async with BridgeProcess(settings) as process:
            await server.wait_ready(timeout=30)
            # Use the mounted filesystem here.
            # Drain RPC notices before checking the subprocess exit status.
            await server.close(timeout=10)
            await process.wait(timeout=10)
        # Context exit reaps the process, including on errors or cancellation.

``start()`` only creates the process; mount readiness belongs to
``RpcServer.wait_ready()``. Stop the RPC session and drain its notices before
calling ``wait()``. ``close()`` is a fallback that terminates the process, so it
cannot guarantee delivery of the final filesystem events.

Create, use and close an instance on the same asyncio event loop. Instances
are single-use and do not restart processes or manage mount sessions.
"""

from __future__ import annotations

import asyncio
import math
import os
import subprocess
from contextlib import ExitStack, suppress
from importlib.resources import as_file, files
from typing import Self

from pydantic import TypeAdapter

from .config import BridgeProcessConfig, PositiveTimeout
from .errors import BridgeProcessError

_STDERR_LIMIT = 64 << 10
_TIMEOUT_ADAPTER = TypeAdapter(PositiveTimeout)


class BridgeProcess:
    """Launch and reap a single Go bridge using caller-supplied RPC credentials.

    By default the executable is loaded from the wheel's ``vfspy/_bin`` resource.
    Editable installs must supply ``executable``, for example the absolute path
    to ``bin/vfspy-native.exe``. No shell is used. The token is passed only in the
    child's environment, without changing the parent's environment.

    stderr is continuously drained and its last 64 KiB retained; stdout and
    stdin are discarded. ``terminate()`` allows ``stop_timeout`` seconds for
    exit, then kills and reaps the child. On Windows termination is forceful;
    request graceful unmount through the RPC server first on every platform.
    """

    def __init__(self, settings: BridgeProcessConfig) -> None:
        self._settings = settings
        self._arguments = (
            "--endpoint",
            settings.endpoint,
            "--mountpoint",
            settings.mountpoint,
            "--max-message-bytes",
            str(settings.max_message_bytes),
            "--startup-timeout",
            f"{math.ceil(settings.startup_timeout * 1_000)}ms",
        )
        self._executable = (
            settings.executable.resolve() if settings.executable is not None else None
        )
        self._resources = ExitStack()
        self._process: asyncio.subprocess.Process | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._wait_task: asyncio.Task[int] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._stop_lock = asyncio.Lock()
        self._stderr = bytearray()
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process is not None else None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def stderr(self) -> str:
        """The diagnostic tail collected so far, decoded with UTF-8 replacement."""
        return self._stderr.decode("utf-8", errors="replace")

    async def start(self) -> Self:
        if self._closed or self._start_task is not None:
            raise RuntimeError("process can only be started once")
        # Shield creation so cancellation cannot orphan a child before we own it.
        self._start_task = asyncio.create_task(self._spawn())
        try:
            await asyncio.shield(self._start_task)
            if self._closed:
                raise RuntimeError("process closed while starting")
        except BaseException:
            await self.close()
            raise
        return self

    async def _spawn(self) -> None:
        try:
            executable = self._executable
            if executable is None:
                name = "vfspy-native.exe" if os.name == "nt" else "vfspy-native"
                resource = files("vfspy").joinpath("_bin", name)
                if not resource.is_file():
                    raise FileNotFoundError(
                        "bundled native bridge not found; build it with "
                        "scripts/build_bridge.py and set BridgeProcessConfig(executable=...)"
                    )
                executable = self._resources.enter_context(as_file(resource))
            env = os.environ.copy()
            env["VFSPY_TOKEN"] = self._settings.token
            self._process = await asyncio.create_subprocess_exec(
                str(executable),
                *self._arguments,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            reader = asyncio.create_task(self._read_stderr())
            self._wait_task = asyncio.create_task(self._reap(reader))
        except BaseException:
            self._resources.close()
            raise

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while chunk := await self._process.stderr.read(8192):
            self._stderr.extend(chunk)
            if len(self._stderr) > _STDERR_LIMIT:
                del self._stderr[:-_STDERR_LIMIT]

    async def _reap(self, reader: asyncio.Task[None]) -> int:
        assert self._process is not None
        try:
            returncode = await self._process.wait()
            await reader
            return returncode
        finally:
            self._resources.close()

    async def wait(self, timeout: float | None = None, *, check: bool = True) -> int:
        """Wait for exit and stderr EOF; timeout/cancellation leaves the child running.

        A nonzero exit raises ``BridgeProcessError`` unless ``check=False``.
        This checks only the process exit, independently of RPC session success.
        """
        task = self._require_started()
        returncode = await asyncio.wait_for(asyncio.shield(task), timeout)
        if check and returncode != 0:
            raise BridgeProcessError(returncode, self.stderr)
        return returncode

    async def terminate(self, timeout: float | None = None) -> int:
        """Terminate, then kill on timeout, and reap; intentional exit is unchecked."""
        task = self._require_started()
        timeout = (
            self._settings.stop_timeout
            if timeout is None
            else _TIMEOUT_ADAPTER.validate_python(timeout)
        )
        async with self._stop_lock:
            assert self._process is not None
            if self.running:
                with suppress(ProcessLookupError):
                    self._process.terminate()
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout)
            except TimeoutError:
                return await self._kill()

    async def kill(self) -> int:
        """Force termination and reap the child; intentional exit is unchecked."""
        self._require_started()
        async with self._stop_lock:
            return await self._kill()

    async def _kill(self) -> int:
        assert self._process is not None and self._wait_task is not None
        if self.running:
            with suppress(ProcessLookupError):
                self._process.kill()
        return await asyncio.shield(self._wait_task)

    async def close(self) -> None:
        """Idempotently terminate and reap, including when this await is cancelled."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self) -> None:
        if self._start_task is not None:
            with suppress(Exception):
                await self._start_task
        try:
            if self._process is not None:
                await self.terminate()
        finally:
            self._resources.close()

    def _require_started(self) -> asyncio.Task[int]:
        if self._wait_task is None:
            raise RuntimeError("process has not started")
        return self._wait_task

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()
