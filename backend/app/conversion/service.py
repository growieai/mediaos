"""Database-guarded internal request review and manual JSON export; no network."""

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from app.conversion.schemas import (
    ConversionExportInput,
    ConversionRequestInput,
    ConversionRequestPayload,
    ConversionReviewInput,
    ConversionRevokeInput,
    DestinationInput,
    DestinationPayload,
    HandoffPayload,
)
from app.db.repository import Repository, canonical_hash, transaction
from app.services.workflows import typed

log = logging.getLogger("mediaos")
RECEIPT_FIELDS = (
    "id",
    "tenant_id",
    "request_id",
    "workflow_run_id",
    "attestation_id",
    "status",
    "delivered",
    "network_performed",
    "content_hash",
    "skill_run_id",
    "created_by",
    "created_at",
)


def _checked(row, schema):
    if canonical_hash(row["payload"]) != row["content_hash"]:
        raise ValueError("Conversion payload checksum mismatch")
    typed(schema, row["payload"])
    return dict(row)


def destinations(repo: Repository):
    return [_checked(row, DestinationPayload) for row in repo.all("conversion_destinations")]


def request_details(repo: Repository, request_id: UUID):
    row = _checked(repo.one("conversion_requests", id=request_id), ConversionRequestPayload)
    row["attestations"] = repo.all("conversion_attestations", request_id=request_id)
    row["revocations"] = repo.all(
        "conversion_revocations", request_reference=row["request_reference"]
    )
    # A historical receipt can be inspected, but returning the handoff itself is
    # reserved for the guarded export path, including idempotent replays.
    row["exports"] = [
        {key: item[key] for key in RECEIPT_FIELDS}
        for item in repo.all("conversion_exports", request_id=request_id)
    ]
    destination = _checked(
        repo.one("conversion_destinations", id=row["destination_version_id"]), DestinationPayload
    )
    row["destination"] = destination
    # Existence checks avoid loading every prior consent payload for each row in
    # the history view. Ownership and the precise revision series remain scoped.
    newer_request = repo.connection.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM conversion_requests WHERE tenant_id=:tenant "
            "AND request_reference=:reference AND revision>:revision)"
        ),
        {
            "tenant": repo.tenant_id,
            "reference": row["request_reference"],
            "revision": row["revision"],
        },
    ).scalar_one()
    newer_destination = repo.connection.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM conversion_destinations WHERE tenant_id=:tenant "
            "AND destination_key=:reference AND revision>:revision)"
        ),
        {
            "tenant": repo.tenant_id,
            "reference": destination["destination_key"],
            "revision": destination["revision"],
        },
    ).scalar_one()
    row["status"] = (
        "REVOKED"
        if row["revocations"]
        else "BLOCKED_FIXTURE"
        if row["mode"] == "FIXTURE"
        else "EXPIRED"
        if row["expires_at"] <= datetime.now(UTC)
        else "SUPERSEDED"
        if newer_request or newer_destination or not destination["enabled"]
        else "EXPORTED_FOR_MANUAL_HANDOFF"
        if row["exports"]
        else "REVIEWED_REQUEST"
        if row["attestations"]
        else "AWAITING_REVIEW"
    )
    row["consent_provenance"] = "OPERATOR_ASSERTION" if row["mode"] == "MANUAL" else "FIXTURE"
    row["delivered"] = False
    row["network_performed"] = False
    return row


def workflow_requests(repo: Repository, workflow_id: UUID):
    repo.one("workflow_runs", id=workflow_id)
    return [
        request_details(repo, row["id"])
        for row in repo.all("conversion_requests", workflow_run_id=workflow_id)
    ]


def create_destination(tenant: UUID, token: str, request: DestinationInput):
    request = DestinationInput.model_validate_json(request.model_dump_json(), strict=True)
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    with transaction(tenant, token) as repo:
        repo.require("ADMIN")
        result_id = repo.connection.execute(
            text("SELECT register_conversion_destination(CAST(:payload AS jsonb),:key,:hash)"),
            {
                "payload": json.dumps(payload),
                "key": request.idempotency_key,
                "hash": canonical_hash({"payload": payload}),
            },
        ).scalar_one()
        return _checked(repo.one("conversion_destinations", id=result_id), DestinationPayload)


def create_request(tenant: UUID, token: str, workflow_id: UUID, request: ConversionRequestInput):
    request = ConversionRequestInput.model_validate_json(request.model_dump_json(), strict=True)
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        repo.one("workflow_runs", id=workflow_id)
        result_id = repo.connection.execute(
            text(
                "SELECT register_conversion_request(:workflow,CAST(:payload AS jsonb),:key,:hash)"
            ),
            {
                "workflow": workflow_id,
                "payload": json.dumps(payload),
                "key": request.idempotency_key,
                "hash": canonical_hash({"workflow_run_id": workflow_id, "payload": payload}),
            },
        ).scalar_one()
        result = request_details(repo, result_id)
    _log("conversion_request_recorded", result)
    return result


def review_request(tenant: UUID, token: str, request_id: UUID, request: ConversionReviewInput):
    request = ConversionReviewInput.model_validate_json(request.model_dump_json(), strict=True)
    with transaction(tenant, token) as repo:
        repo.require("APPROVER")
        repo.connection.execute(
            text("SELECT attest_conversion_request(:request,CAST(:payload AS jsonb))"),
            {"request": request_id, "payload": request.model_dump_json()},
        ).scalar_one()
        result = request_details(repo, request_id)
    _log("conversion_request_reviewed", result)
    return result


def revoke_request(tenant: UUID, token: str, request_id: UUID, request: ConversionRevokeInput):
    request = ConversionRevokeInput.model_validate_json(request.model_dump_json(), strict=True)
    with transaction(tenant, token) as repo:
        repo.connection.execute(
            text("SELECT revoke_conversion_request(:request,CAST(:payload AS jsonb))"),
            {"request": request_id, "payload": request.model_dump_json()},
        ).scalar_one()
        result = request_details(repo, request_id)
    _log("conversion_request_revoked", result)
    return result


def export_request(tenant: UUID, token: str, request_id: UUID, request: ConversionExportInput):
    request = ConversionExportInput.model_validate_json(request.model_dump_json(), strict=True)
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        export_id = repo.connection.execute(
            text("SELECT export_conversion_request(:request,CAST(:payload AS jsonb),:key,:hash)"),
            {
                "request": request_id,
                "payload": json.dumps(payload),
                "key": request.idempotency_key,
                "hash": canonical_hash({"request_id": request_id, "payload": payload}),
            },
        ).scalar_one()
        result = _checked(repo.one("conversion_exports", id=export_id), HandoffPayload)
    _log("conversion_manual_export_created", result)
    return result


def _log(event, row):
    log.info(
        event,
        extra={
            "tenant_id": str(row["tenant_id"]),
            "workflow_run_id": str(row["workflow_run_id"]),
            "state": row["status"],
            "skill_run_id": str(row["skill_run_id"]) if row.get("skill_run_id") else None,
        },
    )
