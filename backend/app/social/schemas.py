from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.models.schemas import StrictModel

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ConnectInput(StrictModel):
    influencer_id: UUID


class PublishInput(StrictModel):
    connection_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)


class PublishSlide(StrictModel):
    index: int = Field(ge=1, le=10)
    sha256: Digest
    source_sha256: Digest
    width: Literal[1080] = 1080
    height: Literal[1350] = 1350
    mime_type: Literal["image/jpeg"] = "image/jpeg"


class PublishPlan(StrictModel):
    schema_version: Literal[1] = 1
    connection_id: UUID
    render_run_id: UUID
    manifest_hash: Digest
    caption: str = Field(min_length=1, max_length=2200)
    slides: list[PublishSlide] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def consecutive(self):
        if [slide.index for slide in self.slides] != list(range(1, len(self.slides) + 1)):
            raise ValueError("Consecutive slide indices required")
        return self


class PublishDecision(StrictModel):
    plan_hash: Digest
    decision: Literal["AUTHORIZE_PUBLISH", "REJECT"]
    reviewed_images: bool
    reviewed_caption: bool
    confirmed_account: bool
    comment: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def confirmation(self):
        if self.decision == "AUTHORIZE_PUBLISH" and not all(
            (self.reviewed_images, self.reviewed_caption, self.confirmed_account)
        ):
            raise ValueError("Exact images, caption and account review required")
        return self


class DispatchInput(StrictModel):
    confirm_public_post: Literal[True]


class ObservationInput(StrictModel):
    idempotency_key: str = Field(min_length=1, max_length=128)


class ReconcileInput(StrictModel):
    candidate_media_id: str = Field(pattern=r"^[0-9]{1,64}$")
    confirm_external_match: Literal[True]
    comment: str = Field(min_length=1, max_length=2000)


class PlatformObservation(StrictModel):
    schema_version: Literal[1] = 1
    media_id: str = Field(pattern=r"^[0-9]{1,64}$")
    captured_at: datetime
    api_version: str = Field(pattern=r"^v[0-9]{1,2}\.0$")
    raw: dict
    raw_hash: Digest
    metrics: dict[str, int | None]
    definitions: dict[str, str]

    @model_validator(mode="after")
    def valid_observation(self):
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() != timedelta(0):
            raise ValueError("UTC observation required")
        if any(v is not None and (type(v) is not int or v < 0) for v in self.metrics.values()):
            raise ValueError("Metrics must be nonnegative integer or unknown")
        return self
