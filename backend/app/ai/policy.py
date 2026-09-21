"""Explicit administrator configuration; there is no implicit model or paid budget."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.models.schemas import StrictModel

Money = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?$")]


class TextAIPolicy(StrictModel):
    schema_version: Literal[1] = 1
    enabled: bool
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    max_input_bytes: int = Field(ge=4096, le=131072)
    max_output_tokens: int = Field(ge=256, le=8192)
    max_calls_per_run: int = Field(ge=1, le=3)
    max_calls_per_day: int = Field(ge=1, le=1000)
    input_usd_per_million_tokens: Money
    output_usd_per_million_tokens: Money
    per_run_usd: Money
    per_day_usd: Money
    price_reference: str = Field(min_length=1, max_length=1000)
    price_checked_at: datetime
    expires_at: datetime

    @field_validator("price_checked_at", "expires_at")
    @classmethod
    def utc(cls, value: datetime):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("UTC timestamp required")
        return value

    @model_validator(mode="after")
    def limits(self):
        if any(
            not 0 < Decimal(value) <= maximum
            for value, maximum in (
                (self.input_usd_per_million_tokens, 1000),
                (self.output_usd_per_million_tokens, 1000),
                (self.per_run_usd, 1000),
                (self.per_day_usd, 10000),
            )
        ):
            raise ValueError("Positive bounded rates and spend limits required")
        if (
            not self.price_checked_at
            < self.expires_at
            <= self.price_checked_at + timedelta(days=30)
        ):
            raise ValueError("Policy must expire within thirty days of its price check")
        return self
