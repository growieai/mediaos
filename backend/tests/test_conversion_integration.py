"""Consent review, exact manual export, tenant isolation and concurrency."""

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import artifact_data, create, headers, revise
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_conversion_unit import destination_payload, request_payload
from test_visual_workflow import approve_content

from app.conversion import service
from app.conversion.schemas import (
    ConversionExportInput,
    ConversionRequestInput,
    ExportReceiptPayload,
)
from app.db.repository import canonical_hash, transaction


def path(request, action=""):
    return f"/v1/conversion-requests/{request['id']}" + (f"/{action}" if action else "")


def destination(client, identity, **changes):
    response = client.post(
        "/v1/conversion-destinations",
        headers=headers(identity, "ADMIN"),
        json=destination_payload(**changes),
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def context(client, identities):
    identity = identities[0]
    run = create(client, identity)
    approval = approve_content(client, identity, run)
    target = destination(client, identity)
    payload = request_payload(approval["approval_record_id"], target["id"])
    return identity, run, target, payload


def capture(client, context, **changes):
    identity, run, _, payload = context
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/conversion-requests",
        headers=headers(identity),
        json={**payload, **changes},
    )
    assert response.status_code == 200, response.text
    return response.json()


def review_payload(request, **changes):
    return {
        "request_hash": request["content_hash"],
        "destination_hash": request["destination_hash"],
        "consent_hash": request["consent_hash"],
        "consent_attested": True,
        "comment": "Checked the exact separately recorded explicit consent evidence.",
        **changes,
    }


def reviewed(client, context, request=None):
    request = request or capture(client, context)
    response = client.post(
        path(request, "review"),
        headers=headers(context[0], "APPROVER"),
        json=review_payload(request),
    )
    assert response.status_code == 200, response.text
    return response.json()


def export_payload(request, key=None, **changes):
    return {
        "idempotency_key": key or str(uuid4()),
        "request_hash": request["content_hash"],
        "destination_hash": request["destination_hash"],
        "attestation_id": request["attestations"][0]["id"]
        if request["attestations"]
        else str(uuid4()),
        **changes,
    }


def test_manual_consent_review_export_preserves_history_without_delivery(
    client, context, monkeypatch
):
    def deny(*args, **kwargs):
        raise AssertionError("Manual handoff must not contact any service")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    request = capture(client, context)
    assert (
        request["status"] == "AWAITING_REVIEW"
        and request["consent_provenance"] == "OPERATOR_ASSERTION"
    )
    request = reviewed(client, context, request)
    assert request["status"] == "REVIEWED_REQUEST"
    response = client.post(
        path(request, "export"), headers=headers(context[0]), json=export_payload(request)
    )
    assert response.status_code == 200, response.text
    exported = response.json()
    assert exported["status"] == "EXPORTED_FOR_MANUAL_HANDOFF"
    assert exported["payload"]["consent_provenance"] == "OPERATOR_ASSERTION_REVIEWED"
    assert not exported["payload"]["delivered"] and not exported["payload"]["network_performed"]
    assert not exported["payload"]["audit_completed"]
    assert "statement" not in exported["payload"] and "email" not in exported["payload"]
    assert canonical_hash(exported["payload"]) == exported["content_hash"]
    detail = client.get(path(request), headers=headers(context[0])).json()
    assert detail["status"] == "EXPORTED_FOR_MANUAL_HANDOFF"
    assert "payload" not in detail["exports"][0]
    with transaction(UUID(context[0]["tenant_id"]), context[0]["tokens"]["OPERATOR"]) as repo:
        attempt = repo.one("skill_runs", id=UUID(exported["skill_run_id"]))
        assert attempt["status"] == "SUCCEEDED" and attempt["cost"] == 0
        assert attempt["skill_identifier"] == "conversion.export"
        receipt = ExportReceiptPayload.model_validate_json(
            json.dumps(attempt["output"]), strict=True
        )
        assert str(receipt.export_id) == exported["id"]
        assert receipt.content_hash == exported["content_hash"]
        assert repo.one("cost_events", skill_run_id=attempt["id"])["cost"] == 0
        assert repo.one("workflow_runs", id=UUID(context[1]["id"]))["state"] == "APPROVED"
        audit = repo.all("audit_events", workflow_run_id=UUID(context[1]["id"]))
        assert {r["event_type"] for r in audit} >= {
            "CONVERSION_REQUEST_RECORDED",
            "CONVERSION_CONSENT_ATTESTED",
            "CONVERSION_MANUAL_EXPORT_CREATED",
        }


def test_roles_and_missing_review_fail_closed(client, context):
    request = capture(client, context)
    assert (
        client.post(
            path(request, "review"), headers=headers(context[0]), json=review_payload(request)
        ).status_code
        == 403
    )
    assert (
        client.post(
            path(request, "export"), headers=headers(context[0]), json=export_payload(request)
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/conversion-destinations", headers=headers(context[0]), json=destination_payload()
        ).status_code
        == 403
    )
    assert client.get(path(request)).status_code == 401


@pytest.mark.parametrize("field", ["request_hash", "destination_hash", "consent_hash"])
def test_review_rejects_changed_hashes(client, context, field):
    request = capture(client, context)
    response = client.post(
        path(request, "review"),
        headers=headers(context[0], "APPROVER"),
        json=review_payload(request, **{field: "0" * 64}),
    )
    assert response.status_code == 409


def test_fixture_cannot_be_attested_exported_or_relabelled(client, context):
    request = capture(client, context, mode="FIXTURE")
    assert request["status"] == "BLOCKED_FIXTURE"
    assert (
        client.post(
            path(request, "review"),
            headers=headers(context[0], "APPROVER"),
            json=review_payload(request),
        ).status_code
        == 409
    )
    assert (
        client.post(
            path(request, "export"), headers=headers(context[0]), json=export_payload(request)
        ).status_code
        == 409
    )
    data = {**context[3], "idempotency_key": str(uuid4()), "mode": "MANUAL"}
    assert (
        client.post(
            f"/v1/workflow-runs/{context[1]['id']}/conversion-requests",
            headers=headers(context[0]),
            json=data,
        ).status_code
        == 409
    )


def test_expired_and_future_consent_fail_closed(client, context):
    consent = {
        **context[3]["consent"],
        "captured_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }
    request = capture(client, context, consent=consent)
    assert request["status"] == "EXPIRED"
    assert (
        client.post(
            path(request, "review"),
            headers=headers(context[0], "APPROVER"),
            json=review_payload(request),
        ).status_code
        == 409
    )
    future = {
        **context[3],
        "request_reference": str(uuid4()),
        "idempotency_key": str(uuid4()),
        "consent": {
            **consent,
            "captured_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    }
    assert (
        client.post(
            f"/v1/workflow-runs/{context[1]['id']}/conversion-requests",
            headers=headers(context[0]),
            json=future,
        ).status_code
        == 409
    )


def test_new_request_revision_requires_fresh_attestation(client, context):
    old = reviewed(client, context)
    new = capture(
        client,
        context,
        idempotency_key=str(uuid4()),
        consent={
            **context[3]["consent"],
            "statement": "An updated explicit request, separately reviewed.",
        },
    )
    assert new["revision"] == old["revision"] + 1 and not new["attestations"]
    assert (
        client.post(
            path(old, "export"), headers=headers(context[0]), json=export_payload(old)
        ).status_code
        == 409
    )
    assert (
        client.post(
            path(new, "export"), headers=headers(context[0]), json=export_payload(old)
        ).status_code
        == 409
    )
    assert reviewed(client, context, new)["status"] == "REVIEWED_REQUEST"


def test_destination_revision_invalidates_old_request_export(client, context):
    request = reviewed(client, context)
    destination(client, context[0], destination_key=context[2]["destination_key"], enabled=False)
    assert (
        client.post(
            path(request, "export"), headers=headers(context[0]), json=export_payload(request)
        ).status_code
        == 409
    )
    assert client.get(path(request), headers=headers(context[0])).json()["status"] == "SUPERSEDED"


def test_consent_expiring_after_review_cannot_export(client, context):
    expires = datetime.now(UTC) + timedelta(seconds=2)
    request = capture(
        client, context, consent={**context[3]["consent"], "expires_at": expires.isoformat()}
    )
    request = reviewed(client, context, request)
    time.sleep(max(0, (expires - datetime.now(UTC)).total_seconds()) + 0.02)
    response = client.post(
        path(request, "export"), headers=headers(context[0]), json=export_payload(request)
    )
    assert response.status_code == 409


def test_same_idempotency_key_is_independent_between_tenants(client, context, identities):
    first = capture(client, context)
    other = identities[1]
    run = create(client, other)
    approval = approve_content(client, other, run)
    target = destination(client, other)
    payload = request_payload(
        approval["approval_record_id"], target["id"], idempotency_key=context[3]["idempotency_key"]
    )
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/conversion-requests", headers=headers(other), json=payload
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] != first["id"]


def test_foreign_approval_and_fixture_community_event_cannot_authorize_manual_request(
    client, context, identities
):
    other_run = create(client, identities[1])
    foreign = approve_content(client, identities[1], other_run)
    response = client.post(
        f"/v1/workflow-runs/{context[1]['id']}/conversion-requests",
        headers=headers(context[0]),
        json={**context[3], "approval_record_id": foreign["approval_record_id"]},
    )
    assert response.status_code == 404
    event = client.post(
        f"/v1/workflow-runs/{context[1]['id']}/community-events",
        headers=headers(context[0]),
        json={
            "schema_version": 1,
            "idempotency_key": str(uuid4()),
            "mode": "FIXTURE",
            "origin": "test:fixture",
            "participant_reference": "subject-1",
            "comment_text": "Please contact me",
            "captured_at": datetime.now(UTC).isoformat(),
        },
    )
    assert event.status_code == 200, event.text
    response = client.post(
        f"/v1/workflow-runs/{context[1]['id']}/conversion-requests",
        headers=headers(context[0]),
        json={**context[3], "community_event_id": event.json()["id"]},
    )
    assert response.status_code == 404


def test_revocation_blocks_export_replays_and_all_future_series_revisions(client, context):
    request = reviewed(client, context)
    payload = export_payload(request)
    assert (
        client.post(path(request, "export"), headers=headers(context[0]), json=payload).status_code
        == 200
    )
    result = client.post(
        path(request, "revoke"),
        headers=headers(context[0]),
        json={"reason": "Subject withdrew the request."},
    )
    assert result.status_code == 200 and result.json()["status"] == "REVOKED"
    assert (
        client.post(path(request, "export"), headers=headers(context[0]), json=payload).status_code
        == 409
    )
    assert (
        client.post(
            path(request, "review"),
            headers=headers(context[0], "APPROVER"),
            json=review_payload(request),
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/v1/workflow-runs/{context[1]['id']}/conversion-requests",
            headers=headers(context[0]),
            json={**context[3], "idempotency_key": str(uuid4())},
        ).status_code
        == 409
    )


def test_cross_tenant_retrieval_reference_review_export_and_revocation(client, context, identities):
    request = reviewed(client, context)
    other = identities[1]
    assert client.get(path(request), headers=headers(other)).status_code == 404
    assert (
        client.post(
            path(request, "review"),
            headers=headers(other, "APPROVER"),
            json=review_payload(request),
        ).status_code
        == 404
    )
    assert (
        client.post(
            path(request, "export"), headers=headers(other), json=export_payload(request)
        ).status_code
        == 404
    )
    assert (
        client.post(
            path(request, "revoke"), headers=headers(other), json={"reason": "Wrong tenant"}
        ).status_code
        == 404
    )
    foreign_destination = destination(client, other)
    data = {
        **context[3],
        "idempotency_key": str(uuid4()),
        "request_reference": str(uuid4()),
        "destination_version_id": foreign_destination["id"],
    }
    assert (
        client.post(
            f"/v1/workflow-runs/{context[1]['id']}/conversion-requests",
            headers=headers(context[0]),
            json=data,
        ).status_code
        == 404
    )
    with transaction(UUID(other["tenant_id"]), other["tokens"]["OPERATOR"]) as repo:
        assert (
            repo.connection.execute(
                text("SELECT count(*) FROM conversion_requests WHERE id=:id"), {"id": request["id"]}
            ).scalar_one()
            == 0
        )


@pytest.mark.parametrize(
    "table",
    [
        "conversion_destinations",
        "conversion_requests",
        "conversion_attestations",
        "conversion_revocations",
        "conversion_exports",
    ],
)
def test_runtime_cannot_write_conversion_tables(client, context, table):
    capture(client, context)
    with pytest.raises(DBAPIError):
        with transaction(UUID(context[0]["tenant_id"]), context[0]["tokens"]["ADMIN"]) as repo:
            repo.connection.execute(text(f"DELETE FROM {table}"))


def test_historical_content_revision_keeps_attribution_without_consent_transfer(client, context):
    request = capture(client, context)
    data = artifact_data(client, context[0], context[1])["content_asset_versions"][0]["payload"]
    revise(client, context[0], context[1], data)
    # Handoff carries no content claims; historical attribution remains exact.
    result = reviewed(client, context, request)
    assert result["approval_record_id"] == context[3]["approval_record_id"]
    assert (
        client.post(
            path(result, "export"), headers=headers(context[0]), json=export_payload(result)
        ).status_code
        == 200
    )


def test_request_idempotency_same_different_and_concurrent(client, context):
    identity, run, _, payload = context

    def worker():
        return service.create_request(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(run["id"]),
            ConversionRequestInput.model_validate_json(json.dumps(payload)),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: worker(), range(4)))
    assert len({row["id"] for row in rows}) == 1
    assert capture(client, context)["id"] == str(rows[0]["id"])
    changed = {**payload, "subject_reference": "different-subject"}
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/conversion-requests",
            headers=headers(identity),
            json=changed,
        ).status_code
        == 409
    )


def test_export_replay_has_one_attempt_and_one_receipt(client, context):
    request = reviewed(client, context)
    payload = export_payload(request)

    def worker():
        return service.export_request(
            UUID(context[0]["tenant_id"]),
            context[0]["tokens"]["OPERATOR"],
            UUID(request["id"]),
            ConversionExportInput.model_validate_json(json.dumps(payload)),
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: worker(), range(3)))
    assert len({row["id"] for row in rows}) == 1
    with transaction(UUID(context[0]["tenant_id"]), context[0]["tokens"]["OPERATOR"]) as repo:
        assert len(repo.all("conversion_exports", request_id=UUID(request["id"]))) == 1
        assert len(repo.all("cost_events", skill_run_id=rows[0]["skill_run_id"])) == 1


def test_sql_rejects_forged_consent_shape_even_with_correct_hash(client, context):
    data = copy.deepcopy(context[3])
    key = data.pop("idempotency_key")
    data["consent"]["inferred_by_model"] = True
    with pytest.raises(DBAPIError):
        with transaction(UUID(context[0]["tenant_id"]), context[0]["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("SELECT register_conversion_request(:run,CAST(:payload AS jsonb),:key,:hash)"),
                {
                    "run": context[1]["id"],
                    "payload": json.dumps(data),
                    "key": key,
                    "hash": canonical_hash({"workflow_run_id": context[1]["id"], "payload": data}),
                },
            )


def test_revocation_serializes_with_export(client, context):
    request = reviewed(client, context)
    ready, release = Event(), Event()
    identity = context[0]

    def revoke_first():
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("SELECT revoke_conversion_request(:id,CAST(:payload AS jsonb))"),
                {"id": request["id"], "payload": json.dumps({"reason": "Explicit withdrawal"})},
            )
            ready.set()
            assert release.wait(10)

    def export_second():
        try:
            service.export_request(
                UUID(identity["tenant_id"]),
                identity["tokens"]["OPERATOR"],
                UUID(request["id"]),
                ConversionExportInput.model_validate_json(json.dumps(export_payload(request))),
            )
        except DBAPIError as error:
            return error.orig.sqlstate
        return "unexpected_success"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(revoke_first)
        assert ready.wait(10)
        second = pool.submit(export_second)
        with pytest.raises(FutureTimeoutError):
            second.result(timeout=0.15)
        release.set()
        first.result(timeout=10)
        assert second.result(timeout=10) == "23514"


@pytest.mark.parametrize("invalidated_by", ["revocation", "expiry", "destination_revision"])
def test_history_never_exposes_guarded_handoff_after_consent_or_destination_changes(
    client, context, invalidated_by
):
    expires = datetime.now(UTC) + timedelta(seconds=3)
    request = capture(
        client,
        context,
        **(
            {"consent": {**context[3]["consent"], "expires_at": expires.isoformat()}}
            if invalidated_by == "expiry"
            else {}
        ),
    )
    request = reviewed(client, context, request)
    export_data = export_payload(request)
    response = client.post(path(request, "export"), headers=headers(context[0]), json=export_data)
    assert response.status_code == 200, response.text
    exported = response.json()
    assert exported["payload"]["subject_reference"] == context[3]["subject_reference"]
    if invalidated_by == "revocation":
        assert (
            client.post(
                path(request, "revoke"),
                headers=headers(context[0]),
                json={"reason": "Participant withdrew consent after export."},
            ).status_code
            == 200
        )
    elif invalidated_by == "expiry":
        time.sleep(max(0, (expires - datetime.now(UTC)).total_seconds()) + 0.02)
    else:
        destination(
            client, context[0], destination_key=context[2]["destination_key"], enabled=False
        )
    assert (
        client.post(
            path(request, "export"), headers=headers(context[0]), json=export_data
        ).status_code
        == 409
    )
    assert (
        client.post(
            path(request, "export"), headers=headers(context[0], "APPROVER"), json=export_data
        ).status_code
        == 403
    )

    detail = client.get(path(request), headers=headers(context[0], "APPROVER")).json()
    listed = client.get(
        f"/v1/workflow-runs/{context[1]['id']}/conversion-requests",
        headers=headers(context[0], "APPROVER"),
    ).json()
    for saved in (detail, next(row for row in listed if row["id"] == request["id"])):
        receipt = saved["exports"][0]
        assert set(receipt) == set(service.RECEIPT_FIELDS)
        assert receipt["content_hash"] == exported["content_hash"]
        assert receipt["request_id"] == request["id"]
        assert (
            "payload" not in receipt
            and "subject_reference" not in receipt
            and "consent" not in receipt
        )

    artifacts = client.get(
        f"/v1/workflow-runs/{context[1]['id']}/artifacts", headers=headers(context[0], "APPROVER")
    ).json()
    attempt = next(row for row in artifacts["skill_runs"] if row["id"] == exported["skill_run_id"])
    telemetry = ExportReceiptPayload.model_validate_json(json.dumps(attempt["output"]), strict=True)
    assert (
        str(telemetry.request_id) == request["id"]
        and telemetry.content_hash == exported["content_hash"]
    )
    with transaction(UUID(context[0]["tenant_id"]), context[0]["tokens"]["OPERATOR"]) as repo:
        raw = repo.one("skill_runs", id=UUID(exported["skill_run_id"]))["output"]
        assert raw == attempt["output"]
