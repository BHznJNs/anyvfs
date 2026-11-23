from collections.abc import Iterable
from pathlib import Path
from typing import Self

from .mountpoint import Mountpoint
from ..process import BridgeProcess, BridgeProcessConfig
from ..server import RpcServer, RpcServerConfig, EventHandler, InitialEntry


class VirtualFileSystem:
    def __init__(self,
                 mountpoint: Mountpoint,
                 server: RpcServer,
                 process: BridgeProcess):
        self._mountpoint = mountpoint
        self._server = server
        self._process = process

    @classmethod
    async def create(cls,
                     mountpoint: Mountpoint,
                     *,
                     on_event: EventHandler,
                     entries: Iterable[InitialEntry] | None = None,
                     max_file_bytes: int = 64 << 20,      # 64 MiB
                     max_total_bytes: int = 512 << 20,    # 512 MiB
                     case_sensitive: bool = True,
                     max_message_bytes: int = 16 << 20,   # 16 MiB
                     executable: Path | None = None,
                     startup_timeout: float = 25.0,
                     stop_timeout: float = 5.0) -> Self:
        await mountpoint.prepare()
        server = await RpcServer.create(
            settings=RpcServerConfig(
                case_sensitive=case_sensitive,
                max_file_bytes=max_file_bytes,
                max_total_bytes=max_total_bytes,
                max_message_bytes=max_message_bytes,
                entries=tuple(entries or ()),
                startup_timeout=startup_timeout,
                stop_timeout=stop_timeout,
            ),
            on_event=on_event,
        )
        process = await BridgeProcess(
            BridgeProcessConfig(
                mountpoint=str(mountpoint.path),
                endpoint=server.endpoint,
                token=server.token,
                executable=executable,
                max_message_bytes=max_message_bytes,
                startup_timeout=startup_timeout,
                stop_timeout=stop_timeout,
            )
        ).start()
        return cls(mountpoint, server, process)

    async def close(self):
        await self._server.close()
        await self._process.close()
        await self._mountpoint.close()
