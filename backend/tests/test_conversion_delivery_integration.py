"""Signed receipt guards, exact consent, RLS and concurrent revocation. Offline only."""

import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Event, local
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import headers
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_conversion_delivery_unit import SECRET, identity_payload, signed_response
from test_conversion_integration import context as conversion_context  # noqa: F401
from test_conversion_integration import path, reviewed

from app.conversion import delivery
from app.conversion.delivery_schemas import DeliveryInput
from app.conversion.transport import HandoffTransport
from app.db.repository import transaction


@pytest.fixture
def handoff(client, conversion_context, database, monkeypatch):  # noqa: F811
    identity = conversion_context[0]
    request = reviewed(client, conversion_context)
    response = client.post(
        "/v1/business-identities",
        headers=headers(identity),
        json=identity_payload(business_reference=request["business_reference"]),
    )
    assert response.status_code == 200, response.text
    business = response.json()
    response = client.post(
        f"/v1/business-identities/{business['id']}/review",
        headers=headers(identity, "APPROVER"),
        json={
            "content_hash": business["content_hash"],
            "identity_attested": True,
            "comment": "Reviewed exact registry evidence.",
        },
    )
    assert response.status_code == 200, response.text
    with database.begin() as connection:
        transport_id = connection.execute(
            text(
                "SELECT private.provision_conversion_transport(:tenant,:destination,:endpoint,7,:secret)"
            ),
            {
                "tenant": UUID(identity["tenant_id"]),
                "destination": UUID(request["destination_version_id"]),
                "endpoint": "https://example.org/handoff",
                "secret": SECRET.get_secret_value(),
            },
        ).scalar_one()
    settings = SimpleNamespace(
        conversion_delivery_enabled=True,
        conversion_delivery_credentials=SecretStr(
            json.dumps({str(transport_id): SECRET.get_secret_value()})
        ),
    )
    monkeypatch.setattr(delivery, "get_settings", lambda: settings)

    def handler(req):
        payload = json.loads(req.content)["payload"]
        return httpx.Response(
            200,
            json=signed_response(
                payload, "REVOKED" if payload["operation"] == "REVOKE" else "RECEIVED"
            ),
        )

    monkeypatch.setattr(
        delivery,
        "HandoffTransport",
        lambda endpoint, secret: HandoffTransport(
            endpoint, secret, transport=httpx.MockTransport(handler)
        ),
    )
    data = {
        "idempotency_key": str(uuid4()),
        "business_identity_id": business["id"],
        "transport_id": str(transport_id),
        "attestation_id": request["attestations"][0]["id"],
        "request_hash": request["content_hash"],
    }
    response = client.post(path(request, "deliveries"), headers=headers(identity), json=data)
    assert response.status_code == 200, response.text
    return identity, request, business, response.json(), data


def endpoint(row, action=""):
    return f"/v1/conversion-deliveries/{row['id']}" + (f"/{action}" if action else "")


def authorize(client, context):
    identity, _, _, row, _ = context
    response = client.post(
        endpoint(row, "authorize"),
        headers=headers(identity, "APPROVER"),
        json={
            "payload_hash": row["payload_hash"],
            "decision": "AUTHORIZE",
            "comment": "Send this exact handoff to the configured recipient.",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_exact_review_signed_receipt_and_persisted_attempt(client, handoff):
    identity, request, _, row, data = handoff
    assert row["status"] == "AWAITING_AUTHORIZATION"
    assert client.post(endpoint(row, "dispatch"), headers=headers(identity)).status_code == 409
    authorize(client, handoff)
    response = client.post(endpoint(row, "dispatch"), headers=headers(identity))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "RECEIVED" and not result["audit_completed"]
    assert len(result["attempts"]) == 1 and result["attempts"][0]["results"][0]["signature"]
    assert (
        client.post(endpoint(row, "dispatch"), headers=headers(identity)).json()["attempts"]
        == result["attempts"]
    )
    assert (
        client.post(path(request, "deliveries"), headers=headers(identity), json=data).json()["id"]
        == row["id"]
    )
    assert (
        client.post(
            path(request, "deliveries"),
            headers=headers(identity),
            json={**data, "idempotency_key": str(uuid4())},
        ).status_code
        == 409
    )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        attempt = repo.one("skill_runs", id=UUID(result["attempts"][0]["skill_run_id"]))
        assert attempt["status"] == "SUCCEEDED" and attempt["adapter"] == "signed-webhook-v1"
        assert repo.one("cost_events", skill_run_id=attempt["id"])["cost"] == 0
        assert "subject_reference" not in attempt["output"]


def test_roles_cross_tenant_and_forged_receipt(client, handoff, identities):
    identity, _, _, row, _ = handoff
    payload = {
        "payload_hash": row["payload_hash"],
        "decision": "AUTHORIZE",
        "comment": "Exact review",
    }
    assert (
        client.post(endpoint(row, "authorize"), headers=headers(identity), json=payload).status_code
        == 403
    )
    assert client.get(endpoint(row), headers=headers(identities[1])).status_code == 404
    assert (
        client.post(
            endpoint(row, "authorize"), headers=headers(identities[1], "APPROVER"), json=payload
        ).status_code
        == 404
    )
    authorize(client, handoff)
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        attempt_id = repo.connection.execute(
            text("SELECT begin_conversion_delivery(:id,'DISPATCH')"), {"id": row["id"]}
        ).scalar_one()
    forged = signed_response(row["payload"])
    with pytest.raises(DBAPIError):
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text(
                    "SELECT finish_conversion_delivery(:id,'RECEIVED',CAST(:payload AS jsonb),:signature)"
                ),
                {"id": attempt_id, "payload": json.dumps(forged["receipt"]), "signature": "0" * 64},
            )
    with pytest.raises(DBAPIError):
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["ADMIN"]) as repo:
            repo.connection.execute(text("SELECT * FROM private.conversion_transport_keys"))


def test_revocation_blocks_send_and_requires_separate_revocation_authorization(client, handoff):
    identity, request, _, row, _ = handoff
    authorize(client, handoff)
    assert (
        client.post(endpoint(row, "dispatch"), headers=headers(identity)).json()["status"]
        == "RECEIVED"
    )
    assert (
        client.post(
            path(request, "revoke"),
            headers=headers(identity),
            json={"reason": "Subject withdrew consent."},
        ).status_code
        == 200
    )
    response = client.post(
        endpoint(row, "revocation"),
        headers=headers(identity),
        json={"idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    revoke = response.json()
    assert (
        revoke["payload"]["operation"] == "REVOKE" and "subject_reference" not in revoke["payload"]
    )
    assert client.post(endpoint(revoke, "dispatch"), headers=headers(identity)).status_code == 409
    response = client.post(
        endpoint(revoke, "authorize"),
        headers=headers(identity, "APPROVER"),
        json={
            "payload_hash": revoke["payload_hash"],
            "decision": "AUTHORIZE",
            "comment": "Request deletion of this exact earlier handoff.",
        },
    )
    assert response.status_code == 200, response.text
    assert (
        client.post(endpoint(revoke, "dispatch"), headers=headers(identity)).json()["status"]
        == "REVOKED"
    )


def test_revocation_before_dispatch_wins(client, handoff):
    identity, request, _, row, _ = handoff
    authorize(client, handoff)
    assert (
        client.post(
            path(request, "revoke"),
            headers=headers(identity),
            json={"reason": "Withdrawn before send"},
        ).status_code
        == 200
    )
    assert client.post(endpoint(row, "dispatch"), headers=headers(identity)).status_code == 409
    assert client.get(endpoint(row), headers=headers(identity)).json()["attempts"] == []


def test_unknown_post_is_not_retried_and_signed_get_can_recover(client, handoff, monkeypatch):
    identity, _, _, row, _ = handoff
    authorize(client, handoff)
    calls = []

    def handler(req):
        calls.append(req.method)
        if req.method == "POST":
            raise httpx.ReadTimeout("private unknown outcome")
        return httpx.Response(200, json=signed_response(row["payload"]))

    monkeypatch.setattr(
        delivery,
        "HandoffTransport",
        lambda endpoint, secret: HandoffTransport(
            endpoint, secret, transport=httpx.MockTransport(handler)
        ),
    )
    assert (
        client.post(endpoint(row, "dispatch"), headers=headers(identity)).json()["status"]
        == "UNKNOWN_OUTCOME"
    )
    assert client.post(endpoint(row, "dispatch"), headers=headers(identity)).status_code == 409
    assert (
        client.post(endpoint(row, "reconcile"), headers=headers(identity)).json()["status"]
        == "RECEIVED"
    )
    assert calls == ["POST", "GET"]


def test_missing_flag_or_credentials_consumes_no_attempt(client, handoff, monkeypatch):
    identity, _, _, row, _ = handoff
    authorize(client, handoff)
    for settings in [
        SimpleNamespace(conversion_delivery_enabled=False),
        SimpleNamespace(conversion_delivery_enabled=True, conversion_delivery_credentials=None),
    ]:
        monkeypatch.setattr(delivery, "get_settings", lambda value=settings: value)
        assert client.post(endpoint(row, "dispatch"), headers=headers(identity)).status_code == 403
    assert client.get(endpoint(row), headers=headers(identity)).json()["attempts"] == []


def test_stale_business_identity_invalidates_prepared_handoff(client, handoff):
    identity, request, _, row, _ = handoff
    authorize(client, handoff)
    assert (
        client.post(
            "/v1/business-identities",
            headers=headers(identity),
            json=identity_payload(business_reference=request["business_reference"]),
        ).status_code
        == 200
    )
    assert client.post(endpoint(row, "dispatch"), headers=headers(identity)).status_code == 409


def test_concurrent_same_key_returns_one_intent(client, handoff):
    identity, request, _, row, data = handoff

    def worker():
        return delivery.create(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(request["id"]),
            DeliveryInput.model_validate_json(json.dumps(data), strict=True),
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: worker(), range(3)))
    assert {str(result["id"]) for result in results} == {row["id"]}


def test_dispatch_serializes_with_consent_revocation(client, handoff, monkeypatch):
    identity, request, _, row, _ = handoff
    authorize(client, handoff)
    entered, release = Event(), Event()

    def handler(req):
        entered.set()
        assert release.wait(10)
        return httpx.Response(200, json=signed_response(row["payload"]))

    monkeypatch.setattr(
        delivery,
        "HandoffTransport",
        lambda endpoint, secret: HandoffTransport(
            endpoint, secret, transport=httpx.MockTransport(handler)
        ),
    )

    def revoke():
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            return repo.connection.execute(
                text("SELECT revoke_conversion_request(:id,CAST(:payload AS jsonb))"),
                {"id": request["id"], "payload": json.dumps({"reason": "Concurrent withdrawal"})},
            ).scalar_one()

    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatch = pool.submit(
            delivery.execute,
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(row["id"]),
        )
        assert entered.wait(10)
        revocation = pool.submit(revoke)
        with pytest.raises(FutureTimeoutError):
            revocation.result(timeout=0.1)
        release.set()
        assert dispatch.result(timeout=10)["status"] == "RECEIVED"
        assert revocation.result(timeout=10)


@pytest.mark.parametrize("table", sorted(delivery.TABLES))
def test_no_direct_runtime_mutation(client, handoff, table):
    with pytest.raises(DBAPIError):
        with transaction(UUID(handoff[0]["tenant_id"]), handoff[0]["tokens"]["ADMIN"]) as repo:
            repo.connection.execute(text(f"DELETE FROM {table}"))


def test_fixture_identity_cannot_be_reviewed_or_promoted(client, identities):
    identity = identities[0]
    body = identity_payload(business_reference=str(uuid4()), mode="FIXTURE")
    result = client.post("/v1/business-identities", headers=headers(identity), json=body)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert (
        client.post(
            f"/v1/business-identities/{saved['id']}/review",
            headers=headers(identity, "APPROVER"),
            json={
                "content_hash": saved["content_hash"],
                "identity_attested": True,
                "comment": "Cannot upgrade synthetic data",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/business-identities",
            headers=headers(identity),
            json={**body, "idempotency_key": str(uuid4()), "mode": "MANUAL"},
        ).status_code
        == 409
    )


def test_cross_tenant_identity_and_transport_references_rejected(client, handoff, identities):
    identity, request, _, _, data = handoff
    other = identities[1]
    response = client.post(
        "/v1/business-identities",
        headers=headers(other),
        json=identity_payload(business_reference=request["business_reference"]),
    )
    assert response.status_code == 200, response.text
    forged = {
        **data,
        "idempotency_key": str(uuid4()),
        "business_identity_id": response.json()["id"],
    }
    assert (
        client.post(path(request, "deliveries"), headers=headers(identity), json=forged).status_code
        == 404
    )
    assert client.get("/v1/conversion-transports", headers=headers(other)).json() == []


def test_wrong_exact_hashes_and_same_key_changed_payload_conflict(client, handoff):
    identity, request, _, row, data = handoff
    assert (
        client.post(
            endpoint(row, "authorize"),
            headers=headers(identity, "APPROVER"),
            json={
                "payload_hash": "0" * 64,
                "decision": "AUTHORIZE",
                "comment": "Wrong exact payload",
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            path(request, "deliveries"),
            headers=headers(identity),
            json={**data, "request_hash": "0" * 64},
        ).status_code
        == 409
    )


def test_reject_undispatched_stale_intent_allows_new_review(client, handoff):
    identity, request, _, row, data = handoff
    authorize(client, handoff)
    response = client.post(
        endpoint(row, "authorize"),
        headers=headers(identity, "APPROVER"),
        json={
            "payload_hash": row["payload_hash"],
            "decision": "REJECT",
            "comment": "Withdraw this intent before network work",
        },
    )
    assert response.status_code == 200 and response.json()["status"] == "REJECTED"
    assert client.post(endpoint(row, "dispatch"), headers=headers(identity)).status_code == 409
    result = client.post(
        path(request, "deliveries"),
        headers=headers(identity),
        json={**data, "idempotency_key": str(uuid4())},
    )
    assert result.status_code == 200 and result.json()["status"] == "AWAITING_AUTHORIZATION"
    assert result.json()["id"] != row["id"]


def test_orphan_attempt_is_checkpointed_before_signed_reconciliation(client, handoff, monkeypatch):
    identity, _, _, row, _ = handoff
    authorize(client, handoff)
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        repo.connection.execute(
            text("SELECT begin_conversion_delivery(:id,'DISPATCH')"), {"id": row["id"]}
        )

    def handler(req):
        assert req.method == "GET"
        return httpx.Response(200, json=signed_response(row["payload"]))

    monkeypatch.setattr(
        delivery,
        "HandoffTransport",
        lambda endpoint, secret: HandoffTransport(
            endpoint, secret, transport=httpx.MockTransport(handler)
        ),
    )
    response = client.post(endpoint(row, "reconcile"), headers=headers(identity))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "RECEIVED"
    assert result["attempts"][0]["results"][0]["status"] == "UNKNOWN_OUTCOME"
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        first = repo.one("skill_runs", id=UUID(result["attempts"][0]["skill_run_id"]))
        assert first["status"] == "FAILED" and first["error_category"] == "UNKNOWN_OUTCOME"


def test_runtime_admin_cannot_provision_destination_signing_key(client, handoff):
    identity, request, _, _, _ = handoff
    with pytest.raises(DBAPIError):
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["ADMIN"]) as repo:
            repo.connection.execute(
                text(
                    "SELECT private.provision_conversion_transport(:tenant,:destination,'https://example.org/forged',7,:secret)"
                ),
                {
                    "tenant": UUID(identity["tenant_id"]),
                    "destination": UUID(request["destination_version_id"]),
                    "secret": SECRET.get_secret_value(),
                },
            )


@pytest.mark.parametrize("competitor", ["service", "runtime_sql"])
def test_reconcile_cannot_steal_dispatch_between_committed_checkpoints(
    client, handoff, monkeypatch, competitor
):
    identity, _, _, row, _ = handoff
    authorize(client, handoff)
    checkpoint_saved, continue_dispatch = Event(), Event()
    worker_state = local()
    original_repository = delivery.Repository
    methods = []

    def repository(connection, tenant, token):
        repo = original_repository(connection, tenant, token)
        if getattr(worker_state, "dispatch", False):
            worker_state.repositories = getattr(worker_state, "repositories", 0) + 1
            if worker_state.repositories == 2:
                # First transaction is committed; the HTTP guard transaction has
                # authenticated but has not acquired its consent-series lock.
                checkpoint_saved.set()
                assert continue_dispatch.wait(10)
        return repo

    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json=signed_response(row["payload"]))

    monkeypatch.setattr(delivery, "Repository", repository)
    monkeypatch.setattr(
        delivery,
        "HandoffTransport",
        lambda endpoint, secret: HandoffTransport(
            endpoint, secret, transport=httpx.MockTransport(handler)
        ),
    )

    def dispatch():
        worker_state.dispatch = True
        return delivery.execute(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], UUID(row["id"])
        )

    def sql_reconcile():
        try:
            with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
                repo.connection.execute(
                    text("SELECT begin_conversion_delivery(:id,'RECONCILE')"), {"id": row["id"]}
                )
        except DBAPIError as error:
            return error.orig.sqlstate
        return "unexpected_reconcile"

    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(dispatch)
        try:
            assert checkpoint_saved.wait(10)
            if competitor == "service":
                busy = delivery.execute(
                    UUID(identity["tenant_id"]),
                    identity["tokens"]["OPERATOR"],
                    UUID(row["id"]),
                    reconcile=True,
                )
                assert busy["execution_in_progress"] is True
                assert len(busy["attempts"]) == 1 and busy["attempts"][0]["results"] == []
            else:
                competing = pool.submit(sql_reconcile)
                with pytest.raises(FutureTimeoutError):
                    competing.result(timeout=0.15)
            continue_dispatch.set()
            assert active.result(timeout=10)["status"] == "RECEIVED"
            if competitor == "runtime_sql":
                assert competing.result(timeout=10) == "23514"
        finally:
            continue_dispatch.set()
    result = client.get(endpoint(row), headers=headers(identity)).json()
    assert result["status"] == "RECEIVED" and len(result["attempts"]) == 1
    assert result["attempts"][0]["results"][0]["status"] == "RECEIVED"
    assert methods == ["POST"]


def test_checkpoint_interruption_releases_session_lock_for_reconciliation(
    client, handoff, monkeypatch
):
    identity, _, _, row, _ = handoff
    authorize(client, handoff)
    original_repository = delivery.Repository
    calls = 0

    def interrupted_repository(connection, tenant, token):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Simulated process interruption after durable intent")
        return original_repository(connection, tenant, token)

    monkeypatch.setattr(delivery, "Repository", interrupted_repository)
    with pytest.raises(RuntimeError, match="Simulated process interruption"):
        delivery.execute(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], UUID(row["id"])
        )
    monkeypatch.setattr(delivery, "Repository", original_repository)
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json=signed_response(row["payload"]))

    monkeypatch.setattr(
        delivery,
        "HandoffTransport",
        lambda endpoint, secret: HandoffTransport(
            endpoint, secret, transport=httpx.MockTransport(handler)
        ),
    )
    result = delivery.execute(
        UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], UUID(row["id"]), reconcile=True
    )
    assert result["status"] == "RECEIVED" and len(result["attempts"]) == 2
    assert result["attempts"][0]["results"][0]["status"] == "UNKNOWN_OUTCOME"
    assert result["attempts"][1]["results"][0]["status"] == "RECEIVED"
    assert methods == ["GET"]
