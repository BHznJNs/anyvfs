from pydantic import Field, StrictBool

from ..utils.field import PositiveTimeout
from ..utils.model import FrozenModel


class InitialEntry(FrozenModel):
    path: str = Field(strict=True, min_length=2, pattern=r"^/")
    directory: StrictBool = False
    data: bytes = Field(default=b"", strict=True, max_length=1 << 30)


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
    entries: tuple[InitialEntry, ...] = ()
    startup_timeout: PositiveTimeout | None = None
    stop_timeout: PositiveTimeout | None = None
