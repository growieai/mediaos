"""Database and HTTP-transport tests: all provider requests are intercepted locally."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import artifact_data, create, decision, headers
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.ai import runtime
from app.ai.structured import OpenAISelectionAdapter
from app.config import get_settings
from app.db.repository import engine, transaction
from app.services.workflows import Runner


def policy(**overrides):
    now = datetime.now(UTC)
    data = {
        "schema_version": 1,
        "enabled": True,
        "model": "test-configured-model",
        "max_input_bytes": 65536,
        "max_output_tokens": 1024,
        "max_calls_per_run": 2,
        "max_calls_per_day": 1000,
        "input_usd_per_million_tokens": "1",
        "output_usd_per_million_tokens": "2",
        "per_run_usd": "1",
        "per_day_usd": "1000",
        "price_reference": "Fixture prices, not real billing",
        "price_checked_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
    }
    return data | overrides


def configure(client, identity, **overrides):
    response = client.post(
        "/v1/text-ai-policy", headers=headers(identity, "ADMIN"), json=policy(**overrides)
    )
    assert response.status_code == 200, response.text
    return response.json()


def response_for(request, **overrides):
    payload = json.loads(request.content)
    schema = payload["text"]["format"]["schema"]
    slides = schema["$defs"]["SlideSelection"]["properties"]
    plan = {
        "schema_version": 1,
        "slides": [
            {"fact_id": fid, "headline_template_id": slides["headline_template_id"]["enum"][0]}
            for fid in slides["fact_id"]["enum"]
        ],
        "caption_template_id": schema["properties"]["caption_template_id"]["enum"][0],
        "cta_template_id": schema["properties"]["cta_template_id"]["enum"][0],
    } | overrides
    return httpx.Response(
        200,
        headers={"x-request-id": "req_fixture"},
        json={
            "id": "resp_fixture",
            "model": payload["model"],
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": json.dumps(plan)}],
                }
            ],
            "usage": {"input_tokens": 321, "output_tokens": 56, "total_tokens": 377},
        },
    )


@pytest.fixture
def paid_mock(monkeypatch):
    # A fake credential and intercepted transport, never a provider connection.
    monkeypatch.setattr(get_settings(), "openai_api_key", SecretStr("fake-key-for-local-tests"))
    monkeypatch.setattr(get_settings(), "ai_mock_mode", False)
    calls = []

    def install(handler=response_for):
        def send(request):
            calls.append(request)
            return handler(request)

        monkeypatch.setattr(
            runtime,
            "OpenAISelectionAdapter",
            lambda settings: OpenAISelectionAdapter(settings, httpx.MockTransport(send)),
        )

    install()
    return calls, install


def execute(client, identity, run):
    return client.post(f"/v1/workflow-runs/{run['id']}/execute", headers=headers(identity))


def attempts(client, identity, run):
    result = client.get(
        f"/v1/workflow-runs/{run['id']}/text-ai-attempts", headers=headers(identity)
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_real_creator_persists_before_network_and_preserves_approval(client, identities, paid_mock):
    identity = identities[0]
    configure(client, identity)
    run = create(client, identity, execute=False)
    calls, install = paid_mock

    def send(request):
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            attempt = repo.one("text_ai_attempts", workflow_run_id=UUID(run["id"]))
            assert attempt["status"] == "RUNNING"
            assert repo.one("skill_runs", id=attempt["skill_run_id"])["status"] == "RUNNING"
            assert repo.one("cost_events", skill_run_id=attempt["skill_run_id"])["cost"] is None
            assert (
                repo.connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND usename=current_user AND state='idle in transaction'"
                    )
                ).scalar_one()
                == 0
            )
        return response_for(request)

    install(send)
    response = execute(client, identity, run)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["state"] == "AWAITING_APPROVAL"
    row = attempts(client, identity, run)[0]
    assert row["status"] == "SUCCEEDED" and row["execution"]["input_tokens"] == 321
    assert row["execution"]["cost"] is None and row["expected_max_cost"] > 0
    assert "request" not in row and "result" not in row
    data = artifact_data(client, identity, current)
    skill = next(s for s in data["skill_runs"] if s["provider"] == "openai")
    cost = next(c for c in data["cost_events"] if c["provider"] == "openai")
    assert skill["input_tokens"] == cost["input_tokens"] == 321
    assert skill["output_tokens"] == cost["output_tokens"] == 56
    assert skill["cost"] is cost["cost"] is None
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(identity, "APPROVER"),
            json=decision(current),
        ).status_code
        == 200
    )
    assert execute(client, identity, run).json()["state"] == "APPROVED"
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["timeout", "server", "malformed", "crash"])
def test_ambiguous_outcome_is_held_without_paid_replay(client, identities, paid_mock, kind):
    identity = identities[0]
    configure(client, identity)
    run = create(client, identity, execute=False)
    calls, install = paid_mock

    class Interrupted(BaseException):
        pass

    def send(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("never expose secret", request=request)
        if kind == "crash":
            raise Interrupted()
        return httpx.Response(503 if kind == "server" else 200, content=b"truncated")

    install(send)
    if kind == "crash":
        with pytest.raises(Interrupted):
            Runner(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]).execute(
                UUID(run["id"])
            )
        assert attempts(client, identity, run)[0]["status"] == "RUNNING"
    else:
        assert execute(client, identity, run).json()["state"] == "FAILED"
    assert execute(client, identity, run).json()["state"] == "FAILED"
    row = attempts(client, identity, run)[0]
    assert row["status"] == "UNKNOWN_OUTCOME" and not row["retryable"]
    assert row["execution"] is None or row["execution"]["cost"] is None
    assert len(calls) == 1
    assert not artifact_data(client, identity, run)["content_asset_versions"]


def test_explicit_rate_rejection_resumes_once_after_persisted_backoff(
    client, identities, paid_mock
):
    identity = identities[0]
    configure(client, identity)
    run = create(client, identity, execute=False)
    calls, install = paid_mock
    install(lambda request: httpx.Response(429, headers={"retry-after": "0"}))
    assert execute(client, identity, run).json()["state"] == "CONTENT_GENERATING"
    row = attempts(client, identity, run)[0]
    assert row["retryable"] and row["retry_at"]
    assert execute(client, identity, run).json()["state"] == "CONTENT_GENERATING"
    assert len(calls) == 1
    time.sleep(2.05)
    install()
    assert execute(client, identity, run).json()["state"] == "AWAITING_APPROVAL"
    assert [a["status"] for a in attempts(client, identity, run)] == ["FAILED", "SUCCEEDED"]
    assert len(artifact_data(client, identity, run)["content_asset_versions"]) == 1
    assert len(calls) == 2


def test_fabricated_selection_records_usage_but_never_commits_claim(client, identities, paid_mock):
    identity = identities[0]
    configure(client, identity)
    calls, install = paid_mock
    install(
        lambda request: response_for(
            request, slides=[{"fact_id": str(uuid4()), "headline_template_id": "fabricated"}]
        )
    )
    run = create(client, identity, execute=False)
    assert execute(client, identity, run).json()["state"] == "FAILED"
    row = attempts(client, identity, run)[0]
    assert row["error_category"] == "INVALID_SELECTION"
    assert row["execution"]["input_tokens"] == 321
    assert not artifact_data(client, identity, run)["content_asset_versions"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "overrides", [{"enabled": False}, {"per_run_usd": "0.000001"}, {"per_day_usd": "0.000001"}]
)
def test_policy_and_budget_reject_before_provider(client, identities, paid_mock, overrides):
    identity = identities[0]
    configure(client, identity, **overrides)
    run = create(client, identity, execute=False)
    response = execute(client, identity, run)
    assert response.status_code == 409, response.text
    assert not paid_mock[0] and not attempts(client, identity, run)


def test_source_attestation_cannot_change_during_model_call(client, identities, paid_mock):
    identity = identities[0]
    configure(client, identity)
    run = create(client, identity, execute=False)

    def send(request):
        response = client.post(
            f"/v1/source-snapshots/{run['source_snapshot_id']}/verify",
            headers=headers(identity, "APPROVER"),
            json={"decision": "REJECTED", "comment": "Source no longer verified"},
        )
        assert response.status_code == 409, response.text
        return response_for(request)

    paid_mock[1](send)
    assert execute(client, identity, run).json()["state"] == "AWAITING_APPROVAL"
    assert attempts(client, identity, run)[0]["status"] == "SUCCEEDED"
    assert (
        artifact_data(client, identity, run)["source_snapshots"][0]["verification_status"]
        == "VERIFIED"
    )


def test_policy_role_and_tenant_boundaries_and_immutable_attempt(client, identities, paid_mock):
    a, b = identities
    assert client.post("/v1/text-ai-policy", headers=headers(a), json=policy()).status_code == 403
    configure(client, a)
    run = create(client, a, execute=False)
    assert execute(client, a, run).status_code == 200
    row = attempts(client, a, run)[0]
    assert (
        client.get(
            f"/v1/workflow-runs/{run['id']}/text-ai-attempts", headers=headers(b)
        ).status_code
        == 404
    )
    with transaction(UUID(b["tenant_id"]), b["tokens"]["OPERATOR"]) as repo:
        assert not repo.all("text_ai_attempts", id=UUID(row["id"]))
    with pytest.raises(DBAPIError):
        with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("UPDATE text_ai_attempts SET retryable=true WHERE id=:id"), {"id": row["id"]}
            )
    with pytest.raises(DBAPIError):
        with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("UPDATE cost_events SET input_tokens=0 WHERE skill_run_id=:id"),
                {"id": row["skill_run_id"]},
            )
    with engine().begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM text_ai_attempts")).scalar_one() == 0


def test_mock_mode_remains_zero_cost_even_with_key_and_policy(
    client, identities, paid_mock, monkeypatch
):
    identity = identities[0]
    configure(client, identity)
    monkeypatch.setattr(get_settings(), "ai_mock_mode", True)
    run = create(client, identity)
    assert run["state"] == "AWAITING_APPROVAL"
    assert not paid_mock[0] and not attempts(client, identity, run)
    assert all(c["cost"] == 0 for c in artifact_data(client, identity, run)["cost_events"])


def test_one_call_cap_terminates_even_explicit_rate_rejection(client, identities, paid_mock):
    identity = identities[0]
    configure(client, identity, max_calls_per_run=1)
    paid_mock[1](lambda request: httpx.Response(429))
    run = create(client, identity, execute=False)
    assert execute(client, identity, run).json()["state"] == "FAILED"
    assert not attempts(client, identity, run)[0]["retryable"]
    assert execute(client, identity, run).json()["state"] == "FAILED"
    assert len(paid_mock[0]) == 1


def test_concurrent_workflows_cannot_exceed_tenant_daily_call_budget(client, identities, paid_mock):
    identity = identities[0]
    tenant, token = UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]
    with transaction(tenant, token) as repo:
        count = repo.connection.execute(
            text(
                "SELECT count(*) FROM text_ai_attempts WHERE started_at >= date_trunc('day',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
            )
        ).scalar_one()
    configure(client, identity, max_calls_per_day=count + 1)
    runs = [create(client, identity, execute=False) for _ in range(2)]

    def perform(run):
        try:
            return Runner(tenant, token).execute(UUID(run["id"]))["state"]
        except DBAPIError as error:
            return error.orig.sqlstate

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(perform, runs))
    assert sorted(results) == ["23514", "AWAITING_APPROVAL"]
    assert len(paid_mock[0]) == 1


def test_runtime_cannot_insert_unreserved_openai_telemetry(client, identities):
    identity = identities[0]
    run = create(client, identity, execute=False)
    with pytest.raises(DBAPIError) as caught:
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.insert(
                "skill_runs",
                workflow_run_id=UUID(run["id"]),
                step_key="forged",
                skill_identifier="content.carousel",
                skill_version="2.0.0",
                input_schema_version=1,
                output_schema_version=1,
                provider="openai",
                model="forged",
                adapter="forged",
                attempt=1,
                input_hash="a" * 64,
                status="RUNNING",
                is_mock=False,
            )
    assert caught.value.orig.sqlstate == "42501"
