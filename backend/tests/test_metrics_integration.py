"""Immutable, tenant-scoped observations and descriptive learning, without publishing.

Every synthetic observation uses FIXTURE mode. A MANUAL-mode contract test only
checks the explicit self-report label; it does not claim verified platform data.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import artifact_data, create, headers, revise
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_visual_workflow import approve_content, approve_visual, render
from test_visual_workflow import passing_render as _passing_render
from test_visual_workflow import visual_context as _visual_context

from app.ai.structured import OpenAISelectionAdapter
from app.db.repository import canonical_hash, engine, transaction
from app.metrics.schemas import MetricLearningInput, MetricSnapshotInput, MetricSubjectInput
from app.metrics.service import create_learning, create_subject, import_snapshot

visual_context = _visual_context
passing_render = _passing_render
COUNTERS = ("reach", "saves", "shares", "comments", "follows")


@pytest.fixture
def metrics_visual(client, passing_render):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    approval = approve_visual(client, identity, rendered)
    return identity, run, rendered, approval


@pytest.fixture
def no_external_observation(monkeypatch):
    calls = []

    def deny(*args, **kwargs):
        calls.append("external call")
        raise AssertionError("Manual metrics must not contact a platform or model")

    async def deny_async(*args, **kwargs):
        return deny(*args, **kwargs)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async)
    monkeypatch.setattr(OpenAISelectionAdapter, "select", deny)
    return calls


def subject_request(rendered, **overrides):
    data = {
        "schema_version": 1,
        "idempotency_key": str(uuid4()),
        "render_run_id": rendered["id"],
        "mode": "FIXTURE",
        "platform_label": "Internal test fixture",
        "external_reference": None,
        "provenance_note": "Synthetic observations in disposable mediaos_test; nothing posted.",
    }
    return {**data, **overrides}


def observation_request(**overrides):
    data = {
        "schema_version": 1,
        "idempotency_key": str(uuid4()),
        "observed_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        "scope": "LIFETIME_CUMULATIVE",
        "reach": 100,
        "saves": 5,
        "shares": None,
        "comments": 0,
        "follows": 2,
        "evidence_text": "Synthetic fixture totals; no platform API or actual account was used.",
        "definition_notes": "Same fixture definitions at both observation times.",
    }
    return {**data, **overrides}


def subject(client, identity, run, rendered, **overrides):
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/metric-subjects",
        headers=headers(identity),
        json=subject_request(rendered, **overrides),
    )
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(client, identity, measured, **overrides):
    response = client.post(
        f"/v1/metric-subjects/{measured['id']}/snapshots",
        headers=headers(identity),
        json=observation_request(**overrides),
    )
    assert response.status_code == 200, response.text
    return response.json()


def report_request(baseline, current, key=None):
    return {
        "idempotency_key": key or str(uuid4()),
        "baseline_snapshot_id": baseline["id"],
        "current_snapshot_id": current["id"],
    }


def learning(client, identity, measured, baseline, current, key=None):
    response = client.post(
        f"/v1/metric-subjects/{measured['id']}/learning-reports",
        headers=headers(identity),
        json=report_request(baseline, current, key),
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def observed_subject(client, metrics_visual):
    identity, run, rendered, _ = metrics_visual
    measured = subject(client, identity, run, rendered)
    baseline = snapshot(client, identity, measured)
    current = snapshot(
        client,
        identity,
        measured,
        observed_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        reach=125,
        saves=2,
        comments=0,
        follows=None,
    )
    return identity, run, measured, baseline, current


def worker(identity, function, owner_id, model, request):
    typed = model.model_validate_json(json.dumps(request), strict=True)
    return jsonable_encoder(
        function(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], UUID(owner_id), typed)
    )


def test_metrics_round_trip_preserves_exact_approved_lineage_and_unknown_values(
    client, metrics_visual, no_external_observation
):
    identity, run, rendered, visual = metrics_visual
    measured = subject(client, identity, run, rendered)
    assert measured["mode"] == "FIXTURE"
    assert measured["render_run_id"] == rendered["id"]
    assert measured["visual_approval_record_id"] == visual["visual_approval_record_id"]
    assert measured["content_approval_record_id"]
    assert measured["manifest_hash"] == rendered["manifest_hash"]
    for field in ("asset_version_id", "research_version_id", "qa_report_id"):
        assert measured[field] == run[field]
    observed = snapshot(client, identity, measured)
    assert observed["payload"]["shares"] is None
    assert observed["payload"]["comments"] == 0
    assert canonical_hash(observed["payload"]) == observed["content_hash"]
    fetched = client.get(f"/v1/metric-subjects/{measured['id']}", headers=headers(identity))
    assert fetched.status_code == 200
    assert [row["id"] for row in fetched.json()["snapshots"]] == [observed["id"]]
    listed = client.get(f"/v1/workflow-runs/{run['id']}/metric-subjects", headers=headers(identity))
    assert listed.status_code == 200 and listed.json()[0]["id"] == measured["id"]
    current = client.get(f"/v1/workflow-runs/{run['id']}", headers=headers(identity)).json()
    assert current["state"] == "APPROVED"
    assert no_external_observation == []


def test_metric_reads_preserve_exact_sql_timestamp_serialization_and_content_hash(
    client, metrics_visual
):
    identity, run, rendered, _ = metrics_visual
    measured = subject(client, identity, run, rendered)
    # The SQL boundary accepts these UTC spellings verbatim. Typed validation may
    # parse them, but read APIs must not reserialize timestamps under an old hash.
    now = datetime.now(UTC)
    timestamps = [
        (now - timedelta(hours=3)).replace(microsecond=120000).isoformat(timespec="microseconds"),
        (now - timedelta(hours=2)).replace(microsecond=0).isoformat(timespec="microseconds"),
    ]
    stored = {}
    for observed_at in timestamps:
        payload = observation_request(observed_at=observed_at)
        key = payload.pop("idempotency_key")
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            snapshot_id = repo.connection.execute(
                text("SELECT import_metric_snapshot(:subject,CAST(:payload AS jsonb),:key,:hash)"),
                {
                    "subject": UUID(measured["id"]),
                    "payload": json.dumps(payload),
                    "key": key,
                    "hash": canonical_hash({"subject_id": measured["id"], "payload": payload}),
                },
            ).scalar_one()
            row = repo.one("metric_snapshots", id=snapshot_id)
            assert row["payload"] == payload
            assert row["content_hash"] == canonical_hash(payload)
            stored[str(snapshot_id)] = row

    fetched = client.get(f"/v1/metric-subjects/{measured['id']}", headers=headers(identity))
    assert fetched.status_code == 200, fetched.text
    observations = fetched.json()["snapshots"]
    assert len(observations) == 2
    for row in observations:
        assert row["payload"] == stored[row["id"]]["payload"]
        assert canonical_hash(row["payload"]) == row["content_hash"]

    ordered = sorted(observations, key=lambda row: row["payload"]["observed_at"])
    report = learning(client, identity, measured, ordered[0], ordered[1])
    assert report["payload"]["baseline_observed_at"] == timestamps[0]
    assert report["payload"]["current_observed_at"] == timestamps[1]
    assert canonical_hash(report["payload"]) == report["content_hash"]
    for prefix, observation in zip(("baseline", "current"), ordered, strict=True):
        assert report["payload"][f"{prefix}_snapshot_hash"] == observation["content_hash"]

    history = client.get(
        f"/v1/metric-subjects/{measured['id']}/learning-reports", headers=headers(identity)
    )
    assert history.status_code == 200 and len(history.json()) == 1
    assert history.json()[0]["payload"] == report["payload"]
    assert canonical_hash(history.json()[0]["payload"]) == history.json()[0]["content_hash"]
    nested = client.get(f"/v1/metric-subjects/{measured['id']}", headers=headers(identity)).json()
    assert nested["learning_reports"][0]["payload"] == report["payload"]
    assert canonical_hash(nested["learning_reports"][0]["payload"]) == report["content_hash"]


def test_metrics_schema_forces_rls_and_does_not_grant_direct_writes(database):
    with database.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE relnamespace='public'::regnamespace AND "
                "relname IN ('metric_subjects','metric_snapshots','learning_reports')"
            )
        ).all()
    assert len(rows) == 3 and all(row[1] and row[2] for row in rows)
    with engine().begin() as conn:
        for table in ("metric_subjects", "metric_snapshots", "learning_reports"):
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                assert not conn.execute(
                    text("SELECT has_table_privilege(current_user,:table,:privilege)"),
                    {"table": table, "privilege": privilege},
                ).scalar_one()


@pytest.mark.parametrize("table", ["metric_subjects", "metric_snapshots", "learning_reports"])
def test_runtime_cannot_relabel_or_delete_metrics_records(client, observed_subject, table):
    identity, _, measured, baseline, current = observed_subject
    report = learning(client, identity, measured, baseline, current)
    row_id = {
        "metric_subjects": measured["id"],
        "metric_snapshots": baseline["id"],
        "learning_reports": report["id"],
    }[table]
    for statement in (
        f"UPDATE {table} SET id=id WHERE id=:id",
        f"DELETE FROM {table} WHERE id=:id",
        f"INSERT INTO {table} SELECT * FROM {table} WHERE id=:id",
    ):
        with pytest.raises(DBAPIError) as rejected:
            with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
                repo.connection.execute(text(statement), {"id": UUID(row_id)})
        assert getattr(rejected.value.orig, "sqlstate", None) == "42501"


def test_metric_subject_requires_both_approvals(client, passing_render):
    identity, run, rendered = passing_render
    request = subject_request(rendered)
    path = f"/v1/workflow-runs/{run['id']}/metric-subjects"
    assert client.post(path, headers=headers(identity), json=request).status_code == 409
    approve_content(client, identity, run)
    assert client.post(path, headers=headers(identity), json=request).status_code == 409
    approve_visual(client, identity, rendered)
    assert client.post(path, headers=headers(identity), json=request).status_code == 200


def test_historical_approved_render_can_be_measured_after_content_revision(client, metrics_visual):
    identity, run, rendered, _ = metrics_visual
    draft = artifact_data(client, identity, run)["content_asset_versions"][0]["payload"]
    changed = revise(client, identity, run, draft)
    assert changed["asset_version_id"] != run["asset_version_id"]
    assert changed["state"] == "AWAITING_APPROVAL"
    assert (
        client.get(f"/v1/renders/{rendered['id']}/export", headers=headers(identity)).status_code
        == 409
    )
    measured = subject(client, identity, run, rendered)
    assert measured["asset_version_id"] == run["asset_version_id"]
    assert snapshot(client, identity, measured)["subject_id"] == measured["id"]


def test_metrics_authentication_and_operator_permission(client, metrics_visual):
    identity, run, rendered, _ = metrics_visual
    path = f"/v1/workflow-runs/{run['id']}/metric-subjects"
    assert client.get(path).status_code == 401
    assert client.post(path, json=subject_request(rendered)).status_code == 401
    assert (
        client.post(
            path, headers=headers(identity, "APPROVER"), json=subject_request(rendered)
        ).status_code
        == 403
    )
    measured = subject(client, identity, run, rendered)
    assert (
        client.post(
            f"/v1/metric-subjects/{measured['id']}/snapshots",
            headers=headers(identity, "APPROVER"),
            json=observation_request(),
        ).status_code
        == 403
    )


def test_cross_tenant_metrics_retrieval_and_reference_are_rejected(
    client, observed_subject, visual_context
):
    owner, run, measured, baseline, current = observed_subject
    outsider = visual_context["b"]
    report = learning(client, owner, measured, baseline, current)
    for path in (
        f"/v1/metric-subjects/{measured['id']}",
        f"/v1/metric-subjects/{measured['id']}/learning-reports",
        f"/v1/workflow-runs/{run['id']}/metric-subjects",
    ):
        assert client.get(path, headers=headers(outsider)).status_code == 404
    assert (
        client.post(
            f"/v1/metric-subjects/{measured['id']}/snapshots",
            headers=headers(outsider),
            json=observation_request(),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/metric-subjects/{measured['id']}/learning-reports",
            headers=headers(outsider),
            json=report_request(baseline, current),
        ).status_code
        == 404
    )
    foreign_run = create(client, outsider)
    assert client.post(
        f"/v1/workflow-runs/{foreign_run['id']}/metric-subjects",
        headers=headers(outsider),
        json=subject_request({"id": measured["render_run_id"]}),
    ).status_code in (404, 409)
    with transaction(UUID(outsider["tenant_id"]), outsider["tokens"]["OPERATOR"]) as repo:
        for table, row_id in (
            ("metric_subjects", measured["id"]),
            ("metric_snapshots", baseline["id"]),
            ("learning_reports", report["id"]),
        ):
            assert (
                repo.connection.execute(
                    text(f"SELECT * FROM {table} WHERE id=:id"), {"id": UUID(row_id)}
                ).all()
                == []
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "MANUAL", "external_reference": None},
        {"mode": "FIXTURE", "external_reference": "https://example.invalid/not-a-post"},
        {"mode": "LIVE"},
        {"provenance_note": ""},
    ],
)
def test_subject_fixture_and_manual_provenance_cannot_be_ambiguous(
    client, metrics_visual, overrides
):
    identity, run, rendered, _ = metrics_visual
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/metric-subjects",
            headers=headers(identity),
            json=subject_request(rendered, **overrides),
        ).status_code
        == 422
    )


@pytest.mark.parametrize("value", [-1, 1.5, True, "100"])
def test_observation_rejects_non_integer_or_negative_counts(client, metrics_visual, value):
    identity, run, rendered, _ = metrics_visual
    measured = subject(client, identity, run, rendered)
    assert (
        client.post(
            f"/v1/metric-subjects/{measured['id']}/snapshots",
            headers=headers(identity),
            json=observation_request(reach=value),
        ).status_code
        == 422
    )


@pytest.mark.parametrize("missing", COUNTERS)
def test_every_counter_must_be_explicit_even_when_unknown(client, metrics_visual, missing):
    identity, run, rendered, _ = metrics_visual
    measured = subject(client, identity, run, rendered)
    request = observation_request()
    del request[missing]
    assert (
        client.post(
            f"/v1/metric-subjects/{measured['id']}/snapshots",
            headers=headers(identity),
            json=request,
        ).status_code
        == 422
    )


def test_future_observation_is_rejected(client, metrics_visual):
    identity, run, rendered, _ = metrics_visual
    measured = subject(client, identity, run, rendered)
    response = client.post(
        f"/v1/metric-subjects/{measured['id']}/snapshots",
        headers=headers(identity),
        json=observation_request(observed_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()),
    )
    assert response.status_code in (409, 422)
    fetched = client.get(f"/v1/metric-subjects/{measured['id']}", headers=headers(identity)).json()
    assert fetched["snapshots"] == []


@pytest.mark.parametrize("problem", ["negative", "boolean", "fraction", "missing", "future"])
def test_database_snapshot_guard_rejects_invalid_payload_without_api_validation(
    client, metrics_visual, problem
):
    identity, run, rendered, _ = metrics_visual
    measured = subject(client, identity, run, rendered)
    payload = observation_request()
    key = payload.pop("idempotency_key")
    if problem == "missing":
        del payload["shares"]
    elif problem == "future":
        payload["observed_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    else:
        payload["reach"] = {"negative": -1, "boolean": True, "fraction": 1.5}[problem]
    with pytest.raises(DBAPIError):
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("SELECT import_metric_snapshot(:subject,CAST(:payload AS jsonb),:key,:hash)"),
                {
                    "subject": UUID(measured["id"]),
                    "payload": json.dumps(payload),
                    "key": key,
                    "hash": canonical_hash({"subject_id": measured["id"], "payload": payload}),
                },
            )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert repo.all("metric_snapshots", subject_id=UUID(measured["id"])) == []


def test_manual_metrics_remain_self_reported_and_never_change_workflow_permissions(
    client, metrics_visual, no_external_observation
):
    identity, run, rendered, _ = metrics_visual
    measured = subject(
        client,
        identity,
        run,
        rendered,
        mode="MANUAL",
        external_reference=f"https://example.invalid/manual-contract-test/{uuid4()}",
        provenance_note="Contract test of an unverified manual assertion, not actual publication.",
    )
    assert measured["verification_status"] == "SELF_REPORTED"
    first = snapshot(client, identity, measured)
    last = snapshot(
        client,
        identity,
        measured,
        observed_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    )
    report = learning(client, identity, measured, first, last)
    assert first["verification_status"] == last["verification_status"] == "SELF_REPORTED"
    assert report["verification_status"] == "SELF_REPORTED"
    assert report["payload"]["limitations"] == [
        "SELF_REPORTED",
        "DESCRIPTIVE_ONLY",
        "NO_CAUSAL_INFERENCE",
    ]
    assert report["payload"]["policy_updated"] is False
    assert no_external_observation == []


def test_same_tenant_render_cannot_be_attached_to_an_unrelated_workflow(client, metrics_visual):
    identity, _, rendered, _ = metrics_visual
    unrelated = create(client, identity)
    response = client.post(
        f"/v1/workflow-runs/{unrelated['id']}/metric-subjects",
        headers=headers(identity),
        json=subject_request(rendered),
    )
    assert response.status_code == 404
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert repo.all("metric_subjects", workflow_run_id=UUID(unrelated["id"])) == []


def test_subject_and_snapshot_idempotency_rejects_changed_payloads(client, metrics_visual):
    identity, run, rendered, _ = metrics_visual
    key = str(uuid4())
    measured = subject(client, identity, run, rendered, idempotency_key=key)
    assert subject(client, identity, run, rendered, idempotency_key=key)["id"] == measured["id"]
    changed = client.post(
        f"/v1/workflow-runs/{run['id']}/metric-subjects",
        headers=headers(identity),
        json=subject_request(rendered, idempotency_key=key, platform_label="Changed label"),
    )
    assert changed.status_code == 409
    relabel = client.post(
        f"/v1/workflow-runs/{run['id']}/metric-subjects",
        headers=headers(identity),
        json=subject_request(
            rendered,
            idempotency_key=key,
            mode="MANUAL",
            external_reference=f"https://example.invalid/relabel/{uuid4()}",
        ),
    )
    assert relabel.status_code == 409
    request = observation_request()
    path = f"/v1/metric-subjects/{measured['id']}/snapshots"
    first = client.post(path, headers=headers(identity), json=request)
    repeated = client.post(path, headers=headers(identity), json=request)
    assert first.status_code == repeated.status_code == 200
    assert first.json()["id"] == repeated.json()["id"]
    assert (
        client.post(path, headers=headers(identity), json={**request, "reach": 101}).status_code
        == 409
    )


def test_concurrent_subject_and_snapshot_requests_commit_once(client, metrics_visual):
    identity, run, rendered, _ = metrics_visual
    request = subject_request(rendered)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(worker, identity, create_subject, run["id"], MetricSubjectInput, request)
            for _ in range(4)
        ]
        measured = [future.result(timeout=30) for future in futures]
    assert len({row["id"] for row in measured}) == 1
    observation = observation_request()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                worker,
                identity,
                import_snapshot,
                measured[0]["id"],
                MetricSnapshotInput,
                observation,
            )
            for _ in range(4)
        ]
        rows = [future.result(timeout=30) for future in futures]
    assert len({row["id"] for row in rows}) == 1
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert len(repo.all("metric_subjects", workflow_run_id=UUID(run["id"]))) == 1
        assert len(repo.all("metric_snapshots", subject_id=UUID(measured[0]["id"]))) == 1


def test_identical_keys_are_independent_across_tenants(client, metrics_visual, visual_context):
    owner, run, rendered, _ = metrics_visual
    other = visual_context["b"]
    other_run = create(client, other)
    other_render = render(client, other, other_run, visual_context["configs"][1])
    approve_content(client, other, other_run)
    approve_visual(client, other, other_render)
    shared = str(uuid4())
    left = subject(client, owner, run, rendered, idempotency_key=shared)
    right = subject(client, other, other_run, other_render, idempotency_key=shared)
    assert left["id"] != right["id"]
    timestamp = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    a = snapshot(client, owner, left, idempotency_key=shared, observed_at=timestamp)
    b = snapshot(client, other, right, idempotency_key=shared, observed_at=timestamp)
    assert a["id"] != b["id"]
    later = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    a2 = snapshot(client, owner, left, observed_at=later)
    b2 = snapshot(client, other, right, observed_at=later)
    assert (
        learning(client, owner, left, a, a2, shared)["id"]
        != learning(client, other, right, b, b2, shared)["id"]
    )
    foreign = client.post(
        f"/v1/metric-subjects/{left['id']}/learning-reports",
        headers=headers(owner),
        json=report_request(a, b2),
    )
    assert foreign.status_code in (404, 409)


def test_learning_is_descriptive_null_aware_and_persists_zero_cost_audit(
    client, observed_subject, no_external_observation
):
    identity, run, measured, baseline, current = observed_subject
    report = learning(client, identity, measured, baseline, current)
    payload = report["payload"]
    assert canonical_hash(payload) == report["content_hash"]
    assert payload["baseline_snapshot_hash"] == baseline["content_hash"]
    assert payload["current_snapshot_hash"] == current["content_hash"]
    assert payload["causal_claim"] is False and payload["policy_updated"] is False
    assert payload["mode"] == "FIXTURE"
    assert set(payload["limitations"]) >= {"FIXTURE", "DESCRIPTIVE_ONLY", "NO_CAUSAL_INFERENCE"}
    metrics = payload["metrics"]
    assert set(metrics) == set(COUNTERS)
    assert metrics["reach"] == {
        "baseline": 100,
        "current": 125,
        "delta": 25,
        "direction": "INCREASED",
    }
    assert metrics["saves"]["delta"] == -3 and metrics["saves"]["direction"] == "DECREASED"
    assert metrics["comments"]["delta"] == 0 and metrics["comments"]["direction"] == "UNCHANGED"
    for field in ("shares", "follows"):
        assert metrics[field]["delta"] is None and metrics[field]["direction"] == "UNKNOWN"
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        attempts = repo.all(
            "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="learning.describe"
        )
        assert len(attempts) == 1 and attempts[0]["status"] == "SUCCEEDED"
        assert attempts[0]["provider"] == "deterministic" and attempts[0]["attempt"] == 1
        costs = repo.all("cost_events", skill_run_id=attempts[0]["id"])
        assert len(costs) == 1
        assert costs[0]["cost"] == 0 and costs[0]["input_tokens"] == costs[0]["output_tokens"] == 0
    audit = client.get(f"/v1/workflow-runs/{run['id']}/audit", headers=headers(identity)).json()
    assert {row["event_type"] for row in audit} >= {
        "METRIC_SUBJECT_REGISTERED",
        "METRIC_SNAPSHOT_IMPORTED",
        "METRIC_LEARNING_CREATED",
    }
    assert no_external_observation == []


def test_all_unknown_observations_produce_insufficient_data_not_zeroes(client, metrics_visual):
    identity, run, rendered, _ = metrics_visual
    measured = subject(client, identity, run, rendered)
    empty = dict.fromkeys(COUNTERS)
    baseline = snapshot(client, identity, measured, **empty)
    current = snapshot(
        client,
        identity,
        measured,
        observed_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        **empty,
    )
    report = learning(client, identity, measured, baseline, current)
    assert report["payload"]["status"] == "INSUFFICIENT_DATA"
    assert all(
        row == {"baseline": None, "current": None, "delta": None, "direction": "UNKNOWN"}
        for row in report["payload"]["metrics"].values()
    )


def test_learning_requires_same_subject_definitions_and_forward_time(client, observed_subject):
    identity, _, measured, baseline, current = observed_subject
    path = f"/v1/metric-subjects/{measured['id']}/learning-reports"
    for first, second in ((current, baseline), (baseline, baseline)):
        assert client.post(
            path, headers=headers(identity), json=report_request(first, second)
        ).status_code in (409, 422)
    simultaneous = snapshot(
        client, identity, measured, observed_at=baseline["payload"]["observed_at"]
    )
    assert (
        client.post(
            path, headers=headers(identity), json=report_request(baseline, simultaneous)
        ).status_code
        == 409
    )
    incompatible = snapshot(
        client,
        identity,
        measured,
        observed_at=(datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
        definition_notes="Different counting definition; incomparable observations.",
    )
    assert (
        client.post(
            path, headers=headers(identity), json=report_request(baseline, incompatible)
        ).status_code
        == 409
    )


def test_same_tenant_unrelated_subject_snapshots_cannot_be_compared(client, metrics_visual):
    identity, run, rendered, _ = metrics_visual
    left = subject(client, identity, run, rendered)
    right = subject(client, identity, run, rendered, platform_label="A separate fixture series")
    first = snapshot(client, identity, left)
    second = snapshot(
        client, identity, right, observed_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat()
    )
    response = client.post(
        f"/v1/metric-subjects/{left['id']}/learning-reports",
        headers=headers(identity),
        json=report_request(first, second),
    )
    assert response.status_code == 404
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert repo.all("learning_reports", subject_id=UUID(left["id"])) == []


def test_concurrent_learning_is_idempotent_without_duplicate_costs(client, observed_subject):
    identity, run, measured, baseline, current = observed_subject
    request = report_request(baseline, current)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                worker, identity, create_learning, measured["id"], MetricLearningInput, request
            )
            for _ in range(4)
        ]
        reports = [future.result(timeout=30) for future in futures]
    assert len({row["id"] for row in reports}) == 1
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        attempts = repo.all(
            "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="learning.describe"
        )
        assert len(attempts) == 1
        assert len(repo.all("cost_events", skill_run_id=attempts[0]["id"])) == 1
    third = snapshot(
        client,
        identity,
        measured,
        observed_at=(datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
    )
    response = client.post(
        f"/v1/metric-subjects/{measured['id']}/learning-reports",
        headers=headers(identity),
        json={**request, "current_snapshot_id": third["id"]},
    )
    assert response.status_code == 409


def test_failed_telemetry_commit_rolls_back_entire_learning_report(
    client, observed_subject, database
):
    identity, run, measured, baseline, current = observed_subject
    request = report_request(baseline, current)
    # Inject a database failure after report creation. All report/attempt/cost/audit
    # writes must share one transaction; an incomplete report must not survive.
    with database.begin() as conn:
        conn.execute(
            text("""
            CREATE FUNCTION public.reject_metrics_test_cost() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              IF EXISTS (SELECT 1 FROM skill_runs WHERE id=NEW.skill_run_id
                         AND skill_identifier='learning.describe') THEN
                RAISE EXCEPTION 'synthetic telemetry failure' USING ERRCODE='23514';
              END IF;
              RETURN NEW;
            END $$;
            CREATE TRIGGER reject_metrics_test_cost BEFORE INSERT ON cost_events
            FOR EACH ROW EXECUTE FUNCTION public.reject_metrics_test_cost();
        """)
        )
    try:
        response = client.post(
            f"/v1/metric-subjects/{measured['id']}/learning-reports",
            headers=headers(identity),
            json=request,
        )
        assert response.status_code == 409
    finally:
        with database.begin() as conn:
            conn.execute(text("DROP TRIGGER reject_metrics_test_cost ON cost_events"))
            conn.execute(text("DROP FUNCTION public.reject_metrics_test_cost()"))
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert repo.all("learning_reports", subject_id=UUID(measured["id"])) == []
        assert (
            repo.all(
                "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="learning.describe"
            )
            == []
        )
        assert not any(
            row["event_type"] == "METRIC_LEARNING_CREATED"
            for row in repo.all("audit_events", workflow_run_id=UUID(run["id"]))
        )
    assert learning(client, identity, measured, baseline, current, request["idempotency_key"])["id"]
