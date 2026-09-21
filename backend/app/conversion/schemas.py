"""Bounded internal conversion contracts; no contact details or inferred consent."""

from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.models.schemas import StrictModel

Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
OpaqueReference = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Key = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class DestinationPayload(StrictModel):
    schema_version: Literal[1] = 1
    destination_key: OpaqueReference
    mode: Literal["MANUAL_EXPORT"] = "MANUAL_EXPORT"
    label: str = Field(min_length=1, max_length=200)
    enabled: bool

    @field_validator("label")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Destination label cannot be blank")
        return value


class DestinationInput(DestinationPayload):
    idempotency_key: Key


class ConsentEvidence(StrictModel):
    schema_version: Literal[1] = 1
    purpose: Literal["BUSINESS_AUDIT_REQUEST"] = "BUSINESS_AUDIT_REQUEST"
    statement: str = Field(min_length=1, max_length=2000)
    origin: str = Field(min_length=1, max_length=512)
    captured_at: datetime
    expires_at: datetime

    @field_validator("statement", "origin")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Explicit consent evidence cannot be blank")
        return value

    @model_validator(mode="after")
    def exact_time_window(self):
        for instant in (self.captured_at, self.expires_at):
            if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
                raise ValueError("Consent timestamps must use UTC")
        if self.expires_at <= self.captured_at:
            raise ValueError("Consent expiry must follow capture")
        return self


class ConversionRequestPayload(StrictModel):
    schema_version: Literal[1] = 1
    request_reference: OpaqueReference
    subject_reference: OpaqueReference
    business_reference: OpaqueReference
    mode: Literal["MANUAL", "FIXTURE"]
    purpose: Literal["BUSINESS_AUDIT_REQUEST"] = "BUSINESS_AUDIT_REQUEST"
    approval_record_id: UUID
    community_event_id: UUID | None
    destination_version_id: UUID
    consent: ConsentEvidence


class ConversionRequestInput(ConversionRequestPayload):
    idempotency_key: Key


class ConversionReviewInput(StrictModel):
    request_hash: Digest
    destination_hash: Digest
    consent_hash: Digest
    consent_attested: Literal[True]
    comment: str = Field(min_length=1, max_length=2000)

    @field_validator("comment")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("A human review note is required")
        return value


class ConversionRevokeInput(StrictModel):
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Revocation reason cannot be blank")
        return value


class ConversionExportInput(StrictModel):
    idempotency_key: Key
    request_hash: Digest
    destination_hash: Digest
    attestation_id: UUID


class HandoffPayload(StrictModel):
    schema_version: Literal[1] = 1
    request_id: UUID
    request_revision: int = Field(ge=1)
    request_hash: Digest
    subject_reference: OpaqueReference
    business_reference: OpaqueReference
    purpose: Literal["BUSINESS_AUDIT_REQUEST"]
    destination_version_id: UUID
    destination_hash: Digest
    consent_attestation_id: UUID
    consent_expires_at: datetime
    workflow_run_id: UUID
    approval_record_id: UUID
    community_event_id: UUID | None
    mode: Literal["MANUAL_EXPORT"] = "MANUAL_EXPORT"
    status: Literal["EXPORTED_FOR_MANUAL_HANDOFF"] = "EXPORTED_FOR_MANUAL_HANDOFF"
    delivered: Literal[False] = False
    network_performed: Literal[False] = False
    audit_completed: Literal[False] = False
    consent_provenance: Literal["OPERATOR_ASSERTION_REVIEWED"] = "OPERATOR_ASSERTION_REVIEWED"


class ExportReceiptPayload(StrictModel):
    """Operational telemetry excludes the handoff's subject and consent data."""

    schema_version: Literal[1] = 1
    export_id: UUID
    request_id: UUID
    content_hash: Digest
    status: Literal["EXPORTED_FOR_MANUAL_HANDOFF"] = "EXPORTED_FOR_MANUAL_HANDOFF"
    mode: Literal["MANUAL_EXPORT"] = "MANUAL_EXPORT"
    delivered: Literal[False] = False
    network_performed: Literal[False] = False
    audit_completed: Literal[False] = False
