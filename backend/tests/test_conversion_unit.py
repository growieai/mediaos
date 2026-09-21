"""Strict conversion input and non-delivery contracts need no infrastructure."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.conversion.schemas import (
    ConsentEvidence,
    ConversionExportInput,
    ConversionRequestInput,
    ConversionReviewInput,
    ConversionRevokeInput,
    DestinationInput,
    ExportReceiptPayload,
    HandoffPayload,
)


@pytest.fixture(scope="session", autouse=True)
def database():
    """Override the integration-only autouse fixture for this pure module."""


def destination_payload(**changes):
    return {
        "schema_version": 1,
        "destination_key": str(uuid4()),
        "mode": "MANUAL_EXPORT",
        "label": "Internal reviewed export",
        "enabled": True,
        "idempotency_key": str(uuid4()),
        **changes,
    }


def request_payload(approval=None, destination=None, **changes):
    now = datetime.now(UTC)
    return {
        "schema_version": 1,
        "request_reference": str(uuid4()),
        "subject_reference": "subject-1",
        "business_reference": "business-1",
        "mode": "MANUAL",
        "purpose": "BUSINESS_AUDIT_REQUEST",
        "approval_record_id": str(approval or uuid4()),
        "community_event_id": None,
        "destination_version_id": str(destination or uuid4()),
        "idempotency_key": str(uuid4()),
        "consent": {
            "schema_version": 1,
            "purpose": "BUSINESS_AUDIT_REQUEST",
            "statement": "The participant explicitly requested this one business audit handoff.",
            "origin": "internal:recorded-explicit-request",
            "captured_at": now.isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
        },
        **changes,
    }


def validate(schema, payload):
    return schema.model_validate_json(json.dumps(payload), strict=True)


@pytest.mark.parametrize("mode", ["MANUAL", "FIXTURE"])
def test_request_preserves_provenance_and_explicit_consent(mode):
    value = validate(ConversionRequestInput, request_payload(mode=mode))
    assert value.mode == mode and value.consent.purpose == value.purpose


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject_reference", "person@example.com"),
        ("business_reference", "+34 123 456"),
        ("request_reference", "https://example.org"),
        ("subject_reference", ""),
        ("mode", "PLATFORM_VERIFIED"),
        ("purpose", "MARKETING"),
        ("consent", None),
        ("community_event_id", "invented"),
        ("destination_version_id", "not-a-uuid"),
    ],
)
def test_rejects_untyped_request_fields(field, value):
    with pytest.raises(ValidationError):
        validate(ConversionRequestInput, request_payload(**{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("statement", " "),
        ("origin", ""),
        ("statement", "x" * 2001),
        ("purpose", "GENERAL_CONTACT"),
        ("captured_at", "2026-09-21T09:00:00"),
        ("expires_at", "2026-09-22T09:00:00+02:00"),
    ],
)
def test_consent_requires_bounded_explicit_evidence_and_utc(field, value):
    payload = request_payload()["consent"]
    payload[field] = value
    with pytest.raises(ValidationError):
        validate(ConsentEvidence, payload)


def test_consent_expiry_must_follow_capture():
    payload = request_payload()["consent"]
    payload["expires_at"] = payload["captured_at"]
    with pytest.raises(ValidationError):
        validate(ConsentEvidence, payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mode", "WEBHOOK"),
        ("enabled", "true"),
        ("label", " "),
        ("destination_key", "https://production.example"),
    ],
)
def test_destination_cannot_be_an_automatic_sender(field, value):
    with pytest.raises(ValidationError):
        validate(DestinationInput, destination_payload(**{field: value}))


def test_review_requires_explicit_attestation():
    payload = {
        "request_hash": "a" * 64,
        "destination_hash": "b" * 64,
        "consent_hash": "c" * 64,
        "consent_attested": True,
        "comment": "Reviewed the explicit request.",
    }
    assert validate(ConversionReviewInput, payload).consent_attested
    for value in (False, "true", None):
        with pytest.raises(ValidationError):
            validate(ConversionReviewInput, {**payload, "consent_attested": value})


def test_extra_contact_and_ai_consent_fields_are_rejected():
    for field in ("email", "phone", "model_inferred_consent", "send_now"):
        with pytest.raises(ValidationError):
            validate(ConversionRequestInput, request_payload(**{field: "anything"}))


def test_review_and_export_hashes_are_exact():
    with pytest.raises(ValidationError):
        validate(
            ConversionExportInput,
            {
                "idempotency_key": "key",
                "request_hash": "wrong",
                "destination_hash": "a" * 64,
                "attestation_id": str(uuid4()),
            },
        )
    with pytest.raises(ValidationError):
        validate(ConversionRevokeInput, {"reason": " "})


@pytest.mark.parametrize("field", ["delivered", "network_performed", "audit_completed"])
def test_handoff_contract_cannot_claim_external_work(field):
    payload = {
        "schema_version": 1,
        "request_id": str(uuid4()),
        "request_revision": 1,
        "request_hash": "a" * 64,
        "subject_reference": "subject",
        "business_reference": "business",
        "purpose": "BUSINESS_AUDIT_REQUEST",
        "destination_version_id": str(uuid4()),
        "destination_hash": "b" * 64,
        "consent_attestation_id": str(uuid4()),
        "consent_expires_at": "2026-09-22T09:00:00Z",
        "workflow_run_id": str(uuid4()),
        "approval_record_id": str(uuid4()),
        "community_event_id": None,
    }
    assert not validate(HandoffPayload, payload).delivered
    with pytest.raises(ValidationError):
        validate(HandoffPayload, {**payload, field: True})


@pytest.mark.parametrize(
    "field", ["subject_reference", "business_reference", "consent", "consent_expires_at"]
)
def test_operational_export_receipt_excludes_subject_and_consent_data(field):
    receipt = {
        "schema_version": 1,
        "export_id": str(uuid4()),
        "request_id": str(uuid4()),
        "content_hash": "a" * 64,
        "status": "EXPORTED_FOR_MANUAL_HANDOFF",
        "mode": "MANUAL_EXPORT",
        "delivered": False,
        "network_performed": False,
        "audit_completed": False,
    }
    assert validate(ExportReceiptPayload, receipt).content_hash == "a" * 64
    with pytest.raises(ValidationError):
        validate(ExportReceiptPayload, {**receipt, field: "must remain in guarded handoff storage"})
