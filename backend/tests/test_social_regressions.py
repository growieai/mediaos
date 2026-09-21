"""Adversarial persisted dispatch recovery; provider calls are local test doubles."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by parameter name.

import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from conftest import artifact_data, headers, revise
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_social_integration import (  # noqa: F401
    FakeInstagram,
    authorize,
    execute,
    ready,
    social,
    visual_context,
)

from app.db.repository import canonical_hash, transaction
from app.rendering.seed import seed_visual_config
from app.social import service


def detail(client, data, row):
    response = client.get(f"/v1/social-publishes/{row['id']}", headers=headers(data["a"]))
    assert response.status_code == 200, response.text
    return response.json()


def test_publish_ack_then_disk_failure_holds_without_duplicate(client, social, monkeypatch):
    row, _, rendered, payload = ready(client, social)
    fake = FakeInstagram(social)
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    original = service._receipt

    def unavailable(folder, job, result=None):
        if job["stage"] == "PUBLISH" and result is not None:
            raise OSError("synthetic receipt write failure")
        return original(folder, job, result)

    monkeypatch.setattr(service, "_receipt", unavailable)
    response = execute(client, social, row)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "UNKNOWN_OUTCOME"
    assert detail(client, social, row)["jobs"][-1]["status"] == "UNKNOWN_OUTCOME"
    monkeypatch.setattr(service, "_receipt", original)
    assert execute(client, social, row).json()["status"] == "UNKNOWN_OUTCOME"
    assert fake.calls.count("publish") == 1
    payload["idempotency_key"] = str(uuid4())
    duplicate = client.post(
        f"/v1/renders/{rendered['id']}/social-publishes", headers=headers(social["a"]), json=payload
    )
    assert duplicate.status_code == 409


def test_durable_publish_receipt_recovers_database_finish_outage(client, social, monkeypatch):
    row, _, _, _ = ready(client, social)
    fake = FakeInstagram(social)
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    original = service._finish

    def outage(conn, tenant, token, job, result):
        if job["stage"] == "PUBLISH":
            raise DBAPIError(None, None, RuntimeError("synthetic database outage"))
        return original(conn, tenant, token, job, result)

    monkeypatch.setattr(service, "_finish", outage)
    response = execute(client, social, row)
    assert response.status_code == 503, response.text
    interrupted = detail(client, social, row)
    assert interrupted["status"] == "PUBLISHING"
    assert interrupted["jobs"][-1]["status"] == "RUNNING"
    monkeypatch.setattr(service, "_finish", original)
    recovered = execute(client, social, row)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "PUBLISHED"
    assert recovered.json()["post_id"] == "10003"
    assert fake.calls.count("publish") == 1
    assert all(job["status"] == "SUCCEEDED" for job in recovered.json()["jobs"])


def test_interrupted_insights_read_recovers_with_persisted_cooldown(client, social, monkeypatch):
    row, _, _, _ = ready(client, social)
    fake = FakeInstagram(social)
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    assert execute(client, social, row).json()["status"] == "PUBLISHED"
    original = fake.insights

    class Interrupted(BaseException):
        pass

    def crash(media_id):
        fake.calls.append("insights-interrupted")
        raise Interrupted()

    monkeypatch.setattr(fake, "insights", crash)
    identity = social["a"]
    args = (
        UUID(identity["tenant_id"]),
        identity["tokens"]["OPERATOR"],
        UUID(row["id"]),
        "orphan-read",
    )
    with pytest.raises(Interrupted):
        service.observe(*args)
    assert detail(client, social, row)["jobs"][-1]["status"] == "RUNNING"
    monkeypatch.setattr(fake, "insights", original)
    recovered = service.observe(*args)
    latest = recovered["jobs"][-1]
    assert recovered["status"] == "PUBLISHED"
    assert latest["status"] == "FAILED" and latest["retryable"] and latest["retry_at"]
    assert service.observe(*args)["jobs"][-1]["id"] == latest["id"]
    assert "insights" not in fake.calls
    time.sleep(2.05)
    done = service.observe(*args)
    assert done["status"] == "PUBLISHED" and len(done["insights"]) == 1
    jobs = [job for job in done["jobs"] if job["stage"] == "INSIGHTS"]
    assert [job["status"] for job in jobs] == ["FAILED", "SUCCEEDED"]
    assert [job["attempt"] for job in jobs] == [1, 2]
    assert fake.calls.count("insights") == 1


@pytest.mark.parametrize("change", ["content_revision", "visual_revision", "revoked_connection"])
def test_authorized_publish_rechecks_current_revisions_and_connection(
    client, social, monkeypatch, database, change
):
    row, run, _, _ = ready(client, social)
    fake = FakeInstagram(social)
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    identity = social["a"]
    if change == "content_revision":
        draft = artifact_data(client, identity, run)["content_asset_versions"][0]["payload"]
        updated = revise(client, identity, run, draft)
        assert updated["asset_version_id"] != run["asset_version_id"]
    elif change == "visual_revision":
        seed_visual_config(
            database,
            identity["tenant_id"],
            identity["influencer_id"],
            "New visual policy " + str(uuid4()),
            "This is an AI creator.",
        )
    else:
        response = client.post(
            f"/v1/social/connections/{row['connection_id']}/revoke",
            headers=headers(identity, "ADMIN"),
        )
        assert response.status_code == 200
    response = execute(client, social, row)
    assert response.status_code == 409, response.text
    assert not fake.calls
    assert detail(client, social, row)["status"] != "PUBLISHED"


@pytest.mark.parametrize("actor", ["operator", "approver", "other_connector"])
def test_fake_job_completion_requires_owned_connector_identity(client, social, actor):
    row, _, _, _ = ready(client, social)
    authorize(client, social, row)
    identity = social["a"]
    tenant = UUID(identity["tenant_id"])
    with transaction(tenant, social["tokens"][str(tenant)]) as repo:
        jid = repo.connection.execute(
            text("SELECT social_reserve_job(:id,'CHILD','child:1',:hash)"),
            {"id": row["id"], "hash": canonical_hash({"plan_hash": row["plan_hash"], "index": 1})},
        ).scalar_one()
    if actor == "other_connector":
        caller = social["b"]
        token = social["tokens"][caller["tenant_id"]]
    else:
        caller = identity
        token = caller["tokens"][actor.upper()]
    fake_result = {
        "id": "88888",
        "response_hash": "e" * 64,
        "captured_at": datetime.now(UTC).isoformat(),
    }
    with pytest.raises(DBAPIError):
        with transaction(UUID(caller["tenant_id"]), token) as repo:
            repo.connection.execute(
                text("SELECT social_finish_job(:id,CAST(:p AS jsonb))"),
                {"id": jid, "p": json.dumps(fake_result)},
            )
    current = detail(client, social, row)
    assert current["status"] == "PREPARING"
    assert current["jobs"][-1]["status"] == "RUNNING"
    assert current["jobs"][-1]["result"] is None
