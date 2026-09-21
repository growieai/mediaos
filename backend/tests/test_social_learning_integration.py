"""Recorded-shaped platform observations with local fake connector calls only."""

# ruff: noqa: F811 -- pytest fixture imports are injected by parameter name.

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

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

from app.db.repository import engine, transaction
from app.metrics.schemas import MetricLearningInput
from app.social import learning, service


def observe(client, social, row, key):
    response = client.post(
        f"/v1/social-publishes/{row['id']}/insights",
        headers=headers(social["a"]),
        json={"idempotency_key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()["insights"][-1]


@pytest.fixture
def observations(client, social, monkeypatch):
    row, run, _, _ = ready(client, social)
    fake = FakeInstagram(social)
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    assert execute(client, social, row).json()["status"] == "PUBLISHED"
    values = {"reach": 100, "saved": 5, "comments": 0, "shares": None}
    definitions = {key: f"Exact lifetime {key}" for key in values}

    def insights(media_id):
        return fake.result(
            "insights",
            {"media_id": media_id, "metrics": values.copy(), "definitions": definitions.copy()},
        )

    monkeypatch.setattr(fake, "insights", insights)
    before = observe(client, social, row, "before")
    values.update(reach=120, saved=3, shares=8)
    after = observe(client, social, row, "after")
    request = {
        "baseline_snapshot_id": before["id"],
        "current_snapshot_id": after["id"],
        "idempotency_key": str(uuid4()),
    }
    return {
        "social": social,
        "row": row,
        "run": run,
        "before": before,
        "after": after,
        "request": request,
        "fake": fake,
        "values": values,
        "definitions": definitions,
    }


def post(client, data, payload=None, identity=None, role="OPERATOR"):
    return client.post(
        f"/v1/social-publishes/{data['row']['id']}/learning",
        headers=headers(identity or data["social"]["a"], role),
        json=payload or data["request"],
    )


def test_platform_learning_exact_immutable_idempotent_report_and_telemetry(client, observations):
    data = observations
    calls = data["fake"].calls.copy()
    response = post(client, data)
    assert response.status_code == 200, response.text
    report = response.json()
    payload = report["payload"]
    assert payload["metrics"]["reach"]["delta"] == 20
    assert payload["metrics"]["saved"]["delta"] == -2
    assert payload["metrics"]["comments"]["delta"] == 0
    assert payload["metrics"]["shares"]["delta"] is None
    assert report["provenance"] == payload["provenance"] == "PLATFORM"
    assert (
        payload["causal_claim"]
        is payload["policy_updated"]
        is payload["network_performed"]
        is False
    )
    assert payload["baseline_snapshot_hash"] == data["before"]["content_hash"]
    assert post(client, data).json()["id"] == report["id"]
    assert data["fake"].calls == calls
    identity = data["social"]["a"]
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        skill = repo.one("skill_runs", id=UUID(report["skill_run_id"]))
        cost = repo.one("cost_events", skill_run_id=skill["id"])
        assert skill["status"] == "SUCCEEDED" and skill["output"] == payload and skill["cost"] == 0
        assert (
            cost["provider"] == "deterministic"
            and cost["input_tokens"] == cost["output_tokens"] == cost["cost"] == 0
        )
        events = repo.all(
            "audit_events",
            workflow_run_id=UUID(data["run"]["id"]),
            event_type="SOCIAL_LEARNING_CREATED",
        )
        assert len(events) == 1
    listed = client.get(
        f"/v1/social-publishes/{data['row']['id']}/learning", headers=headers(identity)
    )
    assert listed.status_code == 200 and listed.json()[0]["id"] == report["id"]
    with pytest.raises(DBAPIError):
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("UPDATE social_learning_reports SET payload='{}' WHERE id=:id"),
                {"id": report["id"]},
            )
    with engine().begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM social_learning_reports")).scalar_one() == 0


def test_learning_tenant_roles_and_snapshot_input_boundaries(client, observations):
    data = observations
    assert post(client, data, role="APPROVER").status_code == 403
    assert post(client, data, identity=data["social"]["b"]).status_code == 404
    for changes, code in (
        ({"baseline_snapshot_id": str(uuid4())}, 404),
        ({"baseline_snapshot_id": data["after"]["id"]}, 422),
        (
            {
                "baseline_snapshot_id": data["after"]["id"],
                "current_snapshot_id": data["before"]["id"],
            },
            409,
        ),
    ):
        assert post(client, data, data["request"] | changes).status_code == code
    assert post(client, data).status_code == 200
    changed = data["request"] | {"current_snapshot_id": str(uuid4())}
    assert post(client, data, changed).status_code == 409


def test_concurrent_learning_replay_creates_one_attempt(client, observations):
    data = observations
    identity = data["social"]["a"]
    request = MetricLearningInput.model_validate_json(json.dumps(data["request"]), strict=True)

    def build(_):
        return learning.create(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(data["row"]["id"]),
            request,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = list(pool.map(build, range(3)))
    assert len({row["id"] for row in reports}) == len({row["skill_run_id"] for row in reports}) == 1


def test_definition_change_blocks_comparison(client, observations):
    data = observations
    data["definitions"]["reach"] = "Changed period or definition"
    changed = observe(client, data["social"], data["row"], "changed-definition")
    response = post(client, data, data["request"] | {"current_snapshot_id": changed["id"]})
    assert response.status_code == 409


def test_unknown_platform_counters_remain_unknown(client, observations):
    data = observations
    data["values"].update(dict.fromkeys(data["values"]))
    before = observe(client, data["social"], data["row"], "unknown-before")
    after = observe(client, data["social"], data["row"], "unknown-after")
    response = post(
        client,
        data,
        data["request"]
        | {"baseline_snapshot_id": before["id"], "current_snapshot_id": after["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "INSUFFICIENT_DATA"
    assert all(metric["delta"] is None for metric in response.json()["payload"]["metrics"].values())


def test_historical_learning_does_not_require_current_dispatch_authority(client, observations):
    data = observations
    identity = data["social"]["a"]
    revoked = client.post(
        f"/v1/social/connections/{data['row']['connection_id']}/revoke",
        headers=headers(identity, "ADMIN"),
    )
    assert revoked.status_code == 200
    response = post(client, data)
    assert response.status_code == 200, response.text
    assert response.json()["payload"]["policy_updated"] is False


def test_runtime_cannot_import_platform_learning_or_snapshots(client, observations):
    data = observations
    identity = data["social"]["a"]
    report = post(client, data).json()
    for query in (
        "INSERT INTO social_learning_reports SELECT * FROM social_learning_reports WHERE id=:id",
        "INSERT INTO social_insight_snapshots SELECT * FROM social_insight_snapshots WHERE id=:id",
    ):
        with pytest.raises(DBAPIError) as caught:
            with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
                repo.connection.execute(text(query), {"id": report["id"]})
        assert caught.value.orig.sqlstate == "42501"


def test_same_tenant_different_post_snapshot_is_rejected(client, observations, monkeypatch):
    data = observations
    original = copy.deepcopy(data["request"])
    second, _, _, _ = ready(client, data["social"])
    monkeypatch.setattr(
        data["fake"], "publish", lambda *args: data["fake"].result("publish", {"id": "10004"})
    )
    authorize(client, data["social"], second)
    assert execute(client, data["social"], second).json()["status"] == "PUBLISHED"
    unrelated = observe(client, data["social"], second, "unrelated-post")
    response = post(client, data, original | {"current_snapshot_id": unrelated["id"]})
    assert response.status_code == 404
