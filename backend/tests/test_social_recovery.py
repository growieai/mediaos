"""Database account cooldowns and uncertain POSTs; no external requests."""

# ruff: noqa: F811 -- imported pytest fixtures are intentionally injected.
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime
from threading import Event
from uuid import uuid4

import pytest
from conftest import headers
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_community_integration import community_identities  # noqa: F401
from test_social_integration import (  # noqa: F401
    FakeInstagram,
    authorize,
    execute,
    ready,
    social,
    visual_context,
)
from test_social_replies_integration import (  # noqa: F401
    authorize as authorize_reply,
)
from test_social_replies_integration import (  # noqa: F401
    raw,
    reply_parent,
    reply_run,
    sql,
)

from app.db.repository import canonical_hash, transaction
from app.social import service


def connector(data):
    identity = data["a"]
    return {
        **identity,
        "tokens": {**identity["tokens"], "SOCIAL": data["tokens"][identity["tenant_id"]]},
    }


def reserve(identity, row, stage="INSIGHTS", step=None, input_hash="e" * 64):
    return sql(
        identity,
        "SELECT social_reserve_job(:id,:stage,:step,:hash)",
        {"id": row["id"], "stage": stage, "step": step or str(uuid4()), "hash": input_hash},
        "SOCIAL",
    )


def fail(identity, job, category="RATE_LIMITED", retry=True, unknown=False, delay=120):
    return sql(
        identity,
        "SELECT social_fail_job(:id,:category,:retry,:unknown,:delay)",
        {"id": job, "category": category, "retry": retry, "unknown": unknown, "delay": delay},
        "SOCIAL",
    )


def cooldown(database, row):
    with database.connect() as conn:
        return conn.execute(
            text(
                "SELECT retry_after FROM private.social_account_cooldowns WHERE tenant_id=:t AND account_id=:a"
            ),
            {"t": row["tenant_id"], "a": row["account_id"]},
        ).scalar_one()


def expire(database, row):
    # Simulated passage of time is a test-admin action, unavailable to runtime.
    with database.begin() as conn:
        conn.execute(
            text(
                "UPDATE private.social_account_cooldowns SET retry_after=clock_timestamp()-interval '1 second' WHERE tenant_id=:t AND account_id=:a"
            ),
            {"t": row["tenant_id"], "a": row["account_id"]},
        )
        conn.execute(
            text(
                "UPDATE social_publish_jobs SET retry_at=clock_timestamp()-interval '1 second' WHERE publish_run_id=:id"
            ),
            {"id": row["id"]},
        )


def finish(identity, job, payload):
    sql(
        identity,
        "SELECT social_finish_job(:id,CAST(:p AS jsonb))",
        {
            "id": job,
            "p": json.dumps(
                {**payload, "response_hash": "d" * 64, "captured_at": datetime.now(UTC).isoformat()}
            ),
        },
        "SOCIAL",
    )


@pytest.fixture
def pending(client, social):
    row, _, _, _ = ready(client, social)
    authorize(client, social, row)
    identity = connector(social)
    for index in range(len(row["plan"]["slides"])):
        finish(identity, reserve(identity, row, "CHILD", f"child:{index + 1}"), {"id": "10001"})
    if len(row["plan"]["slides"]) > 1:
        finish(identity, reserve(identity, row, "CONTAINER", "container"), {"id": "10002"})
    finish(identity, reserve(identity, row, "POLL", "poll:1"), {"status_code": "FINISHED"})
    return identity, row, reserve(identity, row, "PUBLISH", "publish")


@pytest.mark.parametrize(
    "category,retry,unknown",
    [
        ("UNKNOWN_OUTCOME", True, False),
        ("UNKNOWN_OUTCOME", False, False),
        ("PROCESS_INTERRUPTED", False, False),
        ("LOCAL_RECEIPT_FAILED", False, False),
        ("NETWORK", True, False),
        ("PROVIDER_UNAVAILABLE", True, False),
        ("RATE_LIMITED", True, True),
    ],
)
def test_publish_cannot_disguise_uncertainty_as_retry(pending, category, retry, unknown):
    identity, row, job = pending
    with pytest.raises(DBAPIError):
        fail(identity, job, category, retry, unknown, 0)
    assert raw(identity, "social_publish_jobs", job)["status"] == "RUNNING"
    assert raw(identity, "social_publish_runs", row["id"])["status"] == "PUBLISHING"
    fail(identity, job, "UNKNOWN_OUTCOME", False, True, 0)
    assert raw(identity, "social_publish_runs", row["id"])["status"] == "UNKNOWN_OUTCOME"
    with pytest.raises(DBAPIError):
        reserve(identity, row, "PUBLISH", "publish")


def test_explicit_refusal_can_retry_only_after_account_cooldown(pending, database):
    identity, row, job = pending
    fail(identity, job)
    assert raw(identity, "social_publish_runs", row["id"])["status"] == "READY"
    assert (cooldown(database, row) - datetime.now(UTC)).total_seconds() > 115
    with pytest.raises(DBAPIError):
        reserve(identity, row, "PUBLISH", "publish")
    expire(database, row)
    second = reserve(identity, row, "PUBLISH", "publish")
    assert raw(identity, "social_publish_jobs", second)["attempt"] == 2


def test_unknown_with_retry_after_preserves_account_backoff(pending, database):
    identity, row, job = pending
    fail(identity, job, "UNKNOWN_OUTCOME", False, True, 180)
    assert (cooldown(database, row) - datetime.now(UTC)).total_seconds() > 175
    assert not raw(identity, "social_publish_jobs", job)["retryable"]
    request = {
        "candidate_media_id": "123456",
        "confirm_external_match": True,
        "comment": "Synthetic human reconciliation attestation.",
    }
    sql(
        identity,
        "SELECT social_request_reconciliation(:id,CAST(:p AS jsonb))",
        {"id": row["id"], "p": json.dumps(request)},
        "APPROVER",
    )
    with pytest.raises(DBAPIError):
        reserve(identity, row, "RECONCILE", input_hash=canonical_hash(request))


@pytest.fixture
def published(client, social, monkeypatch):
    row, _, _, _ = ready(client, social)
    fake = FakeInstagram(social)
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    assert execute(client, social, row).json()["status"] == "PUBLISHED"
    return connector(social), row, fake


def test_new_insights_keys_cannot_bypass_shared_cooldown(published, client, social, database):
    identity, row, fake = published
    job = reserve(identity, row)
    fail(identity, job)
    with pytest.raises(DBAPIError):
        reserve(identity, row)
    response = client.post(
        f"/v1/social-publishes/{row['id']}/insights",
        headers=headers(identity),
        json={"idempotency_key": str(uuid4())},
    )
    assert response.status_code == 409
    assert "insights" not in fake.calls
    with transaction(identity["tenant_id"], identity["tokens"]["SOCIAL"]) as repo:
        jobs = repo.all("social_publish_jobs", publish_run_id=row["id"])
        assert sum(j["stage"] == "INSIGHTS" for j in jobs) == 1
    # Independent account activity is not globally suspended.
    independent, _, _, _ = ready(client, social)
    authorize(client, social, independent)
    assert reserve(identity, independent, "CHILD", "child:1")
    expire(database, row)
    assert reserve(identity, row)


def test_final_failed_attempt_still_blocks_new_publish_key(client, social, database):
    row, _, rendered, payload = ready(client, social)
    identity = connector(social)
    authorize(client, social, row)
    for attempt in range(1, 4):
        job = reserve(identity, row, "CHILD", "child:1")
        assert raw(identity, "social_publish_jobs", job)["attempt"] == attempt
        fail(identity, job, delay=0)
        if attempt < 3:
            expire(database, row)
    assert raw(identity, "social_publish_runs", row["id"])["status"] == "FAILED"
    assert raw(identity, "social_publish_jobs", job)["retry_at"] is None
    assert (cooldown(database, row) - datetime.now(UTC)).total_seconds() > 55
    payload["idempotency_key"] = str(uuid4())
    replacement = client.post(
        f"/v1/renders/{rendered['id']}/social-publishes", headers=headers(identity), json=payload
    )
    assert replacement.status_code == 200, replacement.text
    authorize(client, social, replacement.json())
    with pytest.raises(DBAPIError):
        reserve(identity, replacement.json(), "CHILD", "child:1")


def test_concurrent_new_key_waits_for_failure_cooldown_commit(published):
    identity, row, _ = published
    job = reserve(identity, row)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction(identity["tenant_id"], identity["tokens"]["SOCIAL"]) as repo:
            repo.connection.execute(
                text("SELECT social_fail_job(:id,'RATE_LIMITED',true,false,120)"), {"id": job}
            )
            pending = pool.submit(reserve, identity, row)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.15)
        with pytest.raises(DBAPIError):
            pending.result(timeout=10)


def test_reply_and_insights_share_account_cooldown(reply_run, database):
    identity, _, parent, _, _, _, row = reply_run
    authorize_reply(identity, row)
    # reply_parent models historical publication directly; add its historical
    # authorization so the independent insights entry point is eligible.
    with database.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO social_publish_decisions(tenant_id,publish_run_id,plan_hash,decision,created_by) SELECT tenant_id,id,plan_hash,'AUTHORIZE_PUBLISH',created_by FROM social_publish_runs WHERE id=:id"
            ),
            {"id": parent["id"]},
        )
    job = reserve(identity, parent)
    fail(identity, job)
    with pytest.raises(DBAPIError):
        sql(identity, "SELECT social_reserve_reply(:id)", {"id": row["id"]}, "SOCIAL")
    expire(database, parent)
    reply_job = sql(identity, "SELECT social_reserve_reply(:id)", {"id": row["id"]}, "SOCIAL")
    sql(
        identity,
        "SELECT social_fail_reply(:id,'RATE_LIMITED',true,false,240)",
        {"id": reply_job},
        "SOCIAL",
    )
    with pytest.raises(DBAPIError):
        reserve(identity, parent)
    assert (cooldown(database, parent) - datetime.now(UTC)).total_seconds() > 235


def test_runtime_cannot_clear_or_forge_cooldown(published, social):
    identity, row, _ = published
    fail(identity, reserve(identity, row))
    for actor in (identity, social["b"]):
        for query in (
            "SELECT * FROM private.social_account_cooldowns",
            "DELETE FROM private.social_account_cooldowns",
            "SELECT private.social_record_cooldown(:t,:a,1)",
        ):
            with pytest.raises(DBAPIError):
                sql(actor, query, {"t": row["tenant_id"], "a": row["account_id"]})


@pytest.mark.parametrize(
    "operation",
    [
        "fail_publish",
        "finish_publish",
        "request_reconciliation",
        "reserve_insights",
        "reserve_reconcile",
        "fail_reply",
        "finish_reply",
    ],
)
def test_recovery_locks_workflow_before_account_parent_and_job(request, database, operation):
    """Reproduce dispatch/recovery lock contention without a real provider call."""
    role = "SOCIAL"
    if operation.endswith("reply"):
        identity, _, parent, _, _, _, row = request.getfixturevalue("reply_run")
        authorize_reply(identity, row)
        job = sql(identity, "SELECT social_reserve_reply(:id)", {"id": row["id"]}, "SOCIAL")
        table, job_table = "social_reply_runs", "social_reply_jobs"
        account = parent["account_id"]
        if operation == "fail_reply":
            query = "SELECT social_fail_reply(:id,'RATE_LIMITED',true,false,120)"
            parameters = {"id": job}
        else:
            receipt = {
                "schema_version": 1,
                "id": "32145",
                "response_hash": "a" * 64,
                "captured_at": datetime.now(UTC).isoformat(),
                "raw": {"id": "32145"},
                "raw_hash": canonical_hash({"id": "32145"}),
                "text_hash": row["text_hash"],
            }
            query = "SELECT social_finish_reply(:id,CAST(:p AS jsonb))"
            parameters = {"id": job, "p": json.dumps(receipt)}
    else:
        table, job_table = "social_publish_runs", "social_publish_jobs"
        if operation == "reserve_insights":
            identity, row, _ = request.getfixturevalue("published")
            job = None
            query = "SELECT social_reserve_job(:id,'INSIGHTS',:key,:hash)"
            parameters = {"id": row["id"], "key": str(uuid4()), "hash": "e" * 64}
        else:
            identity, row, job = request.getfixturevalue("pending")
            if operation == "fail_publish":
                query = "SELECT social_fail_job(:id,'RATE_LIMITED',true,false,120)"
                parameters = {"id": job}
            elif operation == "finish_publish":
                query = "SELECT social_finish_job(:id,CAST(:p AS jsonb))"
                parameters = {
                    "id": job,
                    "p": json.dumps(
                        {
                            "id": "32145",
                            "response_hash": "a" * 64,
                            "captured_at": datetime.now(UTC).isoformat(),
                        }
                    ),
                }
            else:
                fail(identity, job, "UNKNOWN_OUTCOME", False, True, 0)
                attestation = {
                    "candidate_media_id": "32145",
                    "confirm_external_match": True,
                    "comment": "Synthetic reviewed candidate.",
                }
                query = "SELECT social_request_reconciliation(:id,CAST(:p AS jsonb))"
                parameters = {"id": row["id"], "p": json.dumps(attestation)}
                role = "APPROVER"
                if operation == "reserve_reconcile":
                    sql(identity, query, parameters, role)
                    query = "SELECT social_reserve_job(:id,'RECONCILE',:key,:hash)"
                    parameters = {
                        "id": row["id"],
                        "key": str(uuid4()),
                        "hash": canonical_hash(attestation),
                    }
                    role = "SOCIAL"
        account = row["account_id"]

    started = Event()

    def recovery():
        with transaction(identity["tenant_id"], identity["tokens"][role]) as repo:
            repo.connection.execute(text("SET LOCAL lock_timeout='5s'"))
            started.set()
            return repo.connection.execute(text(query), parameters).scalar_one_or_none()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout='500ms'"))
            conn.execute(
                text("SELECT id FROM workflow_runs WHERE id=:id FOR UPDATE"),
                {"id": row["workflow_run_id"]},
            )
            future = pool.submit(recovery)
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.15)
            # Recovery must wait at the workflow, holding none of these later
            # locks. Before the fix at least one acquisition below times out.
            conn.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtextextended('social-account:'||:account,0))"
                ),
                {"account": account},
            )
            conn.execute(text(f"SELECT id FROM {table} WHERE id=:id FOR UPDATE"), {"id": row["id"]})
            if job:
                conn.execute(
                    text(f"SELECT id FROM {job_table} WHERE id=:id FOR UPDATE"), {"id": job}
                )
        future.result(timeout=10)
