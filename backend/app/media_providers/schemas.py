"""Validated provider contracts; remote extras never become application authority."""

import ipaddress
import re
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.:-]+$")]
Status = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "BLOCKED", "CANCELED"]


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        len(value) > 8192
        or re.search(r"[\s\x00-\x1f\x7f]", value)
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
        or "." not in parsed.hostname
        or parsed.hostname.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("Expected a public credential-free HTTPS URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return value
    if not address.is_global:
        raise ValueError("Expected a public HTTPS URL")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)


class Alignment(StrictModel):
    characters: list[str] = Field(min_length=1, max_length=10000)
    character_start_times_seconds: list[float]
    character_end_times_seconds: list[float]

    @model_validator(mode="after")
    def timings(self) -> "Alignment":
        starts, ends = self.character_start_times_seconds, self.character_end_times_seconds
        if not (len(self.characters) == len(starts) == len(ends)):
            raise ValueError("Alignment lengths differ")
        if any(len(c) != 1 for c in self.characters):
            raise ValueError("Alignment must contain characters")
        if any(not 0 <= start <= end <= 300 for start, end in zip(starts, ends, strict=True)):
            raise ValueError("Invalid alignment interval")
        if starts != sorted(starts) or ends != sorted(ends) or ends[-1] == 0:
            raise ValueError("Alignment is not ordered")
        return self


class SpeechResult(StrictModel):
    audio: bytes = Field(repr=False, min_length=1, max_length=12_000_000)
    alignment: Alignment
    request_id: Identifier | None = None
    cost_usd: Decimal | None = None
    tokens: int | None = None
    usage_characters: int | None = None


class UploadResult(StrictModel):
    asset_id: Identifier
    mime_type: Literal["image/png", "image/jpeg", "audio/mpeg", "audio/mp3", "audio/wav"]
    size_bytes: int = Field(gt=0, le=32_000_000)


class SubmittedJob(StrictModel):
    request_id: Identifier
    status: Status


class JobStatus(SubmittedJob):
    output_url: str | None = Field(default=None, repr=False)
    duration_seconds: float | None = Field(default=None, gt=0, le=300)
    error_category: Literal["GENERATION_FAILED", "MODERATION_BLOCKED"] | None = None

    @field_validator("output_url")
    @classmethod
    def valid_url(cls, value: str | None) -> str | None:
        return https_url(value) if value is not None else None

    @model_validator(mode="after")
    def complete_output(self) -> "JobStatus":
        if (self.status == "COMPLETED") != (self.output_url is not None):
            raise ValueError("Only completed jobs must have output")
        return self


class CostEstimate(StrictModel):
    credits: Decimal = Field(ge=0)
    usd: Decimal = Field(ge=0)


class KlingInput(StrictModel):
    image_url: str
    prompt: str = Field(default="", max_length=4000)
    duration: int = Field(default=5, ge=3, le=15)
    sound: Literal["on", "off"] = "off"
    cfg_scale: float = Field(default=0.5, ge=0, le=1)
    multi_shots: Literal[False] = False
    last_image_url: str | None = None

    _urls = field_validator("image_url")(https_url)

    @field_validator("last_image_url")
    @classmethod
    def optional_url(cls, value: str | None) -> str | None:
        return https_url(value) if value is not None else None


class WanInput(StrictModel):
    image_url: str
    audio_url: str | None = None
    prompt: str = Field(default="", max_length=4000)
    duration: int = Field(default=5, ge=2, le=15)
    resolution: Literal["720p", "1080p"] = "1080p"
    seed: int | None = Field(default=None, ge=1, le=2147483646)
    prompt_extend: Literal[False] = False
    negative_prompt: str = Field(default="", max_length=2000)

    _image_url = field_validator("image_url")(https_url)

    @field_validator("audio_url")
    @classmethod
    def optional_audio_url(cls, value: str | None) -> str | None:
        return https_url(value) if value is not None else None
