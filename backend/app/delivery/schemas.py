"""Strict contracts for internal delivery rehearsal; no live dispatch exists."""

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.models.schemas import StrictModel, TextBlock

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DeliveryInput(StrictModel):
    target_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    mode: Literal["DRY_RUN"] = "DRY_RUN"


class DeliveryTargetConfig(StrictModel):
    schema_version: Literal[1] = 1
    mode: Literal["DRY_RUN"] = "DRY_RUN"
    adapter_version: Literal["dry-run-v1"] = "dry-run-v1"


class DeliverySlide(StrictModel):
    index: int = Field(ge=1, le=20)
    filename: str = Field(pattern=r"^slide-[0-9]{2}\.png$")
    sha256: Digest
    width: Literal[1080] = 1080
    height: Literal[1350] = 1350
    media_type: Literal["image/png"] = "image/png"


class DeliveryPlan(StrictModel):
    schema_version: Literal[1] = 1
    mode: Literal["DRY_RUN"] = "DRY_RUN"
    adapter_version: Literal["dry-run-v1"] = "dry-run-v1"
    target_id: UUID
    render_run_id: UUID
    manifest_hash: Digest
    language: str = Field(min_length=1, max_length=100)
    caption: TextBlock
    slides: list[DeliverySlide] = Field(min_length=1, max_length=20)
    package_sha256: Digest
    post_id: None = None

    @model_validator(mode="after")
    def ordered_files(self):
        for index, slide in enumerate(self.slides, 1):
            if slide.index != index or slide.filename != f"slide-{index:02d}.png":
                raise ValueError(
                    "Delivery slides must have consecutive indices and exact filenames"
                )
        return self


class DeliveryReceipt(StrictModel):
    schema_version: Literal[1] = 1
    mode: Literal["DRY_RUN"] = "DRY_RUN"
    status: Literal["DRY_RUN_COMPLETE"] = "DRY_RUN_COMPLETE"
    adapter_version: Literal["dry-run-v1"] = "dry-run-v1"
    network_performed: Literal[False] = False
    package_sha256: Digest
    payload_sha256: Digest
    manifest_hash: Digest
    caption_sha256: Digest
    slide_sha256: list[Digest] = Field(min_length=1, max_length=20)
    validated_at: datetime
    post_id: None = None
    published_at: None = None

    @model_validator(mode="after")
    def utc_timestamp(self):
        if self.validated_at.tzinfo is None or self.validated_at.utcoffset() != timedelta(0):
            raise ValueError("Validation timestamp must use UTC")
        return self
