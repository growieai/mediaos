"""Uncertain posts need a recorded human match and a recorded provider read."""

# ruff: noqa: F811
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from conftest import headers
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
from app.social import service
from app.social_provider import SocialProviderError


def uncertain(client, data, monkeypatch):
    row, _, _, _ = ready(client, data)
    fake = FakeInstagram(data, "unknown")
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, data, row)
    assert execute(client, data, row).json()["status"] == "UNKNOWN_OUTCOME"
    return row, fake


def request():
    return {
        "candidate_media_id": "76543",
        "confirm_external_match": True,
        "comment": "Explicit test-only human match to the external account post.",
    }


@pytest.mark.parametrize("case", ["match", "wrong_account", "wrong_caption", "unavailable"])
def test_reconciliation_records_assertion_and_attempt_before_read(
    client, social, monkeypatch, case
):
    row, fake = uncertain(client, social, monkeypatch)
    identity = social["a"]
    tenant = UUID(identity["tenant_id"])
    with transaction(tenant, identity["tokens"]["OPERATOR"]) as repo:
        account = repo.one("social_connections", id=UUID(row["connection_id"]))["account_id"]

    def read(mid):
        with transaction(tenant, social["tokens"][str(tenant)]) as repo:
            assertions = repo.all("social_reconcile_requests", publish_run_id=UUID(row["id"]))
            assert len(assertions) == 1
            jobs = repo.all("social_publish_jobs", publish_run_id=UUID(row["id"]))
            assert jobs[-1]["stage"] == "RECONCILE" and jobs[-1]["status"] == "RUNNING"
        if case == "unavailable":
            raise SocialProviderError("RATE_LIMITED", retryable=True, retry_after_seconds=60)
        result = fake.result("reconcile", {"id": mid})
        result.payload = SimpleNamespace(
            id=mid,
            owner=SimpleNamespace(id="999999" if case == "wrong_account" else account),
            caption="Different" if case == "wrong_caption" else row["plan"]["caption"],
            media_type="IMAGE",
            timestamp=datetime.now(UTC).isoformat(),
        )
        return result

    monkeypatch.setattr(fake, "read_media", read, raising=False)
    path = f"/v1/social-publishes/{row['id']}/reconcile"
    denied = client.post(path, headers=headers(identity), json=request())
    assert denied.status_code == 403
    response = client.post(path, headers=headers(identity, "APPROVER"), json=request())
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["reconciliation_requests"]) == 1
    assert result["jobs"][-1]["stage"] == "RECONCILE"
    if case == "match":
        assert result["status"] == "PUBLISHED" and result["post_id"] == "76543"
        assert (
            client.post(path, headers=headers(identity, "APPROVER"), json=request()).json()[
                "post_id"
            ]
            == "76543"
        )
        assert fake.calls.count("reconcile") == 1
    else:
        assert result["status"] == "UNKNOWN_OUTCOME"
        assert result["jobs"][-1]["status"] == "FAILED"
        assert result["jobs"][-1]["retryable"] == (case == "unavailable")
    assert fake.calls.count("publish") == 1


def test_connector_cannot_reconcile_without_human_assertion(client, social, monkeypatch):
    row, _ = uncertain(client, social, monkeypatch)
    tenant = UUID(social["a"]["tenant_id"])
    with pytest.raises(DBAPIError):
        with transaction(tenant, social["tokens"][str(tenant)]) as repo:
            repo.connection.execute(
                text("SELECT social_reserve_job(:id,'RECONCILE','reconcile',:hash)"),
                {"id": row["id"], "hash": canonical_hash(request())},
            )


@pytest.mark.parametrize("field", ["captured_at", "response_hash", "id"])
def test_null_provider_acknowledgement_rejected_by_database(client, social, field):
    row, _, _, _ = ready(client, social)
    authorize(client, social, row)
    tenant = UUID(social["a"]["tenant_id"])
    with transaction(tenant, social["tokens"][str(tenant)]) as repo:
        jid = repo.connection.execute(
            text("SELECT social_reserve_job(:id,'CHILD','child:1',:hash)"),
            {"id": row["id"], "hash": "a" * 64},
        ).scalar_one()
    result = {
        "id": "12345",
        "captured_at": datetime.now(UTC).isoformat(),
        "response_hash": "b" * 64,
    }
    result[field] = None
    with pytest.raises(DBAPIError):
        with transaction(tenant, social["tokens"][str(tenant)]) as repo:
            repo.connection.execute(
                text("SELECT social_finish_job(:id,CAST(:p AS jsonb))"),
                {"id": jid, "p": json.dumps(result)},
            )


def test_connector_cannot_write_unreserved_skill_history(client, social):
    row, run, _, _ = ready(client, social)
    tenant = UUID(social["a"]["tenant_id"])
    with pytest.raises(DBAPIError):
        with transaction(tenant, social["tokens"][str(tenant)]) as repo:
            repo.connection.execute(
                text("""INSERT INTO skill_runs(tenant_id,workflow_run_id,step_key,
                skill_identifier,skill_version,input_schema_version,output_schema_version,
                provider,model,adapter,attempt,input_hash,status,is_mock,cost)
                VALUES(:tenant,:run,'social:forged','social.publish','1.0.0',1,1,
                'instagram','graph-api','instagram-login-v1',1,:hash,'RUNNING',false,NULL)"""),
                {"tenant": tenant, "run": run["id"], "hash": row["plan_hash"]},
            )
