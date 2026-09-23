"""No paid calls: service checkpoints and failures with synthetic SQL receipts."""

# Pytest discovers the explicitly imported fixtures; test arguments use those names.
# ruff: noqa: F811

import time
from uuid import UUID, uuid4

import pytest
from conftest import headers
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_media_integration import (  # noqa: F401
    HUMAN_CHECKS,
    aged_media_prices,
    budget,
    complete,
    forbid_media_network,
    media_identities,
    media_parent,
    media_run,
    records,
    reserve,
    row,
    synthetic_result,
)

from app.config import get_settings
from app.media import service
from app.media.files import MediaFileError
from app.media_providers.http import ProviderError
from app.services.workflows import ConflictError


@pytest.fixture
def executor(monkeypatch, media_run, tmp_path):
    identity, _, run_id = media_run
    settings = get_settings().model_copy(
        update={
            "media_live_enabled": True,
            "media_storage_path": tmp_path / "media",
            "elevenlabs_api_key": SecretStr("test-not-a-real-key"),
            "heygen_api_key": SecretStr("test-not-a-real-key"),
        }
    )
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(service.shutil, "which", lambda _: "test-executable")
    calls = []

    def invoke(directory, saved, job, *args):
        # Separate connection observes a committed RUNNING attempt before provider work.
        assert row(identity, "media_jobs", job["id"])["status"] == "RUNNING"
        assert row(identity, "skill_runs", job["skill_run_id"])["status"] == "RUNNING"
        calls.append(job["stage"])
        return synthetic_result(identity, run_id, job["stage"])

    monkeypatch.setattr(service, "_invoke", invoke)
    return calls


def execute(client, identity, run_id):
    response = client.post(f"/v1/media-runs/{run_id}/execute", headers=headers(identity))
    assert response.status_code == 200, response.text
    return response.json()


def ready(client, media_run, executor, database):
    identity, _, run_id = media_run
    first = execute(client, identity, run_id)
    assert first["status"] == "AVATAR_PENDING"
    with database.begin() as conn:
        conn.execute(
            text("UPDATE media_runs SET next_poll_at=now()-interval '1 second' WHERE id=:id"),
            {"id": run_id},
        )
    assert execute(client, identity, run_id)["status"] == "AVATAR_READY"
    return execute(client, identity, run_id)


def test_service_resumes_only_committed_checkpoints_and_redacts_provider_urls(
    client, media_run, executor, database
):
    identity, workflow, run_id = media_run
    final = ready(client, media_run, executor, database)
    assert final["status"] == "AWAITING_APPROVAL"
    assert executor == [
        "SPEECH",
        "IMAGE_UPLOAD",
        "AUDIO_UPLOAD",
        "AVATAR_SUBMIT",
        "AVATAR_POLL",
        "COMPOSE",
    ]
    assert final["approvals"] == []
    assert "never-fetched" not in str(final)
    history = client.get(f"/v1/workflow-runs/{workflow['id']}/artifacts", headers=headers(identity))
    assert "never-fetched" not in history.text
    execute(client, identity, run_id)
    assert len(executor) == 6
    assert len(records(identity, "media_jobs", run_id)) == 6


def test_execution_disabled_without_credentials_before_reservation(client, media_run, monkeypatch):
    identity, _, run_id = media_run
    monkeypatch.setattr(service, "dependencies", lambda: {"live_enabled": False})
    response = client.post(f"/v1/media-runs/{run_id}/execute", headers=headers(identity))
    assert response.status_code == 409
    assert records(identity, "media_jobs", run_id) == []


def test_unsupported_portrait_fails_before_any_paid_reservation(
    client, media_run, executor, monkeypatch
):
    identity, _, run_id = media_run

    def unsupported(*args):
        raise ConflictError("Speaking media requires an intact pinned PNG or JPEG portrait")

    monkeypatch.setattr(service, "_portrait", unsupported)
    assert (
        client.post(f"/v1/media-runs/{run_id}/execute", headers=headers(identity)).status_code
        == 409
    )
    assert records(identity, "media_jobs", run_id) == [] and executor == []


def test_transient_compose_download_resumes_without_repeating_paid_stages(
    client, media_run, executor, monkeypatch, database
):
    identity, _, run_id = media_run
    original = service._invoke
    compose_attempts = 0

    def unavailable_once(directory, saved, job, *args):
        nonlocal compose_attempts
        if job["stage"] == "COMPOSE":
            compose_attempts += 1
            if compose_attempts == 1:
                raise service.files.MediaRetrievalError(retry_after_seconds=1)
        return original(directory, saved, job, *args)

    monkeypatch.setattr(service, "_invoke", unavailable_once)
    assert ready(client, media_run, executor, database)["status"] == "AVATAR_READY"
    failed = next(
        job for job in records(identity, "media_jobs", run_id) if job["stage"] == "COMPOSE"
    )
    assert failed["status"] == "FAILED" and failed["retryable"] and failed["actual_cost"] == 0
    time.sleep(1.05)
    assert execute(client, identity, run_id)["status"] == "AWAITING_APPROVAL"
    assert (
        compose_attempts == 2 and executor.count("SPEECH") == executor.count("AVATAR_SUBMIT") == 1
    )


@pytest.mark.parametrize(
    "category,retryable,state",
    [
        ("UNKNOWN_OUTCOME", False, "UNKNOWN_OUTCOME"),
        ("AUTHENTICATION", False, "FAILED"),
        ("RATE_LIMITED", True, "CREATED"),
    ],
)
def test_provider_failure_is_persisted_without_secret_or_duplicate(
    client, media_run, executor, monkeypatch, category, retryable, state
):
    identity, _, run_id = media_run

    def fail(*args):
        raise ProviderError(
            category,
            retryable,
            request_id="safe-provider-correlation-123",
            retry_after_seconds=120 if retryable else None,
        )

    monkeypatch.setattr(service, "_invoke", fail)
    failed = execute(client, identity, run_id)
    assert failed["status"] == state
    jobs = records(identity, "media_jobs", run_id)
    assert len(jobs) == 1
    assert jobs[0]["actual_cost"] is None
    assert jobs[0]["failure_request_id"] == "safe-provider-correlation-123"
    assert failed["jobs"][0]["failure_request_id"] == "safe-provider-correlation-123"
    if retryable:
        assert (jobs[0]["retry_at"] - jobs[0]["ended_at"]).total_seconds() >= 120
        assert (
            client.post(f"/v1/media-runs/{run_id}/execute", headers=headers(identity)).status_code
            == 409
        )
    else:
        assert execute(client, identity, run_id)["status"] == state
    assert len(records(identity, "media_jobs", run_id)) == 1


def test_interrupted_generation_without_receipt_is_held(client, media_run, executor):
    identity, _, run_id = media_run
    reserve(identity, run_id, "SPEECH")
    result = execute(client, identity, run_id)
    assert result["status"] == "UNKNOWN_OUTCOME" and executor == []


def test_database_checkpoint_outage_recovers_receipt_without_paid_replay(
    client, media_run, executor, monkeypatch
):
    identity, _, run_id = media_run
    original = service._finish
    calls = 0

    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DBAPIError("checkpoint", {}, RuntimeError("temporary test outage"))
        return original(*args)

    monkeypatch.setattr(service, "_finish", interrupted)
    response = client.post(f"/v1/media-runs/{run_id}/execute", headers=headers(identity))
    assert response.status_code == 503
    assert records(identity, "media_jobs", run_id)[0]["status"] == "RUNNING"
    assert execute(client, identity, run_id)["status"] == "SPEECH_READY"
    assert executor == ["SPEECH"]


def test_corrupt_recovery_receipt_does_not_strand_running_job(client, media_run, executor):
    identity, _, run_id = media_run
    job = row(identity, "media_jobs", reserve(identity, run_id, "SPEECH"))
    directory = service.files.media_directory(
        service.get_settings().media_storage_path, UUID(identity["tenant_id"]), run_id
    )
    (directory / f"receipt-{job['id']}.json").write_bytes(b'{"partial":')
    assert execute(client, identity, run_id)["status"] == "UNKNOWN_OUTCOME"
    assert executor == []
    assert row(identity, "media_jobs", job["id"])["status"] == "UNKNOWN_OUTCOME"


def test_interrupted_generation_with_receipt_recovers_without_provider_replay(
    client, media_run, executor
):
    identity, _, run_id = media_run
    job = row(identity, "media_jobs", reserve(identity, run_id, "SPEECH"))
    directory = service.files.media_directory(
        service.get_settings().media_storage_path, UUID(identity["tenant_id"]), run_id
    )
    service._receipt(directory, job, synthetic_result(identity, run_id, "SPEECH"))
    result = execute(client, identity, run_id)
    assert result["status"] == "SPEECH_READY" and executor == []
    assert row(identity, "media_jobs", job["id"])["status"] == "SUCCEEDED"


def test_expired_prices_preserve_receipt_recovery_without_new_paid_reservations(
    client, media_run, executor, database
):
    identity, _, run_id = media_run
    job = row(identity, "media_jobs", reserve(identity, run_id, "SPEECH"))
    directory = service.files.media_directory(
        service.get_settings().media_storage_path, UUID(identity["tenant_id"]), run_id
    )
    service._receipt(directory, job, synthetic_result(identity, run_id, "SPEECH"))
    with aged_media_prices(database):
        assert execute(client, identity, run_id)["status"] == "SPEECH_READY"
    assert executor == []
    assert len(records(identity, "media_jobs", run_id)) == 1
    assert row(identity, "media_jobs", job["id"])["status"] == "SUCCEEDED"


def test_review_checks_actual_bytes_and_export_needs_exact_approval(
    client, media_run, executor, database, monkeypatch
):
    identity, _, run_id = media_run
    final = ready(client, media_run, executor, database)
    payload = {
        "manifest_hash": final["manifest_hash"],
        "decision": "APPROVE",
        "checks": HUMAN_CHECKS,
    }
    assert (
        client.post(
            f"/v1/media-runs/{run_id}/review", headers=headers(identity, "APPROVER"), json=payload
        ).status_code
        == 409
    )
    assert records(identity, "media_approval_records", run_id) == []
    checked = []

    def validated(directory, manifest):
        checked.append(manifest["video_sha256"])
        return b"test-decoded-mp4"

    monkeypatch.setattr(service.files, "validate_final", validated)
    assert (
        client.get(
            f"/v1/media-runs/{run_id}/video?export=true", headers=headers(identity)
        ).status_code
        == 409
    )
    assert (
        client.get(f"/v1/media-runs/{run_id}/video", headers=headers(identity)).status_code == 200
    )
    assert (
        client.post(
            f"/v1/media-runs/{run_id}/review", headers=headers(identity), json=payload
        ).status_code
        == 403
    )
    approved = client.post(
        f"/v1/media-runs/{run_id}/review", headers=headers(identity, "APPROVER"), json=payload
    )
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    assert len(checked) == 2
    assert (
        client.get(
            f"/v1/media-runs/{run_id}/video?export=true", headers=headers(identity)
        ).status_code
        == 200
    )

    def corrupted(*args):
        raise MediaFileError("MEDIA_INTEGRITY_FAILED")

    monkeypatch.setattr(service.files, "validate_final", corrupted)
    assert (
        client.get(
            f"/v1/media-runs/{run_id}/video?export=true", headers=headers(identity)
        ).status_code
        == 409
    )


def test_media_api_auth_scope_idempotency_and_input_contracts(client, media_run, media_identities):
    identity, workflow, run_id = media_run
    saved = row(identity, "media_runs", run_id)
    assert client.get("/v1/media-dependencies").status_code == 401
    assert (
        client.get(f"/v1/media-runs/{run_id}", headers=headers(media_identities[1])).status_code
        == 404
    )
    payload = {
        "profile_id": str(saved["profile_id"]),
        "selected_paths": saved["selected_paths"],
        "idempotency_key": saved["idempotency_key"],
    }
    route = f"/v1/workflow-runs/{workflow['id']}/media-runs"
    assert client.post(route, headers=headers(identity), json=payload).json()["id"] == str(run_id)
    payload["selected_paths"] = ["cta"]
    assert client.post(route, headers=headers(identity), json=payload).status_code == 409
    payload["idempotency_key"] = str(uuid4())
    payload["selected_paths"] = ["caption", "caption"]
    assert client.post(route, headers=headers(identity), json=payload).status_code == 422
