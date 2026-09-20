"""Dry-run delivery contracts: exact approvals, private bytes, and no platform calls."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from threading import Event
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import artifact_data, create, headers, revise
from fastapi.encoders import jsonable_encoder
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from test_visual_workflow import (
    approve_content,
    approve_visual,
    render,
)
from test_visual_workflow import (
    export as visual_export,
)
from test_visual_workflow import (
    passing_render as _passing_render,
)
from test_visual_workflow import (
    visual_context as _visual_context,
)

from app.db.repository import canonical_hash, engine, transaction
from app.delivery import adapter
from app.delivery.schemas import DeliveryInput
from app.delivery.seed import seed_delivery_targets
from app.delivery.service import create_delivery, execute_delivery

visual_context = _visual_context
passing_render = _passing_render


@pytest.fixture
def approved_visual(client, passing_render):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    visual = approve_visual(client, identity, rendered)
    return identity, run, rendered, visual


@pytest.fixture
def forbid_platform_http(monkeypatch):
    attempts = []

    def deny_sync(transport, request):
        attempts.append(str(request.url))
        raise AssertionError("Dry-run delivery attempted an external HTTP request")

    async def deny_async(transport, request):
        attempts.append(str(request.url))
        raise AssertionError("Dry-run delivery attempted an external HTTP request")

    # TestClient uses its own in-process transport; PostgreSQL uses psycopg.
    # Guard the actual network transports without breaking either local boundary.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async)
    return attempts


@pytest.fixture
def delivery_targets(database, visual_context):
    result = []
    for identity in (visual_context["a"], visual_context["b"]):
        result.append(seed_delivery_targets(database, UUID(identity["tenant_id"])))
    return result


def delivery_request(target_id, key=None):
    return {"target_id": str(target_id), "idempotency_key": key or str(uuid4()), "mode": "DRY_RUN"}


def deliver(client, identity, rendered, target_id, key=None):
    response = client.post(
        f"/v1/renders/{rendered['id']}/deliveries",
        headers=headers(identity),
        json=delivery_request(target_id, key),
    )
    assert response.status_code == 200, response.text
    return response.json()


def delivery_worker(identity, rendered, target_id):
    # Exercise independent persisted workers, not TestClient's shared ASGI event loop.
    request = DeliveryInput.model_validate_json(json.dumps(delivery_request(target_id)))
    return jsonable_encoder(
        create_delivery(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(rendered["id"]),
            request,
        )
    )


def start_only(identity, rendered, target_id, key=None):
    request = delivery_request(target_id, key)
    digest = canonical_hash(
        {
            "render_run_id": rendered["id"],
            "target_id": str(target_id),
            "manifest_hash": rendered["manifest_hash"],
            "mode": "DRY_RUN",
            "adapter_version": "dry-run-v1",
        }
    )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        delivery_id = repo.connection.execute(
            text("SELECT start_delivery(:render,:target,:key,:hash)"),
            {
                "render": UUID(rendered["id"]),
                "target": target_id,
                "key": request["idempotency_key"],
                "hash": digest,
            },
        ).scalar_one()
    return delivery_id


def delivery_attempts(identity, workflow_id):
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        return [
            row
            for row in repo.all("skill_runs", workflow_run_id=UUID(workflow_id))
            if row["skill_identifier"].startswith("delivery.")
        ]


def test_delivery_dry_run_uses_exact_approvals_and_never_calls_a_platform(
    client, approved_visual, delivery_targets, forbid_platform_http
):
    identity, run, rendered, visual = approved_visual
    delivered = deliver(client, identity, rendered, delivery_targets[0])
    assert delivered["status"] == "DRY_RUN_COMPLETE"
    assert delivered["mode"] == "DRY_RUN"
    for field in ("workflow_run_id", "asset_version_id", "research_version_id", "qa_report_id"):
        assert delivered[field] == (run["id"] if field == "workflow_run_id" else run[field])
    assert delivered["render_run_id"] == rendered["id"]
    assert delivered["manifest_hash"] == rendered["manifest_hash"]
    assert delivered["visual_approval_record_id"] == visual["visual_approval_record_id"]
    assert delivered["content_approval_record_id"]
    assert canonical_hash(delivered["payload"]) == delivered["payload_hash"]
    assert canonical_hash(delivered["receipt"]) == delivered["receipt_hash"]
    assert delivered["receipt"]["network_performed"] is False
    assert delivered["receipt"]["post_id"] is None and delivered["receipt"]["published_at"] is None
    assert delivered["payload"]["post_id"] is None
    assert delivered["receipt"]["payload_sha256"] == delivered["payload_hash"]
    package = visual_export(client, identity, rendered)
    assert package.status_code == 200
    assert hashlib.sha256(package.content).hexdigest() == delivered["receipt"]["package_sha256"]
    assert forbid_platform_http == []
    attempts = delivery_attempts(identity, run["id"])
    assert len(attempts) == 1 and attempts[0]["status"] == "SUCCEEDED"
    assert attempts[0]["provider"] == "mock" and attempts[0]["is_mock"]
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        costs = repo.all("cost_events", skill_run_id=attempts[0]["id"])
        approval = repo.one("approval_records", id=UUID(delivered["content_approval_record_id"]))
    assert costs and all(
        row["cost"] == 0 and row["input_tokens"] == row["output_tokens"] == 0 for row in costs
    )
    assert approval["decision"] == "APPROVE"
    assert str(approval["asset_version_id"]) == run["asset_version_id"]
    fetched = client.get(f"/v1/deliveries/{delivered['id']}", headers=headers(identity))
    assert (
        fetched.status_code == 200 and fetched.json()["receipt_hash"] == delivered["receipt_hash"]
    )
    history = client.get(f"/v1/workflow-runs/{run['id']}/deliveries", headers=headers(identity))
    assert history.status_code == 200
    rows = history.json()
    assert len(rows) == 1 and rows[0]["id"] == delivered["id"]
    assert len(rows[0]["attempts"]) == 1
    current = client.get(f"/v1/workflow-runs/{run['id']}", headers=headers(identity)).json()
    assert current["state"] == "APPROVED", "A rehearsal must not invent a published workflow state"


def test_delivery_live_mode_and_missing_authentication_fail_closed(
    client, approved_visual, delivery_targets, forbid_platform_http
):
    identity, _, rendered, _ = approved_visual
    assert client.get("/v1/delivery-targets").status_code == 401
    request = {**delivery_request(delivery_targets[0]), "mode": "LIVE"}
    assert (
        client.post(
            f"/v1/renders/{rendered['id']}/deliveries", headers=headers(identity), json=request
        ).status_code
        == 422
    )
    assert forbid_platform_http == []


def test_cross_tenant_delivery_access_references_and_mutation_are_rejected(
    client, approved_visual, delivery_targets, visual_context
):
    owner, run, rendered, _ = approved_visual
    delivered = deliver(client, owner, rendered, delivery_targets[0])
    outsider = visual_context["b"]
    assert (
        client.get(f"/v1/deliveries/{delivered['id']}", headers=headers(outsider)).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/deliveries/{delivered['id']}/execute", headers=headers(outsider)
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/v1/workflow-runs/{run['id']}/deliveries", headers=headers(outsider)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/renders/{rendered['id']}/deliveries",
            headers=headers(outsider),
            json=delivery_request(delivery_targets[1]),
        ).status_code
        == 404
    )
    assert client.post(
        f"/v1/renders/{rendered['id']}/deliveries",
        headers=headers(owner),
        json=delivery_request(delivery_targets[1]),
    ).status_code in (404, 409)
    with transaction(UUID(outsider["tenant_id"]), outsider["tokens"]["OPERATOR"]) as repo:
        assert (
            repo.connection.execute(
                text("SELECT * FROM delivery_runs WHERE id=:id"), {"id": UUID(delivered["id"])}
            ).all()
            == []
        )
        assert (
            repo.connection.execute(
                text("SELECT * FROM delivery_targets WHERE id=:id"), {"id": delivery_targets[0]}
            ).all()
            == []
        )
    with pytest.raises(DBAPIError) as rejected:
        with transaction(UUID(outsider["tenant_id"]), outsider["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("SELECT fail_delivery(:id,'POLICY_BLOCKED',false)"),
                {"id": UUID(delivered["id"])},
            )
    assert getattr(rejected.value.orig, "sqlstate", None) == "P0002"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE delivery_runs SET status='DRY_RUN_COMPLETE' WHERE id=:id",
        "DELETE FROM delivery_runs WHERE id=:id",
        "INSERT INTO delivery_runs OVERRIDING SYSTEM VALUE SELECT * FROM delivery_runs WHERE id=:id",
        "UPDATE delivery_targets SET enabled=true WHERE id=:target",
        "DELETE FROM delivery_targets WHERE id=:target",
    ],
)
def test_runtime_cannot_write_delivery_state_or_target_configuration(
    client, approved_visual, delivery_targets, statement
):
    identity, _, rendered, _ = approved_visual
    delivered = deliver(client, identity, rendered, delivery_targets[0])
    with pytest.raises(DBAPIError) as rejected:
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text(statement), {"id": UUID(delivered["id"]), "target": delivery_targets[0]}
            )
    assert getattr(rejected.value.orig, "sqlstate", None) == "42501"


def test_delivery_requires_both_content_and_visual_approval(
    client, passing_render, delivery_targets
):
    identity, run, rendered = passing_render
    request = delivery_request(delivery_targets[0])
    assert (
        client.post(
            f"/v1/renders/{rendered['id']}/deliveries", headers=headers(identity), json=request
        ).status_code
        == 409
    )
    approve_content(client, identity, run)
    assert (
        client.post(
            f"/v1/renders/{rendered['id']}/deliveries", headers=headers(identity), json=request
        ).status_code
        == 409
    )
    approve_visual(client, identity, rendered)
    assert deliver(client, identity, rendered, delivery_targets[0])["status"] == "DRY_RUN_COMPLETE"


@pytest.mark.parametrize("block", [False, True])
def test_changed_or_blocked_content_cannot_reuse_old_delivery_approvals(
    client, approved_visual, delivery_targets, block
):
    identity, run, rendered, _ = approved_visual
    draft = artifact_data(client, identity, run)["content_asset_versions"][0]["payload"]
    if block:
        draft["slides"][0]["body"]["text"] = "An unsupported promise with no matching evidence."
    newer = revise(client, identity, run, draft)
    assert newer["state"] == ("BLOCKED" if block else "AWAITING_APPROVAL")
    response = client.post(
        f"/v1/renders/{rendered['id']}/deliveries",
        headers=headers(identity),
        json=delivery_request(delivery_targets[0]),
    )
    assert response.status_code == 409


def test_corrupt_bytes_cannot_complete_delivery(
    client, approved_visual, delivery_targets, visual_context
):
    identity, _, rendered, _ = approved_visual
    image = (
        visual_context["storage"]
        / identity["tenant_id"]
        / rendered["id"]
        / "files"
        / "slide-01.png"
    )
    image.write_bytes(image.read_bytes() + b"tampered after approval")
    response = client.post(
        f"/v1/renders/{rendered['id']}/deliveries",
        headers=headers(identity),
        json=delivery_request(delivery_targets[0]),
    )
    assert response.status_code == 200
    assert (
        response.json()["status"] == "FAILED"
        and response.json()["error_category"] == "PACKAGE_INVALID"
    )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        rows = repo.all("delivery_runs", render_run_id=UUID(rendered["id"]))
    assert not any(
        row["status"] == "DRY_RUN_COMPLETE" or row["receipt"] is not None for row in rows
    )


def test_delivery_idempotency_and_conflicting_payload(
    client, approved_visual, delivery_targets, visual_context
):
    identity, run, rendered, _ = approved_visual
    key = str(uuid4())
    first = deliver(client, identity, rendered, delivery_targets[0], key)
    repeated = deliver(client, identity, rendered, delivery_targets[0], key)
    assert first["id"] == repeated["id"] and first["receipt_hash"] == repeated["receipt_hash"]
    assert len(delivery_attempts(identity, run["id"])) == 1
    newer = render(client, identity, run, visual_context["configs"][0])
    approve_visual(client, identity, newer)
    conflict = client.post(
        f"/v1/renders/{newer['id']}/deliveries",
        headers=headers(identity),
        json=delivery_request(delivery_targets[0], key),
    )
    assert conflict.status_code == 409


def test_concurrent_same_key_creates_one_delivery_run(client, approved_visual, delivery_targets):
    identity, _, rendered, _ = approved_visual
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [
            pool.submit(start_only, identity, rendered, delivery_targets[0], key) for _ in range(2)
        ]
        ids = [request.result() for request in requests]
    assert ids[0] == ids[1]
    resumed = client.post(f"/v1/deliveries/{ids[0]}/execute", headers=headers(identity))
    assert resumed.status_code == 200 and resumed.json()["status"] == "DRY_RUN_COMPLETE", (
        resumed.text
    )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert len(repo.all("delivery_runs", idempotency_key=key)) == 1


def test_created_delivery_resumes_without_duplicate_success(
    client, approved_visual, delivery_targets
):
    identity, run, rendered, _ = approved_visual
    delivery_id = start_only(identity, rendered, delivery_targets[0])
    before = client.get(f"/v1/deliveries/{delivery_id}", headers=headers(identity)).json()
    assert before["status"] == "CREATED" and before["attempt_count"] == 0
    first = execute_delivery(
        UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], delivery_id
    )
    second = execute_delivery(
        UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], delivery_id
    )
    assert first["status"] == "DRY_RUN_COMPLETE" and first["receipt_hash"] == second["receipt_hash"]
    assert len(delivery_attempts(identity, run["id"])) == 1


def test_interrupted_delivery_resumes_from_persisted_attempt(
    client, approved_visual, delivery_targets, monkeypatch
):
    identity, run, rendered, _ = approved_visual
    original = adapter.prepare_delivery

    def interrupted(*args, **kwargs):
        raise SystemExit("Simulated stopped delivery process")

    monkeypatch.setattr(adapter, "prepare_delivery", interrupted)
    request = DeliveryInput.model_validate_json(json.dumps(delivery_request(delivery_targets[0])))
    with pytest.raises(SystemExit):
        create_delivery(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(rendered["id"]),
            request,
        )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        stored = repo.one("delivery_runs", render_run_id=UUID(rendered["id"]))
    assert stored["status"] == "VALIDATING" and stored["receipt"] is None
    monkeypatch.setattr(adapter, "prepare_delivery", original)
    resumed = execute_delivery(
        UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], stored["id"]
    )
    assert resumed["status"] == "DRY_RUN_COMPLETE"
    attempts = delivery_attempts(identity, run["id"])
    assert [row["status"] for row in attempts] == ["INTERRUPTED", "SUCCEEDED"]
    assert [row["attempt"] for row in attempts] == [1, 2]


def test_retryable_delivery_failure_has_persisted_backoff_and_one_success(
    client, approved_visual, delivery_targets, database, monkeypatch
):
    identity, run, rendered, _ = approved_visual
    original = adapter.prepare_delivery

    def temporarily_unavailable(*args, **kwargs):
        raise adapter.RetryableDeliveryError("Temporary local package reader failure")

    monkeypatch.setattr(adapter, "prepare_delivery", temporarily_unavailable)
    response = client.post(
        f"/v1/renders/{rendered['id']}/deliveries",
        headers=headers(identity),
        json=delivery_request(delivery_targets[0]),
    )
    assert response.status_code == 200
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        stored = repo.one("delivery_runs", render_run_id=UUID(rendered["id"]))
    assert stored["status"] == "RETRY_WAIT" and stored["attempt_count"] == 1
    assert stored["retry_at"] is not None and stored["receipt"] is None
    with database.begin() as conn:
        conn.execute(
            text("UPDATE delivery_runs SET retry_at=now()+interval '1 hour' WHERE id=:id"),
            {"id": stored["id"]},
        )
    early = client.post(f"/v1/deliveries/{stored['id']}/execute", headers=headers(identity))
    assert early.status_code == 200 and early.json()["status"] == "RETRY_WAIT"
    assert len(delivery_attempts(identity, run["id"])) == 1
    monkeypatch.setattr(adapter, "prepare_delivery", original)
    # Advance only this disposable test row's operational clock instead of sleeping.
    with database.begin() as conn:
        conn.execute(
            text("UPDATE delivery_runs SET retry_at=now()-interval '1 second' WHERE id=:id"),
            {"id": stored["id"]},
        )
    resumed = client.post(f"/v1/deliveries/{stored['id']}/execute", headers=headers(identity))
    assert resumed.status_code == 200 and resumed.json()["status"] == "DRY_RUN_COMPLETE", (
        resumed.text
    )
    attempts = delivery_attempts(identity, run["id"])
    assert [attempt["status"] for attempt in attempts] == ["FAILED", "SUCCEEDED"]
    assert attempts[0]["retryable"] and [attempt["attempt"] for attempt in attempts] == [1, 2]
    again = client.post(f"/v1/deliveries/{stored['id']}/execute", headers=headers(identity))
    assert (
        again.status_code == 200 and again.json()["receipt_hash"] == resumed.json()["receipt_hash"]
    )
    assert len(delivery_attempts(identity, run["id"])) == 2


def test_delivery_retry_attempts_are_bounded(
    client, approved_visual, delivery_targets, database, monkeypatch
):
    identity, run, rendered, _ = approved_visual

    def timeout(*args, **kwargs):
        raise TimeoutError("Local rehearsal timed out")

    monkeypatch.setattr(adapter, "prepare_delivery", timeout)
    delivery_id = start_only(identity, rendered, delivery_targets[0])
    for attempt in range(1, 4):
        response = client.post(f"/v1/deliveries/{delivery_id}/execute", headers=headers(identity))
        assert response.status_code == 200
        stored = client.get(f"/v1/deliveries/{delivery_id}", headers=headers(identity)).json()
        assert stored["attempt_count"] == attempt
        assert stored["status"] == ("RETRY_WAIT" if attempt < 3 else "FAILED")
        assert stored["receipt"] is None
        if attempt < 3:
            with database.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE delivery_runs SET retry_at=now()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": delivery_id},
                )
    assert len(delivery_attempts(identity, run["id"])) == 3
    response = client.post(f"/v1/deliveries/{delivery_id}/execute", headers=headers(identity))
    assert response.status_code in (200, 409)
    assert len(delivery_attempts(identity, run["id"])) == 3


def test_same_delivery_key_is_independent_across_tenants(
    client, approved_visual, delivery_targets, visual_context
):
    owner, _, rendered, _ = approved_visual
    key = str(uuid4())
    first = deliver(client, owner, rendered, delivery_targets[0], key)
    outsider = visual_context["b"]
    run = create(client, outsider)
    other_render = render(client, outsider, run, visual_context["configs"][1])
    approve_content(client, outsider, run)
    approve_visual(client, outsider, other_render)
    second = deliver(client, outsider, other_render, delivery_targets[1], key)
    assert first["id"] != second["id"] and second["status"] == "DRY_RUN_COMPLETE"


@pytest.mark.parametrize(
    "corruption",
    [
        "caption",
        "slide_hash",
        "plan_post_id",
        "network",
        "receipt_post_id",
        "receipt_slides",
        "payload_hash",
        "receipt_hash",
    ],
)
def test_database_rejects_rehashed_delivery_payload_and_receipt_tampering(
    client, approved_visual, delivery_targets, corruption
):
    identity, _, rendered, _ = approved_visual
    successful = deliver(client, identity, rendered, delivery_targets[0])
    delivery_id = start_only(identity, rendered, delivery_targets[0])
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        repo.connection.execute(text("SELECT claim_delivery(:id)"), {"id": delivery_id})
    payload, receipt = copy.deepcopy(successful["payload"]), copy.deepcopy(successful["receipt"])
    receipt["validated_at"] = datetime.now(UTC).isoformat()
    if corruption == "caption":
        payload["caption"]["text"] = "Unsupported promotional claim."
    elif corruption == "slide_hash":
        payload["slides"][0]["sha256"] = "0" * 64
    elif corruption == "plan_post_id":
        payload["post_id"] = "fabricated-platform-post"
    elif corruption == "network":
        receipt["network_performed"] = True
    elif corruption == "receipt_post_id":
        receipt["post_id"] = "fabricated-platform-post"
    elif corruption == "receipt_slides":
        receipt["slide_sha256"] = ["0" * 64]
    # Rehash tampered structures: content validation must add protection beyond hashes.
    payload_hash = canonical_hash(payload)
    receipt["payload_sha256"] = payload_hash
    receipt_hash = canonical_hash(receipt)
    if corruption == "payload_hash":
        payload_hash = "0" * 64
    elif corruption == "receipt_hash":
        receipt_hash = "0" * 64
    with pytest.raises(DBAPIError) as rejected:
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text(
                    "SELECT complete_delivery(:id,CAST(:payload AS jsonb),:phash,CAST(:receipt AS jsonb),:rhash)"
                ),
                {
                    "id": delivery_id,
                    "payload": json.dumps(payload),
                    "phash": payload_hash,
                    "receipt": json.dumps(receipt),
                    "rhash": receipt_hash,
                },
            )
    assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
    stored = client.get(f"/v1/deliveries/{delivery_id}", headers=headers(identity)).json()
    assert stored["status"] == "VALIDATING" and stored["receipt"] is None


def test_new_disabled_target_revision_blocks_queued_and_new_deliveries(
    client, approved_visual, delivery_targets, database, monkeypatch
):
    identity, run, rendered, _ = approved_visual
    queued = start_only(identity, rendered, delivery_targets[0])
    disabled = seed_delivery_targets(database, UUID(identity["tenant_id"]), enabled=False)
    assert disabled != delivery_targets[0]
    called = []
    original = adapter.prepare_delivery

    def track(*args, **kwargs):
        called.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter, "prepare_delivery", track)
    for target_id in (delivery_targets[0], disabled):
        response = client.post(
            f"/v1/renders/{rendered['id']}/deliveries",
            headers=headers(identity),
            json=delivery_request(target_id),
        )
        assert response.status_code == 409
    resumed = client.post(f"/v1/deliveries/{queued}/execute", headers=headers(identity))
    assert resumed.status_code == 200 and resumed.json()["status"] == "BLOCKED", resumed.text
    assert resumed.json()["receipt"] is None and called == []
    assert delivery_attempts(identity, run["id"]) == []


def test_new_content_after_queueing_blocks_delivery_before_adapter(
    client, approved_visual, delivery_targets, monkeypatch
):
    identity, run, rendered, _ = approved_visual
    queued = start_only(identity, rendered, delivery_targets[0])
    draft = artifact_data(client, identity, run)["content_asset_versions"][0]["payload"]
    revise(client, identity, run, draft)

    def forbidden(*args, **kwargs):
        raise AssertionError("Stale approval reached delivery adapter")

    monkeypatch.setattr(adapter, "prepare_delivery", forbidden)
    response = client.post(f"/v1/deliveries/{queued}/execute", headers=headers(identity))
    assert response.status_code == 200 and response.json()["status"] == "BLOCKED", response.text
    assert response.json()["receipt"] is None and delivery_attempts(identity, run["id"]) == []


def test_invalid_adapter_output_is_rejected_without_retry(
    client, approved_visual, delivery_targets, monkeypatch, recwarn
):
    identity, run, rendered, _ = approved_visual
    original = adapter.prepare_delivery

    def false_publish(*args, **kwargs):
        payload, receipt = original(*args, **kwargs)
        # Model copies do not validate updates; the service must validate adapter returns.
        receipt = receipt.model_copy(update={"network_performed": True, "post_id": "invented-post"})
        return payload, receipt

    monkeypatch.setattr(adapter, "prepare_delivery", false_publish)
    delivered = deliver(client, identity, rendered, delivery_targets[0])
    assert delivered["status"] == "FAILED" and delivered["receipt"] is None
    attempts = delivery_attempts(identity, run["id"])
    assert len(attempts) == 1 and attempts[0]["status"] == "FAILED" and not attempts[0]["retryable"]
    repeated = client.post(f"/v1/deliveries/{delivered['id']}/execute", headers=headers(identity))
    assert repeated.status_code == 200 and repeated.json()["status"] == "FAILED"
    assert len(delivery_attempts(identity, run["id"])) == 1
    assert not any("invented-post" in str(warning.message) for warning in recwarn)


def test_delivery_holds_workflow_guard_until_receipt_commits_before_content_revision(
    client, approved_visual, delivery_targets, database, monkeypatch
):
    identity, run, rendered, _ = approved_visual
    draft = artifact_data(client, identity, run)["content_asset_versions"][0]["payload"]
    adapter_entered, release_adapter, revision_submitted = Event(), Event(), Event()
    revision_backend = []
    original = adapter.prepare_delivery

    def paused_preflight(*args, **kwargs):
        adapter_entered.set()
        if not release_adapter.wait(timeout=20):
            raise AssertionError("Test did not release paused preflight")
        return original(*args, **kwargs)

    def capture_revision(conn, cursor, statement, parameters, context, executemany):
        if "begin_revision" in statement:
            revision_backend.append(conn.connection.driver_connection.info.backend_pid)
            revision_submitted.set()

    monkeypatch.setattr(adapter, "prepare_delivery", paused_preflight)
    runtime = engine()
    event.listen(runtime, "before_cursor_execute", capture_revision)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            delivery = pool.submit(delivery_worker, identity, rendered, delivery_targets[0])
            try:
                assert adapter_entered.wait(timeout=15), (
                    "Preflight did not enter its guarded transaction"
                )
                revision = pool.submit(
                    client.post,
                    f"/v1/workflow-runs/{run['id']}/revisions",
                    headers=headers(identity),
                    json=draft,
                )
                assert revision_submitted.wait(timeout=15), (
                    "Revision did not reach its database guard"
                )
                with pytest.raises(FutureTimeoutError):
                    revision.result(timeout=0.25)
                with database.begin() as conn:
                    blockers = conn.execute(
                        text("SELECT pg_blocking_pids(:pid)"),
                        {"pid": revision_backend[0]},
                    ).scalar_one()
                assert blockers, "The concurrent revision must wait on the delivery's held guard"
            finally:
                release_adapter.set()
            completed = delivery.result(timeout=20)
            revised = revision.result(timeout=20)
    finally:
        release_adapter.set()
        event.remove(runtime, "before_cursor_execute", capture_revision)
    assert completed["status"] == "DRY_RUN_COMPLETE"
    assert completed["asset_version_id"] == run["asset_version_id"]
    assert (
        revised.status_code == 200 and revised.json()["asset_version_id"] != run["asset_version_id"]
    )
    audit = client.get(f"/v1/workflow-runs/{run['id']}/audit", headers=headers(identity)).json()
    delivery_event = next(row for row in audit if row["event_type"] == "DELIVERY_DRY_RUN_COMPLETED")
    revision_event = next(
        row
        for row in audit
        if row["event_type"] == "STATE_TRANSITION" and row["from_state"] == "APPROVED"
    )
    assert delivery_event["sequence"] < revision_event["sequence"]
    new_request = client.post(
        f"/v1/renders/{rendered['id']}/deliveries",
        headers=headers(identity),
        json=delivery_request(delivery_targets[0]),
    )
    assert new_request.status_code == 409
    historical = client.get(f"/v1/deliveries/{completed['id']}", headers=headers(identity)).json()
    assert (
        historical["status"] == "DRY_RUN_COMPLETE"
        and historical["receipt_hash"] == completed["receipt_hash"]
    )


def test_target_disable_and_delivery_completion_serialize_without_stale_success(
    client, approved_visual, delivery_targets, database, monkeypatch
):
    identity, _, rendered, _ = approved_visual
    adapter_entered, release_adapter, disable_submitted = Event(), Event(), Event()
    disable_backend = []
    original = adapter.prepare_delivery

    def paused_preflight(*args, **kwargs):
        adapter_entered.set()
        if not release_adapter.wait(timeout=20):
            raise AssertionError("Test did not release paused preflight")
        return original(*args, **kwargs)

    def capture_target_lock(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            disable_backend.append(conn.connection.driver_connection.info.backend_pid)
            disable_submitted.set()

    monkeypatch.setattr(adapter, "prepare_delivery", paused_preflight)
    event.listen(database, "before_cursor_execute", capture_target_lock)
    disabled_before_completion = False
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            delivery = pool.submit(delivery_worker, identity, rendered, delivery_targets[0])
            try:
                assert adapter_entered.wait(timeout=15), (
                    "Preflight did not enter its guarded transaction"
                )
                disable = pool.submit(
                    seed_delivery_targets, database, UUID(identity["tenant_id"]), enabled=False
                )
                assert disable_submitted.wait(timeout=15), (
                    "Target update did not reach its version lock"
                )
                try:
                    disabled_id = disable.result(timeout=0.25)
                    disabled_before_completion = True
                except FutureTimeoutError:
                    with database.begin() as conn:
                        blockers = conn.execute(
                            text("SELECT pg_blocking_pids(:pid)"),
                            {"pid": disable_backend[0]},
                        ).scalar_one()
                    assert blockers, "Pending target update must be serialized by a database lock"
            finally:
                release_adapter.set()
            completed = delivery.result(timeout=20)
            disabled_id = disable.result(timeout=20)
    finally:
        release_adapter.set()
        event.remove(database, "before_cursor_execute", capture_target_lock)
    assert disabled_id != delivery_targets[0]
    with database.begin() as conn:
        target = (
            conn.execute(text("SELECT * FROM delivery_targets WHERE id=:id"), {"id": disabled_id})
            .mappings()
            .one()
        )
    assert not target["enabled"]
    if disabled_before_completion:
        assert completed["status"] == "BLOCKED" and completed["receipt"] is None
    elif completed["status"] == "DRY_RUN_COMPLETE":
        # If completion held the target lock, its receipt must predate the new version.
        assert datetime.fromisoformat(completed["ended_at"]) <= target["created_at"]
    else:
        assert completed["status"] == "BLOCKED" and completed["receipt"] is None
    for target_id in (delivery_targets[0], disabled_id):
        request = client.post(
            f"/v1/renders/{rendered['id']}/deliveries",
            headers=headers(identity),
            json=delivery_request(target_id),
        )
        assert request.status_code == 409
