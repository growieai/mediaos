"""Stale-plan cancellation and historical reads across account reconnection."""

# ruff: noqa: F811
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from conftest import artifact_data, headers, revise
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_social_integration import (  # noqa: F401
    authorize,
    ready,
    social,
    visual_context,
)
from test_social_recovery import (  # noqa: F401
    connector,
    fail,
    pending,
    published,
    reserve,
)
from test_social_replies_integration import raw, sql

from app.social import service
from app.social.schemas import ReconcileInput


def reconnect(identity, row, *, scopes=None, api="v25.0", influencer=None, account=None):
    original = raw(identity, "social_connections", row["connection_id"])
    digest = hashlib.sha256(str(uuid4()).encode()).hexdigest()
    sid = sql(
        identity,
        "SELECT social_begin_oauth(:id,:hash)",
        {"id": influencer or original["influencer_id"], "hash": digest},
        "ADMIN",
    )
    sql(identity, "SELECT social_consume_oauth(:hash)", {"hash": digest}, "SOCIAL")
    return sql(
        identity,
        "SELECT social_finish_oauth(:sid,:account,'synthetic_reconnect',:api,CAST(:scopes AS jsonb),:token,:expiry,:vault)",
        {
            "sid": sid,
            "account": account or original["account_id"],
            "api": api,
            "scopes": json.dumps(original["scopes"] if scopes is None else scopes),
            "token": "synthetic-current-credential",
            "expiry": datetime.now(UTC) + timedelta(days=1),
            "vault": "k" * 32,
        },
        "SOCIAL",
    )


def decision(row, kind="REJECT"):
    return {
        "plan_hash": row["plan_hash"],
        "decision": kind,
        "reviewed_images": kind != "REJECT",
        "reviewed_caption": kind != "REJECT",
        "confirmed_account": kind != "REJECT",
        "comment": "Reviewed this exact plan.",
    }


@pytest.mark.parametrize("authorized", [False, True])
@pytest.mark.parametrize("change", ["content", "reconnect", "revoked"])
def test_stale_undispatched_plan_can_be_rejected_without_authorizing_it(
    client, social, authorized, change
):
    row, workflow, _, _ = ready(client, social)
    identity = connector(social)
    if authorized:
        authorize(client, social, row)
    if change == "content":
        payload = artifact_data(client, identity, workflow)["content_asset_versions"][0]["payload"]
        revise(client, identity, workflow, payload)
    elif change == "reconnect":
        reconnect(identity, row)
    else:
        sql(identity, "SELECT social_revoke(:id)", {"id": row["connection_id"]}, "ADMIN")
    path = f"/v1/social-publishes/{row['id']}/review"
    approval = client.post(
        path, headers=headers(identity, "APPROVER"), json=decision(row, "AUTHORIZE_PUBLISH")
    )
    assert approval.status_code == 409
    rejected = client.post(path, headers=headers(identity, "APPROVER"), json=decision(row))
    assert rejected.status_code == 200, rejected.text
    result = rejected.json()
    assert result["status"] == "REJECTED" and not result["jobs"]
    assert [d["decision"] for d in result["decisions"]] == (
        ["AUTHORIZE_PUBLISH", "REJECT"] if authorized else ["REJECT"]
    )
    with pytest.raises(DBAPIError):
        reserve(identity, row, "CHILD", "child:1")


@pytest.mark.parametrize("state", ["PUBLISHING", "UNKNOWN_OUTCOME"])
def test_dispatched_or_uncertain_publication_cannot_be_cancelled(pending, client, state):
    identity, row, job = pending
    if state == "UNKNOWN_OUTCOME":
        fail(identity, job, "UNKNOWN_OUTCOME", False, True, 0)
    response = client.post(
        f"/v1/social-publishes/{row['id']}/review",
        headers=headers(identity, "APPROVER"),
        json=decision(row),
    )
    assert response.status_code == 409
    assert raw(identity, "social_publish_runs", row["id"])["status"] == state


def test_cancellation_still_requires_exact_hash_and_authorized_tenant(client, social):
    row, _, _, _ = ready(client, social)
    path = f"/v1/social-publishes/{row['id']}/review"
    assert client.post(path, headers=headers(social["a"]), json=decision(row)).status_code == 403
    assert (
        client.post(path, headers=headers(social["b"], "APPROVER"), json=decision(row)).status_code
        == 404
    )
    wrong = {**decision(row), "plan_hash": "0" * 64}
    assert (
        client.post(path, headers=headers(social["a"], "APPROVER"), json=wrong).status_code == 409
    )
    assert (
        raw(social["a"], "social_publish_runs", row["id"])["status"] == "AWAITING_PUBLISH_APPROVAL"
    )


def test_historical_insights_pin_current_compatible_credential(published, monkeypatch):
    identity, row, fake = published
    current = reconnect(identity, row)
    secrets_used = []

    def provider(token=None):
        secrets_used.append(token)
        return fake

    monkeypatch.setattr(service, "provider", provider)
    result = service.observe(
        UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], UUID(row["id"]), str(uuid4())
    )
    job = next(j for j in result["jobs"] if j["stage"] == "INSIGHTS")
    assert job["read_connection_id"] == current
    assert str(result["connection_id"]) == row["connection_id"]
    assert result["insights"][0]["payload"]["api_version"] == "v25.0"
    assert secrets_used == ["synthetic-current-credential"] and fake.calls.count("insights") == 1


@pytest.mark.parametrize("incompatible", ["api", "scope", "revoked", "influencer", "other_account"])
def test_historical_reads_reject_incompatible_or_unavailable_current_connection(
    published, database, incompatible
):
    identity, row, fake = published
    changes = {}
    if incompatible == "api":
        changes["api"] = "v26.0"
    elif incompatible == "scope":
        changes["scopes"] = ["instagram_business_basic", "instagram_business_content_publish"]
    elif incompatible == "influencer":
        with database.begin() as conn:
            changes["influencer"] = conn.execute(
                text(
                    "INSERT INTO influencers(tenant_id,slug,name) VALUES(:tenant,:slug,'Another creator') RETURNING id"
                ),
                {"tenant": identity["tenant_id"], "slug": str(uuid4())},
            ).scalar_one()
    elif incompatible == "other_account":
        changes["account"] = str(uuid4().int)[:16]
    current = reconnect(identity, row, **changes)
    if incompatible in ("revoked", "other_account"):
        sql(
            identity,
            "SELECT social_revoke(:id)",
            {"id": current if incompatible == "revoked" else row["connection_id"]},
            "ADMIN",
        )
    with pytest.raises(DBAPIError):
        reserve(identity, row)
    assert "insights" not in fake.calls


def test_read_pin_rejects_reconnection_between_reservation_and_provider(published, monkeypatch):
    identity, row, fake = published
    jobid = reserve(identity, row)
    job = raw(identity, "social_publish_jobs", jobid)
    reconnect(identity, row)
    monkeypatch.setattr(
        service,
        "connected_provider",
        lambda *args: pytest.fail("Stale read pin accessed credentials"),
    )
    with pytest.raises(DBAPIError):
        service.read_job_provider(UUID(identity["tenant_id"]), job)
    assert "insights" not in fake.calls
    with pytest.raises(DBAPIError):
        sql(identity, "SELECT social_read_job_connection(:id)", {"id": jobid}, "OPERATOR")


def test_reconciliation_uses_current_same_account_credential(pending, monkeypatch):
    identity, row, job = pending
    fail(identity, job, "UNKNOWN_OUTCOME", False, True, 0)
    current = reconnect(identity, row)
    calls = []

    def read_media(mid):
        calls.append(mid)
        return SimpleNamespace(
            payload=SimpleNamespace(
                id=mid,
                owner=SimpleNamespace(id=row["account_id"]),
                caption=row["plan"]["caption"],
                media_type="IMAGE",
                timestamp=datetime.now(UTC).isoformat(),
            ),
            response_hash="a" * 64,
            captured_at=datetime.now(UTC),
        )

    provider = SimpleNamespace(settings=SimpleNamespace(api_version="v25.0"), read_media=read_media)
    monkeypatch.setattr(service, "provider", lambda token=None: provider)
    result = service.reconcile(
        UUID(identity["tenant_id"]),
        identity["tokens"]["APPROVER"],
        UUID(row["id"]),
        ReconcileInput(
            candidate_media_id="123456789",
            confirm_external_match=True,
            comment="Synthetic human match.",
        ),
    )
    assert result["status"] == "PUBLISHED" and calls == ["123456789"]
    assert (
        next(j for j in result["jobs"] if j["stage"] == "RECONCILE")["read_connection_id"]
        == current
    )
    assert str(result["connection_id"]) == row["connection_id"]


def test_observed_receipt_recovers_after_credential_revocation(published, monkeypatch):
    identity, row, fake = published
    original = service._finish

    def outage(*args):
        raise DBAPIError(None, None, RuntimeError("Synthetic database outage after receipt"))

    monkeypatch.setattr(service, "_finish", outage)
    args = (
        UUID(identity["tenant_id"]),
        identity["tokens"]["OPERATOR"],
        UUID(row["id"]),
        "durable-observation",
    )
    with pytest.raises(DBAPIError):
        service.observe(*args)
    sql(identity, "SELECT social_revoke(:id)", {"id": row["connection_id"]}, "ADMIN")
    monkeypatch.setattr(service, "_finish", original)
    result = service.observe(*args)
    assert len(result["insights"]) == 1 and fake.calls.count("insights") == 1
    assert result["jobs"][-1]["status"] == "SUCCEEDED"
