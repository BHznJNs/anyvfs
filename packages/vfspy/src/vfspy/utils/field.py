from typing import Annotated

from pydantic import Field


PositiveTimeout = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
