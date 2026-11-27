from collections.abc import Iterator
from typing import Literal

from pydantic import Field, StrictBool

from .._rpc import bootstrap_pb2 as pb
from ..utils.field import PositiveTimeout
from ..utils.model import FrozenModel


class InitialFile(FrozenModel):
    kind: Literal["file"] = "file"
    path: str = Field(strict=True, min_length=2, pattern=r"^/")
    data: bytes = Field(default=b"", strict=True, max_length=1 << 30)


class InitialDirectory(FrozenModel):
    kind: Literal["directory"] = "directory"
    path: str = Field(strict=True, min_length=2, pattern=r"^/")


class RpcServerConfig(FrozenModel):
    case_sensitive: StrictBool = True
    max_file_bytes: int = Field(strict=True, gt=0, le=1 << 30)
    max_total_bytes: int = Field(strict=True, gt=0, le=1 << 40)
    max_message_bytes: int = Field(
        default=16 << 20,
        strict=True,
        gt=0,
        le=(1 << 31) - 1,
        description="Maximum encoded gRPC message size in bytes, for both sends and receives.",
    )
    entries: tuple[InitialFile | InitialDirectory, ...] = ()
    startup_timeout: PositiveTimeout | None = None
    stop_timeout: PositiveTimeout | None = None

    def iter_bootstrap_items(self) -> Iterator[pb.BootstrapItem]:
        yield pb.BootstrapItem(
            config=pb.Config(
                case_sensitive=self.case_sensitive,
                max_file_bytes=self.max_file_bytes,
                max_total_bytes=self.max_total_bytes,
            )
        )
        for entry in self.entries:
            if isinstance(entry, InitialFile):
                yield pb.BootstrapItem(file=pb.InitialFile(path=entry.path, data=entry.data))
            else:
                yield pb.BootstrapItem(directory=pb.InitialDirectory(path=entry.path))
