"""Typed manual metrics; unavailable counters remain null rather than becoming zero."""

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.models.schemas import StrictModel

MAX_COUNT = 9007199254740991
METRICS = ("reach", "saves", "shares", "comments", "follows")
ALGORITHM_VERSION = "descriptive-v1"
Count = Annotated[int, Field(ge=0, le=MAX_COUNT)]
Delta = Annotated[int, Field(ge=-MAX_COUNT, le=MAX_COUNT)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Mode = Literal["MANUAL", "FIXTURE"]
Direction = Literal["UNKNOWN", "INCREASED", "UNCHANGED", "DECREASED"]


class MetricSubjectPayload(StrictModel):
    schema_version: Literal[1] = 1
    render_run_id: UUID
    mode: Mode
    platform_label: str = Field(min_length=1, max_length=100)
    external_reference: str | None = Field(max_length=512)
    provenance_note: str = Field(min_length=1, max_length=2000)

    @field_validator("platform_label", "external_reference", "provenance_note")
    @classmethod
    def meaningful_text(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Metric provenance fields cannot be blank")
        return value

    @model_validator(mode="after")
    def provenance_mode(self):
        if self.mode == "MANUAL" and self.external_reference is None:
            raise ValueError("Manual observations require an external reference")
        if self.mode == "FIXTURE" and self.external_reference is not None:
            raise ValueError("Fixture observations cannot claim an external reference")
        return self


class MetricSubjectInput(MetricSubjectPayload):
    idempotency_key: Key


class MetricSnapshotPayload(StrictModel):
    schema_version: Literal[1] = 1
    observed_at: datetime
    scope: Literal["LIFETIME_CUMULATIVE"]
    reach: Count | None
    saves: Count | None
    shares: Count | None
    comments: Count | None
    follows: Count | None
    evidence_text: str = Field(min_length=1, max_length=20000)
    definition_notes: str = Field(min_length=1, max_length=2000)

    @field_validator("evidence_text", "definition_notes")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("Metric evidence and definitions cannot be blank")
        return value

    @model_validator(mode="after")
    def utc_observation(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError("Metric observation timestamp must use UTC")
        return self


class MetricSnapshotInput(MetricSnapshotPayload):
    idempotency_key: Key


class MetricLearningInput(StrictModel):
    idempotency_key: Key
    baseline_snapshot_id: UUID
    current_snapshot_id: UUID

    @model_validator(mode="after")
    def different_snapshots(self):
        if self.baseline_snapshot_id == self.current_snapshot_id:
            raise ValueError("Learning requires two different snapshots")
        return self


class MetricComparison(StrictModel):
    baseline: Count | None
    current: Count | None
    delta: Delta | None
    direction: Direction

    @model_validator(mode="after")
    def exact_delta(self):
        expected = (
            None if self.baseline is None or self.current is None else self.current - self.baseline
        )
        direction = (
            "UNKNOWN"
            if expected is None
            else "INCREASED"
            if expected > 0
            else "DECREASED"
            if expected < 0
            else "UNCHANGED"
        )
        if self.delta != expected or self.direction != direction:
            raise ValueError("Metric delta must reflect the exact observed counters")
        return self


class MetricComparisons(StrictModel):
    reach: MetricComparison
    saves: MetricComparison
    shares: MetricComparison
    comments: MetricComparison
    follows: MetricComparison


class MetricLearningPayload(StrictModel):
    schema_version: Literal[1] = 1
    algorithm_version: Literal["descriptive-v1"] = "descriptive-v1"
    subject_id: UUID
    baseline_snapshot_id: UUID
    current_snapshot_id: UUID
    baseline_snapshot_hash: Digest
    current_snapshot_hash: Digest
    mode: Mode
    scope: Literal["LIFETIME_CUMULATIVE"]
    baseline_observed_at: datetime
    current_observed_at: datetime
    definition_notes: str = Field(min_length=1, max_length=2000)
    metrics: MetricComparisons
    status: Literal["DESCRIPTIVE", "INSUFFICIENT_DATA"]
    limitations: list[
        Literal["SELF_REPORTED", "FIXTURE", "DESCRIPTIVE_ONLY", "NO_CAUSAL_INFERENCE"]
    ]
    causal_claim: Literal[False] = False
    policy_updated: Literal[False] = False

    @model_validator(mode="after")
    def descriptive_contract(self):
        for instant in (self.baseline_observed_at, self.current_observed_at):
            if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
                raise ValueError("Learning timestamps must use UTC")
        if (
            self.baseline_snapshot_id == self.current_snapshot_id
            or self.baseline_observed_at >= self.current_observed_at
        ):
            raise ValueError("Learning snapshots must be distinct and strictly time ordered")
        expected_limitations = [
            "SELF_REPORTED" if self.mode == "MANUAL" else "FIXTURE",
            "DESCRIPTIVE_ONLY",
            "NO_CAUSAL_INFERENCE",
        ]
        if self.limitations != expected_limitations:
            raise ValueError("Learning limitations must preserve observation provenance")
        status = (
            "INSUFFICIENT_DATA"
            if all(getattr(self.metrics, metric).delta is None for metric in METRICS)
            else "DESCRIPTIVE"
        )
        if self.status != status:
            raise ValueError("Learning status must reflect counter availability")
        return self
