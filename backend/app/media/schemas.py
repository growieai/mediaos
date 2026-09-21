"""Internal speaking-video requests. Pricing and spend limits are explicit administrator data."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.schemas import StrictModel

Money = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
TextPath = Annotated[
    str, Field(pattern=r"^(caption|cta|slides\.(0|[1-9][0-9]?)\.(headline|body))$")
]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")
    return value


class MediaProfile(StrictModel):
    schema_version: Literal[1] = 1
    voice_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    tts_model: Literal["eleven_multilingual_v2", "eleven_v3"] = "eleven_multilingual_v2"
    presenter_provider: Literal["heygen"] = "heygen"
    tts_usd_per_1000_characters: Money
    avatar_usd_per_second: Money
    price_reference: str = Field(min_length=1, max_length=1000)
    price_checked_at: datetime

    _utc = field_validator("price_checked_at")(utc)

    @field_validator("tts_usd_per_1000_characters", "avatar_usd_per_second")
    @classmethod
    def price(cls, value: str) -> str:
        if not 0 < Decimal(value) <= 1000:
            raise ValueError("Positive bounded price required")
        return value


class ProfileInput(StrictModel):
    visual_config_version_id: UUID
    payload: MediaProfile


class SpendPolicy(StrictModel):
    schema_version: Literal[1] = 1
    per_run_usd: Money
    per_day_usd: Money
    expires_at: datetime
    enabled: bool

    _utc = field_validator("expires_at")(utc)

    @model_validator(mode="after")
    def limits(self):
        if not (0 < Decimal(self.per_run_usd) <= 1000 and 0 < Decimal(self.per_day_usd) <= 10000):
            raise ValueError("Invalid spend ceiling")
        return self


class MediaInput(StrictModel):
    profile_id: UUID
    selected_paths: list[TextPath] = Field(min_length=1, max_length=10)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("selected_paths")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("Narration paths must be unique")
        return value


class HumanChecks(StrictModel):
    identity: bool
    voice: bool
    lip_sync: bool
    captions: bool
    disclosure: bool


class MediaDecision(StrictModel):
    manifest_hash: Digest
    decision: Literal["APPROVE", "REJECT"]
    checks: HumanChecks
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def checked(self):
        if self.decision == "APPROVE" and not all(self.checks.model_dump().values()):
            raise ValueError("Every human review check is required")
        return self
