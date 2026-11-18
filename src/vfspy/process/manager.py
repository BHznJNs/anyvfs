"""Launch and reap a native bridge subprocess.

Each instance can launch at most one subprocess and must be used on a single
asyncio event loop. For editable installs, set ``BridgeProcessConfig.executable``
to the built bridge binary.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import suppress
from typing import Self

from ._executable import bundled_bridge_executable
from .config import BridgeProcessConfig


class BridgeProcess:
    def __init__(self, settings: BridgeProcessConfig) -> None:
        self._settings = settings
        self._executable = (
            settings.executable.resolve()
            if settings.executable is not None
            else bundled_bridge_executable()
        )
        self._lifecycle_lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> Self:
        async with self._lifecycle_lock:
            if self._process is not None:
                raise RuntimeError("process can only be started once")
            self._process = await asyncio.create_subprocess_exec(
                str(self._executable),
                *self._settings.to_arguments(),
                env=self._settings.to_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        return self

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if not self.is_running: return
            process = self._process
            assert process is not None

            with suppress(ProcessLookupError):
                process.terminate()
            try:
                async with asyncio.timeout(self._settings.stop_timeout):
                    await process.wait()
            except TimeoutError:
                pass
            finally:
                # Force termination even if waiting for the child is cancelled.
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()
