import math
import os
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ..utils.model import FrozenModel

PositiveTimeout = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
_MAX_GO_DURATION_MILLISECONDS = ((1 << 63) - 1) // 1_000_000

# Loopback endpoint with a decimal port in 1..65535 (leading zeroes allowed).
_ENDPOINT_PATTERN = (
    r"^127\.0\.0\.1:0*(?:[1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
    r"65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])$"
)


class BridgeProcessConfig(FrozenModel):
    mountpoint: str = Field(strict=True, min_length=1, pattern=r"^[^\x00]+$")
    endpoint: str = Field(strict=True, pattern=_ENDPOINT_PATTERN)
    token: str = Field(strict=True, min_length=1, pattern=r"^[^\x00]+$", repr=False)
    max_message_bytes: int = Field(default=16 << 20, strict=True, gt=0, lt=1 << 31)
    executable: Path | None = None
    startup_timeout: PositiveTimeout = Field(
        default=25.0,
        lt=_MAX_GO_DURATION_MILLISECONDS / 1_000,
        description=(
            "Bootstrap deadline in seconds, rounded up to milliseconds within Go's duration range."
        ),
    )
    stop_timeout: PositiveTimeout = 5.0

    def to_arguments(self) -> tuple[str, ...]:
        return (
            "--endpoint",
            self.endpoint,
            "--mountpoint",
            self.mountpoint,
            "--max-message-bytes",
            str(self.max_message_bytes),
            "--startup-timeout",
            f"{math.ceil(self.startup_timeout * 1_000)}ms",
        )

    def to_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["VFSPY_TOKEN"] = self.token
        return env
