"""Persist intent before any network work; no automatic replay of a POST."""

import json
from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text

from app.config import get_settings
from app.conversion.delivery_schemas import (
    BusinessIdentityInput,
    BusinessReviewInput,
    DeliveryAuthorization,
    DeliveryInput,
    RevocationDeliveryInput,
)
from app.conversion.transport import HandoffError, HandoffTransport
from app.db.repository import Repository, canonical_hash, engine, transaction

TABLES = frozenset(
    {
        "business_identities",
        "business_identity_reviews",
        "conversion_transports",
        "conversion_deliveries",
        "conversion_delivery_decisions",
        "conversion_delivery_attempts",
        "conversion_delivery_results",
    }
)


def rows(repo: Repository, name: str, **filters):
    if name not in TABLES or any(
        key not in {"id", "identity_id", "delivery_id", "attempt_id", "request_id"}
        for key in filters
    ):
        raise ValueError("Invalid conversion query")
    predicates = " AND ".join(f"{key}=:{key}" for key in filters)
    query = f"SELECT * FROM {name} WHERE tenant_id=:tenant" + (
        " AND " + predicates if predicates else ""
    )
    return [
        dict(row)
        for row in repo.connection.execute(
            text(query + " ORDER BY created_at,id"), {"tenant": repo.tenant_id, **filters}
        ).mappings()
    ]


def one(repo: Repository, name: str, row_id: UUID):
    found = rows(repo, name, id=row_id)
    if not found:
        raise LookupError("Conversion record not found")
    return found[0]


def business_details(repo: Repository, identity_id: UUID):
    result = one(repo, "business_identities", identity_id)
    result["reviews"] = rows(repo, "business_identity_reviews", identity_id=identity_id)
    superseded = repo.connection.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM business_identities WHERE tenant_id=:tenant AND business_reference=:reference AND revision>:revision)"
        ),
        {
            "tenant": repo.tenant_id,
            "reference": result["business_reference"],
            "revision": result["revision"],
        },
    ).scalar_one()
    result["status"] = (
        "EXPIRED"
        if result["retain_until"] <= datetime.now(UTC)
        else "SUPERSEDED"
        if superseded
        else "BLOCKED_FIXTURE"
        if result["mode"] == "FIXTURE"
        else "OPERATOR_ASSERTION_REVIEWED"
        if result["reviews"]
        else "AWAITING_REVIEW"
    )
    return result


def create_business(tenant: UUID, token: str, data: BusinessIdentityInput):
    payload = data.model_dump(mode="json", exclude={"idempotency_key"})
    with transaction(tenant, token) as repo:
        identity_id = repo.connection.execute(
            text("SELECT register_business_identity(CAST(:payload AS jsonb),:key,:hash)"),
            {
                "payload": json.dumps(payload),
                "key": data.idempotency_key,
                "hash": canonical_hash({"payload": payload}),
            },
        ).scalar_one()
        return business_details(repo, identity_id)


def review_business(tenant: UUID, token: str, identity_id: UUID, data: BusinessReviewInput):
    with transaction(tenant, token) as repo:
        repo.connection.execute(
            text("SELECT review_business_identity(:id,CAST(:payload AS jsonb))"),
            {"id": identity_id, "payload": data.model_dump_json()},
        ).scalar_one()
        return business_details(repo, identity_id)


def details(repo: Repository, delivery_id: UUID):
    result = one(repo, "conversion_deliveries", delivery_id)
    result["transport"] = one(repo, "conversion_transports", result["transport_id"])
    result["decisions"] = rows(repo, "conversion_delivery_decisions", delivery_id=delivery_id)
    result["attempts"] = rows(repo, "conversion_delivery_attempts", delivery_id=delivery_id)
    outcomes = []
    for attempt in result["attempts"]:
        attempt["results"] = rows(repo, "conversion_delivery_results", attempt_id=attempt["id"])
        outcomes.extend(attempt["results"])
    confirmed = next(
        (item["status"] for item in outcomes if item["status"] in {"RECEIVED", "REVOKED"}), None
    )
    result["status"] = confirmed or (
        "UNKNOWN_OUTCOME"
        if result["attempts"]
        else "REJECTED"
        if any(item["decision"] == "REJECT" for item in result["decisions"])
        else "AUTHORIZED"
        if any(
            item["decision"] == "AUTHORIZE" and item["expires_at"] > datetime.now(UTC)
            for item in result["decisions"]
        )
        else "AWAITING_AUTHORIZATION"
    )
    result["audit_completed"] = False
    result["retention_due"] = result["expires_at"] <= datetime.now(UTC)
    return result


def create(tenant: UUID, token: str, request_id: UUID, data: DeliveryInput):
    payload = data.model_dump(mode="json", exclude={"idempotency_key"})
    with transaction(tenant, token) as repo:
        result_id = repo.connection.execute(
            text("SELECT create_conversion_delivery(:id,CAST(:payload AS jsonb),:key,:hash)"),
            {
                "id": request_id,
                "payload": json.dumps(payload),
                "key": data.idempotency_key,
                "hash": canonical_hash({"request_id": request_id, "payload": payload}),
            },
        ).scalar_one()
        return details(repo, result_id)


def authorize(tenant: UUID, token: str, delivery_id: UUID, data: DeliveryAuthorization):
    with transaction(tenant, token) as repo:
        repo.connection.execute(
            text("SELECT authorize_conversion_delivery(:id,CAST(:payload AS jsonb))"),
            {"id": delivery_id, "payload": data.model_dump_json()},
        ).scalar_one()
        return details(repo, delivery_id)


def revoke(tenant: UUID, token: str, delivery_id: UUID, data: RevocationDeliveryInput):
    with transaction(tenant, token) as repo:
        result_id = repo.connection.execute(
            text("SELECT create_conversion_revocation(:id,:key,:hash)"),
            {
                "id": delivery_id,
                "key": data.idempotency_key,
                "hash": canonical_hash({"original_delivery_id": delivery_id}),
            },
        ).scalar_one()
        return details(repo, result_id)


def _transport(row: dict):
    settings = get_settings()
    if not settings.conversion_delivery_enabled:
        raise PermissionError("External handoff is disabled")
    configured = settings.conversion_delivery_credentials
    try:
        credentials = json.loads(configured.get_secret_value()) if configured else {}
        secret = credentials[str(row["transport_id"])]
        if not isinstance(secret, str):
            raise ValueError("Credential required")
        return HandoffTransport(row["transport"]["endpoint"], SecretStr(secret))
    except (ValueError, TypeError, KeyError, HandoffError):
        raise PermissionError("Exact destination credential is not configured") from None


def execute(tenant: UUID, token: str, delivery_id: UUID, *, reconcile=False):
    action = "RECONCILE" if reconcile else "DISPATCH"
    lock_key = f"conversion-execution:{tenant}:{delivery_id}"
    # One physical connection owns this session lock across both committed
    # checkpoints. A concurrent reconciliation cannot mistake the gap between
    # transactions for a process interruption. SQL takes the matching lock before
    # any consent-series lock, including callers which bypass this service.
    with engine().connect() as connection:
        claimed = False
        try:
            with connection.begin():
                repo = Repository(connection, tenant, token)
                repo.require("OPERATOR")
                row = details(repo, delivery_id)
                if row["status"] in {"RECEIVED", "REVOKED"}:
                    return row
                transport = _transport(row)
                claimed = connection.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
                    {"key": lock_key},
                ).scalar_one()
                if not claimed:
                    row["execution_in_progress"] = True
                    return row
                attempt_id = connection.execute(
                    text("SELECT begin_conversion_delivery(:id,:action)"),
                    {"id": delivery_id, "action": action},
                ).scalar_one()
            # The consent-series lock additionally spans the bounded HTTP call.
            # Revocation commits before dispatch and blocks it, or afterwards and
            # requires a separately authorized revocation notice.
            with connection.begin():
                repo = Repository(connection, tenant, token)
                connection.execute(
                    text("SELECT guard_conversion_dispatch(:id)"), {"id": attempt_id}
                )
                try:
                    signed = transport.exchange(row["payload"], reconcile=reconcile)
                    status, receipt, sig = (
                        signed.receipt.status,
                        signed.receipt.model_dump_json(),
                        signed.signature,
                    )
                except HandoffError:
                    status, receipt, sig = "UNKNOWN_OUTCOME", None, None
                connection.execute(
                    text(
                        "SELECT finish_conversion_delivery(:id,:status,CAST(:receipt AS jsonb),:signature)"
                    ),
                    {"id": attempt_id, "status": status, "receipt": receipt, "signature": sig},
                )
                result = details(repo, delivery_id)
            return result
        finally:
            if claimed and not connection.invalidated:
                # Never return a pooled physical connection holding a session
                # lock, even when authentication, validation or commit fails.
                try:
                    connection.rollback()
                    connection.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                        {"key": lock_key},
                    )
                    connection.commit()
                except Exception:
                    connection.invalidate()
                    raise
