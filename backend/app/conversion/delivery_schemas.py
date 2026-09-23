"""Exact consent-bound handoff contracts; a receipt never means an audit was completed."""

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.conversion.schemas import Digest, Key, OpaqueReference
from app.models.schemas import StrictModel


class BusinessIdentityInput(StrictModel):
    schema_version: Literal[1] = 1
    idempotency_key: Key
    business_reference: OpaqueReference
    legal_name: str = Field(min_length=1, max_length=200)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    registry_reference: OpaqueReference
    evidence_origin: str = Field(min_length=1, max_length=512)
    evidence_sha256: Digest
    mode: Literal["MANUAL", "FIXTURE"]
    retain_until: datetime

    @field_validator("legal_name", "evidence_origin")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Explicit business identity evidence is required")
        return value

    @field_validator("retain_until")
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Retention time must be UTC")
        return value


class BusinessReviewInput(StrictModel):
    content_hash: Digest
    identity_attested: Literal[True]
    comment: str = Field(min_length=1, max_length=2000)


class DeliveryInput(StrictModel):
    idempotency_key: Key
    business_identity_id: UUID
    transport_id: UUID
    attestation_id: UUID
    request_hash: Digest


class DeliveryAuthorization(StrictModel):
    payload_hash: Digest
    decision: Literal["AUTHORIZE", "REJECT"]
    comment: str = Field(min_length=1, max_length=2000)


class RevocationDeliveryInput(StrictModel):
    idempotency_key: Key


class DestinationReceipt(StrictModel):
    schema_version: Literal[1] = 1
    delivery_id: UUID
    payload_hash: Digest
    status: Literal["RECEIVED", "REVOKED", "NOT_FOUND"]
    receipt_reference: OpaqueReference
    audit_completed: Literal[False] = False


class SignedReceipt(StrictModel):
    receipt: DestinationReceipt
    signature: Digest
