"""Strict, bounded schemas for manual community observations and reviewed drafts."""

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.models.schemas import StrictModel, TextBlock

Key = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class CommunityEventPayload(StrictModel):
    schema_version: Literal[1] = 1
    mode: Literal["MANUAL", "FIXTURE"]
    origin: str = Field(min_length=1, max_length=512)
    participant_reference: str = Field(min_length=1, max_length=128)
    comment_text: str = Field(min_length=1, max_length=4000)
    captured_at: datetime

    @field_validator("origin", "participant_reference", "comment_text")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("Community input fields cannot be blank")
        return value

    @model_validator(mode="after")
    def utc_capture(self):
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() != timedelta(0):
            raise ValueError("Community captured_at must use UTC")
        return self


class CommunityEventInput(CommunityEventPayload):
    idempotency_key: Key


class CommunityReviewInput(StrictModel):
    idempotency_key: Key
    fact_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def unique_facts(self):
        if len(self.fact_ids) != len(set(self.fact_ids)):
            raise ValueError("Community fact references must be unique")
        return self


class CommunityDecisionInput(StrictModel):
    draft_hash: Digest
    qa_hash: Digest
    comment: str | None = Field(default=None, max_length=2000)


class CommunityClassification(StrictModel):
    schema_version: Literal[1] = 1
    category: Literal["ACKNOWLEDGEMENT", "SOURCE_REQUEST", "HUMAN_REVIEW"]
    reason_code: Literal[
        "EXACT_ACKNOWLEDGEMENT", "EXACT_SOURCE_REQUEST", "POLICY_MISSING", "UNRECOGNIZED_INPUT"
    ]

    @model_validator(mode="after")
    def consistent_reason(self):
        expected = {
            "ACKNOWLEDGEMENT": {"EXACT_ACKNOWLEDGEMENT"},
            "SOURCE_REQUEST": {"EXACT_SOURCE_REQUEST"},
            "HUMAN_REVIEW": {"POLICY_MISSING", "UNRECOGNIZED_INPUT"},
        }
        if self.reason_code not in expected[self.category]:
            raise ValueError("Classification reason does not match category")
        return self


class CommunityDraft(StrictModel):
    schema_version: Literal[1] = 1
    influencer_version_id: UUID
    character_config_version_id: UUID
    policy_hash: Digest
    language: str = Field(min_length=1, max_length=100)
    blocks: list[TextBlock] = Field(min_length=1, max_length=6)
    disclosure: str = Field(max_length=2000)
