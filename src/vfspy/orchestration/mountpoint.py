from __future__ import annotations

import ntpath
import os
import sys
from contextlib import suppress
from pathlib import Path, PureWindowsPath


_IS_WINDOWS = os.name == "nt"

def _is_reserved_windows_name(name: str) -> bool:
    is_reserved = getattr(ntpath, "isreserved", None)
    if is_reserved is not None:
        return is_reserved(name)
    return (
        name.endswith((".", " "))
        or any(character in '<>:"|?*' or ord(character) < 32 for character in name)
        or PureWindowsPath(name).is_reserved()
    )

class MountPoint:
    def __init__(self, path: str | os.PathLike[str]):
        self._path = Path(path)
        self._validate(self._path)

        # only used on Linux/macOS
        self._created: os.stat_result | None = None

    def _validate(self, path: Path):
        if not path.is_absolute():
            raise ValueError("mountpoint must be absolute")
        if "\x00" in str(path):
            raise ValueError("mountpoint contains a NUL character")

        name = path.name
        if _IS_WINDOWS and _is_reserved_windows_name(name):
            raise ValueError(f"invalid Windows mountpoint directory name: {name!r}")

        if not path.parent.is_dir():
            raise ValueError("mountpoint parent must be a existing directory")

    async def prepare(self):
        """
        FUSE on Linux/macOS mounts over an existing directory.
        And WinFsp creates the directory itself.
        """
        if sys.platform == "win32":
            if self._path.exists():
                raise RuntimeError("mountpoint already exists")
        else:
            self._path.mkdir()
            self._created = self._path.lstat()

    async def close(self):
        if self._created is None: return

        # remove the created directory on Linux/macOS
        with suppress(FileNotFoundError):
            if os.path.samestat(self._created, self._path.lstat()):
                self._path.rmdir()
        self._created = None
