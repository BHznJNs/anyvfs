from pydantic import BaseModel, ConfigDict, Field, StrictBool


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class InitialEntry(_FrozenModel):
    path: str = Field(strict=True, min_length=2, pattern=r"^/")
    directory: StrictBool = False
    data: bytes = Field(default=b"", strict=True, max_length=1 << 30)


class RpcServerConfig(_FrozenModel):
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
