from collections.abc import Iterable
from pathlib import Path

from .mountpoint import Mountpoint
from ..server import EventHandler, InitialEntry


class VirtualFileSystem:
    def __init__(
        self,
        mountpoint: Mountpoint,
        *,
        entries: Iterable[InitialEntry] = (),
        on_event: EventHandler | None = None,
        max_file_bytes: int = 64 << 20,      # 64 MiB
        max_total_bytes: int = 512 << 20,    # 512 MiB
        case_sensitive: bool = True,
        max_message_bytes: int = 16 << 20,   # 16 MiB
        executable: Path | None = None,
        startup_timeout: float = 25.0,
        stop_timeout: float = 5.0,
    ):
        self._mountpoint = mountpoint
        self._entries = tuple(entries)
        self._on_event = on_event
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._case_sensitive = case_sensitive
        self._max_message_bytes = max_message_bytes
        self._executable = executable
        self._startup_timeout = startup_timeout
        self._stop_timeout = stop_timeout
