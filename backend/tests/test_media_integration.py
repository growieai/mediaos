"""Disposable SQL media contracts with synthetic receipts and no provider calls.

These tests prove guards, reservations and lineage, not real synthesized audio,
provider billing, lip sync or decoded video quality. Runtime byte checks have
separate tests. No generated receipt in this file represents production media.
"""

import hashlib
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import artifact_data, create, headers, revise
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_community_unit import character_config
from test_visual_workflow import approve_content

from app.config import REPO_ROOT, get_settings
from app.db.repository import canonical_hash, engine, transaction
from app.rendering.seed import seed_visual_config
from app.seed import seed

TABLES = (
    "media_profiles",
    "media_spend_policies",
    "media_runs",
    "media_jobs",
    "media_approval_records",
)
HUMAN_CHECKS = dict.fromkeys(("identity", "voice", "lip_sync", "captions", "disclosure"), True)


@pytest.fixture(autouse=True)
def forbid_media_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("SQL media tests must never call a paid provider")

    async def deny_async(*args, **kwargs):
        deny(*args, **kwargs)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async)
    monkeypatch.setattr(get_settings(), "media_live_enabled", False)
    # SQL tests do not create media; keep the missing-file API check isolated from
    # local production artifacts without depending on pytest's private temp ACLs.
    monkeypatch.setattr(
        get_settings(),
        "media_storage_path",
        REPO_ROOT / ".local" / "media-sql-tests" / str(uuid4()),
    )


@pytest.fixture
def media_identities(database):
    result = []
    for _ in range(2):
        slug = f"media-guard-test-{uuid4()}"
        tokens = {role: secrets.token_urlsafe(32) for role in ("OPERATOR", "APPROVER", "ADMIN")}
        ids = seed(
            database,
            slug,
            "Test team",
            "Virtual assistant",
            "Verified notes",
            character_config(),
            tokens,
        )
        visual_id = seed_visual_config(
            database,
            ids["tenant_id"],
            ids["influencer_id"],
            "Test reference",
            "This is an AI creator.",
            reference_path="characters/sofia/references/v1/portrait.png",
            reference_metadata={"environment": "DISPOSABLE_TEST", "synthetic_receipts": True},
        )
        result.append({**ids, "tokens": tokens, "visual_id": visual_id})
    return result


def sql(identity, statement, parameters=None, role="OPERATOR"):
    with transaction(UUID(identity["tenant_id"]), identity["tokens"][role]) as repo:
        return repo.connection.execute(text(statement), parameters or {}).scalar_one()


def row(identity, table, row_id):
    assert table in TABLES or table in ("skill_runs", "cost_events", "visual_config_versions")
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        return dict(
            repo.connection.execute(
                text(f"SELECT * FROM {table} WHERE id=:id"), {"id": UUID(str(row_id))}
            )
            .mappings()
            .one()
        )


def records(identity, table, run_id):
    assert table in ("media_jobs", "media_approval_records")
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        return [
            dict(value)
            for value in repo.connection.execute(
                text(f"SELECT * FROM {table} WHERE media_run_id=:id ORDER BY id"),
                {"id": UUID(str(run_id))},
            ).mappings()
        ]


def profile_payload(**changes):
    return {
        "schema_version": 1,
        "voice_id": "test_voice_identifier",
        "tts_model": "eleven_multilingual_v2",
        "presenter_provider": "heygen",
        "tts_usd_per_1000_characters": "1.0",
        "avatar_usd_per_second": "0.1",
        "price_reference": "Synthetic test rate, not a provider price assertion.",
        "price_checked_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        **changes,
    }


def profile(identity, payload=None, role="ADMIN", visual_id=None):
    return sql(
        identity,
        "SELECT create_media_profile(:influencer,:visual,CAST(:payload AS jsonb))",
        {
            "influencer": UUID(identity["influencer_id"]),
            "visual": visual_id or identity["visual_id"],
            "payload": json.dumps(payload or profile_payload()),
        },
        role,
    )


def budget_payload(**changes):
    return {
        "schema_version": 1,
        "per_run_usd": "10.0",
        "per_day_usd": "100.0",
        "enabled": True,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        **changes,
    }


def budget(identity, payload=None, role="ADMIN"):
    return sql(
        identity,
        "SELECT set_media_spend_policy(CAST(:payload AS jsonb))",
        {
            "payload": json.dumps(payload or budget_payload()),
        },
        role,
    )


def start(identity, workflow, profile_id, paths=None, key=None):
    paths = ["slides.0.body"] if paths is None else paths
    return sql(
        identity,
        "SELECT start_media_run(:workflow,:profile,CAST(:paths AS jsonb),:key,:hash)",
        {
            "workflow": UUID(workflow["id"]),
            "profile": profile_id,
            "paths": json.dumps(paths),
            "key": key or str(uuid4()),
            "hash": canonical_hash(
                {
                    "workflow_run_id": workflow["id"],
                    "profile_id": str(profile_id),
                    "selected_paths": paths,
                }
            ),
        },
    )


def reserve(identity, run_id, stage, **changes):
    saved = row(identity, "media_runs", run_id)
    payload = {"schema_version": 1, "script_hash": saved["script_hash"], **changes}
    return sql(
        identity,
        "SELECT reserve_media_job(:run,:stage,CAST(:input AS jsonb))",
        {
            "run": run_id,
            "stage": stage,
            "input": json.dumps(payload),
        },
    )


def finish(identity, job_id, result):
    return sql(
        identity,
        "SELECT finish_media_job(:job,CAST(:result AS jsonb))",
        {
            "job": job_id,
            "result": json.dumps(result),
        },
    )


def fail(identity, job_id, retryable=False, unknown=False, category="TEST_FAILURE"):
    return sql(
        identity,
        "SELECT fail_media_job(:job,:category,:retryable,:unknown)",
        {
            "job": job_id,
            "category": category,
            "retryable": retryable,
            "unknown": unknown,
        },
    )


def decision(identity, run_id, decision_name="APPROVE", checks=None, digest=None, role="APPROVER"):
    saved = row(identity, "media_runs", run_id)
    return sql(
        identity,
        "SELECT approve_media_run(:run,:hash,:decision,CAST(:checks AS jsonb),:comment)",
        {
            "run": run_id,
            "hash": digest or saved["manifest_hash"] or "0" * 64,
            "decision": decision_name,
            "checks": json.dumps(HUMAN_CHECKS if checks is None else checks),
            "comment": "Synthetic SQL test; no real video was created or approved for distribution.",
        },
        role,
    )


@pytest.fixture
def media_parent(client, media_identities):
    identity = media_identities[0]
    workflow = approve_content(client, identity, create(client, identity))["workflow"]
    profile_id = profile(identity)
    return identity, workflow, profile_id


@pytest.fixture
def media_run(media_parent):
    identity, workflow, profile_id = media_parent
    budget(identity)
    return identity, workflow, start(identity, workflow, profile_id)


def synthetic_result(identity, run_id, stage):
    saved = row(identity, "media_runs", run_id)
    narration = hashlib.sha256(saved["script"]["text"].encode()).hexdigest()
    portrait = row(identity, "visual_config_versions", saved["visual_config_version_id"])[
        "reference_sha256"
    ]
    payloads = {
        "SPEECH": {
            "schema_version": 1,
            "provider": "elevenlabs",
            "model": "eleven_multilingual_v2",
            "audio_sha256": "a" * 64,
            "audio_size_bytes": 1000,
            "alignment_sha256": "b" * 64,
            "narration_sha256": narration,
            "duration_seconds": 10.25,
            "request_id": "synthetic-speech-request",
            "character_count": len(saved["script"]["text"]),
        },
        "IMAGE_UPLOAD": {
            "schema_version": 1,
            "provider": "heygen",
            "asset_id": "synthetic-image",
            "mime_type": "image/png",
            "size_bytes": 1000,
            "sha256": portrait,
        },
        "AUDIO_UPLOAD": {
            "schema_version": 1,
            "provider": "heygen",
            "asset_id": "synthetic-audio",
            "mime_type": "audio/mpeg",
            "size_bytes": 1000,
            "sha256": "a" * 64,
        },
        "AVATAR_SUBMIT": {
            "schema_version": 1,
            "provider": "heygen",
            "request_id": "synthetic-avatar",
            "status": "QUEUED",
        },
        "AVATAR_POLL": {
            "schema_version": 1,
            "provider": "heygen",
            "request_id": "synthetic-avatar",
            "status": "COMPLETED",
            "output_url": "https://example.invalid/never-fetched-test-video.mp4",
            "duration_seconds": 10.25,
            "error_category": None,
        },
        "COMPOSE": {
            "schema_version": 1,
            "script_hash": saved["script_hash"],
            "narration_sha256": narration,
            "portrait_sha256": portrait,
            "speech_sha256": "a" * 64,
            "alignment_sha256": "b" * 64,
            "source_video_sha256": "c" * 64,
            "video_sha256": "d" * 64,
            "video_size_bytes": 10000,
            "captions_sha256": "e" * 64,
            "caption_text": saved["script"]["text"],
            "disclosure": saved["script"]["disclosure"],
            "width": 1080,
            "height": 1920,
            "fps": 24,
            "duration_seconds": 10.25,
            "audio_present": True,
            "video_codec": "h264",
            "audio_codec": "aac",
            "sample_rate": 48000,
            "disclosure_burned_in": True,
        },
    }
    return payloads[stage]


STAGES = ("SPEECH", "IMAGE_UPLOAD", "AUDIO_UPLOAD", "AVATAR_SUBMIT", "AVATAR_POLL", "COMPOSE")


def prepare_until(identity, run_id, database, stage="COMPOSE"):
    for name in STAGES:
        if name == stage:
            return
        if name == "AVATAR_POLL":
            with database.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE media_runs SET next_poll_at=now()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": run_id},
                )
        finish(identity, reserve(identity, run_id, name), synthetic_result(identity, run_id, name))


def complete(identity, run_id, database):
    prepare_until(identity, run_id, database)
    finish(
        identity,
        reserve(identity, run_id, "COMPOSE"),
        synthetic_result(identity, run_id, "COMPOSE"),
    )
    return row(identity, "media_runs", run_id)


@contextmanager
def aged_media_prices(database):
    # Simulate the passage of the price-review window without modifying immutable
    # profile history or waiting 30 days. Only the disposable migration role can
    # replace this private policy constant; runtime callers cannot change it.
    with database.begin() as conn:
        conn.execute(
            text(
                "CREATE OR REPLACE FUNCTION private.media_price_max_age() RETURNS interval LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT interval '0 seconds' $$"
            )
        )
    try:
        yield
    finally:
        with database.begin() as conn:
            conn.execute(
                text(
                    "CREATE OR REPLACE FUNCTION private.media_price_max_age() RETURNS interval LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT interval '30 days' $$"
                )
            )


@pytest.mark.parametrize("offset", [timedelta(days=-31), timedelta(minutes=1)])
def test_profile_price_review_must_be_recent_and_not_future(media_identities, offset):
    with pytest.raises(DBAPIError, match="within the last 30 days"):
        profile(
            media_identities[0],
            profile_payload(price_checked_at=(datetime.now(UTC) + offset).isoformat()),
        )


@pytest.mark.parametrize("stage", ["SPEECH", "AVATAR_SUBMIT"])
def test_paid_reservation_rechecks_price_age_at_every_paid_checkpoint(media_run, database, stage):
    identity, _, run_id = media_run
    prepare_until(identity, run_id, database, stage=stage)
    before = records(identity, "media_jobs", run_id)
    with aged_media_prices(database):
        with pytest.raises(DBAPIError, match="within the last 30 days"):
            reserve(identity, run_id, stage)
    assert records(identity, "media_jobs", run_id) == before
    assert row(identity, "media_jobs", reserve(identity, run_id, stage))["status"] == "RUNNING"


def test_expired_prices_do_not_prevent_free_poll_compose_or_exact_review(media_run, database):
    identity, _, run_id = media_run
    prepare_until(identity, run_id, database, stage="AVATAR_POLL")
    with database.begin() as conn:
        conn.execute(
            text("UPDATE media_runs SET next_poll_at=now()-interval '1 second' WHERE id=:id"),
            {"id": run_id},
        )
    with aged_media_prices(database):
        for stage in ("AVATAR_POLL", "COMPOSE"):
            finish(
                identity,
                reserve(identity, run_id, stage),
                synthetic_result(identity, run_id, stage),
            )
        decision(identity, run_id)
    assert row(identity, "media_runs", run_id)["status"] == "APPROVED"


def test_runtime_cannot_relax_media_price_age(media_run):
    identity, _, _ = media_run
    with pytest.raises(DBAPIError) as rejected:
        sql(
            identity,
            "CREATE OR REPLACE FUNCTION private.media_price_max_age() RETURNS interval LANGUAGE sql IMMUTABLE AS $$ SELECT interval '100 years' $$",
        )
    assert getattr(rejected.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize("request_id", ["", "x" * 256, "id\nheader", "https://provider.invalid/id"])
def test_sql_failure_rejects_unbounded_provider_correlation(media_run, request_id):
    identity, _, run_id = media_run
    job_id = reserve(identity, run_id, "SPEECH")
    with pytest.raises(DBAPIError):
        sql(
            identity,
            "SELECT fail_media_job(:job,'UNKNOWN_OUTCOME',false,true,NULL,:rid)",
            {"job": job_id, "rid": request_id},
        )
    assert row(identity, "media_jobs", job_id)["status"] == "RUNNING"


def test_provider_failure_correlation_is_tenant_scoped_immutable_and_audited(
    media_run, media_identities
):
    identity, workflow, run_id = media_run
    job_id = reserve(identity, run_id, "SPEECH")
    statement = "SELECT fail_media_job(:job,'UNKNOWN_OUTCOME',false,true,NULL,:rid)"
    values = {"job": job_id, "rid": "provider-request-123"}
    with pytest.raises(DBAPIError):
        sql(media_identities[1], statement, values)
    with pytest.raises(DBAPIError):
        sql(identity, statement, values, role="APPROVER")
    sql(identity, statement, values)
    job = row(identity, "media_jobs", job_id)
    assert job["failure_request_id"] == values["rid"] and job["status"] == "UNKNOWN_OUTCOME"
    assert job["actual_cost"] is None and not job["retryable"]
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        events = repo.all("audit_events", workflow_run_id=UUID(workflow["id"]))
        assert any(event["details"].get("provider_request_id") == values["rid"] for event in events)
    with pytest.raises(DBAPIError):
        sql(
            identity,
            "UPDATE media_jobs SET failure_request_id='replacement' WHERE id=:job RETURNING id",
            {"job": job_id},
        )
    with pytest.raises(DBAPIError):
        sql(identity, statement, {**values, "rid": "replacement"})
    assert row(identity, "media_jobs", job_id)["failure_request_id"] == values["rid"]


def test_media_reservations_and_exact_parent_lineage_persist_before_external_work(media_run):
    identity, workflow, run_id = media_run
    saved = row(identity, "media_runs", run_id)
    assert saved["status"] == "CREATED"
    for field in ("asset_version_id", "research_version_id", "qa_report_id"):
        assert str(saved[field]) == workflow[field]
    assert (
        saved["script"]["text"] == "The office opens at 09:00 on weekdays.\nThis is an AI creator."
    )
    assert canonical_hash(saved["script"]) == saved["script_hash"]
    assert saved["script"]["blocks"][0]["path"] == "slides.0.body"
    assert saved["script"]["blocks"][0]["fact_ids"]
    job = row(identity, "media_jobs", reserve(identity, run_id, "SPEECH"))
    skill = row(identity, "skill_runs", job["skill_run_id"])
    assert job["status"] == skill["status"] == "RUNNING"
    assert job["actual_cost"] is None and skill["cost"] is None
    assert job["expected_max_cost"] == Decimal(len(saved["script"]["text"])) / 1000
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        cost = repo.one("cost_events", skill_run_id=job["skill_run_id"])
    assert cost["cost"] is None and cost["provider"] == "elevenlabs"
    assert job["request_id"] and job["input_hash"] == canonical_hash(job["input"])


def test_structurally_complete_video_still_needs_separate_exact_human_review(
    client, media_run, database
):
    identity, workflow, run_id = media_run
    saved = complete(identity, run_id, database)
    assert saved["status"] == "AWAITING_APPROVAL"
    assert saved["qa_result"] == {"schema_version": 1, "status": "PASS", "findings": []}
    assert records(identity, "media_approval_records", run_id) == []
    approval_id = decision(identity, run_id)
    approved = row(identity, "media_runs", run_id)
    approval = row(identity, "media_approval_records", approval_id)
    assert approved["status"] == "APPROVED" and approval["manifest_hash"] == saved["manifest_hash"]
    assert approval["human_checks"] == HUMAN_CHECKS
    jobs = records(identity, "media_jobs", run_id)
    assert len(jobs) == 6 and all(job["status"] == "SUCCEEDED" for job in jobs)
    avatar = next(job for job in jobs if job["stage"] == "AVATAR_SUBMIT")
    assert avatar["expected_max_cost"] == Decimal("1.1"), (
        "Avatar reservation rounds duration upward"
    )
    assert all(job["actual_cost"] is None for job in jobs if job["provider"] != "deterministic")
    current = client.get(f"/v1/workflow-runs/{workflow['id']}", headers=headers(identity)).json()
    assert (
        current["state"] == "APPROVED"
        and current["asset_version_id"] == workflow["asset_version_id"]
    )


@pytest.mark.parametrize("role", ["OPERATOR", "APPROVER"])
def test_only_administrators_may_set_profiles_and_spend_policy(media_identities, role):
    identity = media_identities[0]
    for operation in (lambda: profile(identity, role=role), lambda: budget(identity, role=role)):
        with pytest.raises(DBAPIError) as rejected:
            operation()
        assert getattr(rejected.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize(
    "change",
    [
        {"tts_model": None},
        {"tts_model": "unpriced-model"},
        {"voice_id": "https://untrusted.invalid"},
        {"api_key": "not-a-real-secret"},
        {"tts_usd_per_1000_characters": 1},
        {"avatar_usd_per_second": "0"},
        {"tts_usd_per_1000_characters": "-1"},
        {"price_checked_at": "2026-01-01T00:00:00+01:00"},
    ],
)
def test_profile_schema_rejects_secret_fields_unknown_models_and_unpriced_values(
    media_identities, change
):
    with pytest.raises(DBAPIError):
        profile(media_identities[0], profile_payload(**change))


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": "true"},
        {"per_run_usd": "1001"},
        {"per_day_usd": "10001"},
        {"per_run_usd": "0"},
        {"per_day_usd": 100},
        {"expires_at": "2000-01-01T00:00:00Z"},
    ],
)
def test_spend_policy_is_explicit_bounded_typed_and_unexpired(media_identities, change):
    with pytest.raises(DBAPIError):
        budget(media_identities[0], budget_payload(**change))


@pytest.mark.parametrize(
    "paths",
    [
        [],
        ["arbitrary"],
        ["slides.99.body"],
        ["caption", "caption"],
        ["disclosure"],
        ["slides.0.body"] * 11,
    ],
)
def test_narration_accepts_only_existing_unique_approved_text_paths(media_parent, paths):
    identity, workflow, profile_id = media_parent
    with pytest.raises(DBAPIError):
        start(identity, workflow, profile_id, paths)


def test_narration_order_is_explicit_and_body_text_cannot_be_overridden(media_parent):
    identity, workflow, profile_id = media_parent
    run_id = start(identity, workflow, profile_id, ["cta", "slides.0.body"])
    saved = row(identity, "media_runs", run_id)
    assert saved["script"]["text"].startswith("Read the source.\nThe office")
    with pytest.raises(DBAPIError):
        reserve(identity, run_id, "SPEECH", text="Fabricated funding claim", expected_max_cost=0)


def test_run_idempotency_same_key_payload_and_different_tenant(
    media_parent, media_identities, client
):
    identity, workflow, profile_id = media_parent
    key = str(uuid4())
    first = start(identity, workflow, profile_id, key=key)
    assert start(identity, workflow, profile_id, key=key) == first
    with pytest.raises(DBAPIError) as conflict:
        start(identity, workflow, profile_id, paths=["cta"], key=key)
    assert getattr(conflict.value.orig, "sqlstate", None) == "23505"
    other = media_identities[1]
    other_workflow = approve_content(client, other, create(client, other))["workflow"]
    assert start(other, other_workflow, profile(other), key=key) != first


def test_concurrent_same_key_creates_one_media_run(media_parent):
    identity, workflow, profile_id = media_parent
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(lambda _: start(identity, workflow, profile_id, key=key), range(3)))
    assert len(set(ids)) == 1


def test_superseded_media_run_cannot_reserve_paid_work(media_parent):
    identity, workflow, profile_id = media_parent
    budget(identity)
    old_run = start(identity, workflow, profile_id)
    current_run = start(identity, workflow, profile_id)
    with pytest.raises(DBAPIError) as superseded:
        reserve(identity, old_run, "SPEECH")
    assert getattr(superseded.value.orig, "sqlstate", None) == "23514"
    assert records(identity, "media_jobs", old_run) == []
    current_job = row(identity, "media_jobs", reserve(identity, current_run, "SPEECH"))
    assert current_job["media_run_id"] == current_run and current_job["status"] == "RUNNING"


def test_disabled_missing_expired_or_replaced_budget_prevents_paid_reservation(
    media_parent, database
):
    identity, workflow, profile_id = media_parent
    run_id = start(identity, workflow, profile_id)
    with pytest.raises(DBAPIError):
        reserve(identity, run_id, "SPEECH")
    budget(identity, budget_payload(enabled=False))
    with pytest.raises(DBAPIError):
        reserve(identity, run_id, "SPEECH")
    # Disabled historical policy cannot be overridden by selecting an older enabled policy.
    budget(identity)
    budget(identity, budget_payload(enabled=False, expires_at="2000-01-01T00:00:00Z"))
    with pytest.raises(DBAPIError):
        reserve(identity, run_id, "SPEECH")
    assert records(identity, "media_jobs", run_id) == []


def test_reservations_count_failed_attempts_and_enforce_run_budget(media_parent, database):
    identity, workflow, profile_id = media_parent
    run_id = start(identity, workflow, profile_id)
    narration = row(identity, "media_runs", run_id)["script"]["text"]
    cost = Decimal(len(narration)) / 1000
    budget(identity, budget_payload(per_run_usd=str(cost), per_day_usd="100"))
    job_id = reserve(identity, run_id, "SPEECH")
    fail(identity, job_id, retryable=True)
    with database.begin() as conn:
        # A failed call is not proof of a provider refund.
        assert (
            conn.execute(
                text("SELECT expected_max_cost FROM media_jobs WHERE id=:id"), {"id": job_id}
            ).scalar_one()
            == cost
        )
    time.sleep(1.1)
    with pytest.raises(DBAPIError) as exceeded:
        reserve(identity, run_id, "SPEECH")
    assert "ceiling" in exceeded.value.orig.diag.message_primary
    assert len(records(identity, "media_jobs", run_id)) == 1


def test_concurrent_paid_reservations_respect_tenant_daily_ceiling(media_parent, client):
    identity, workflow, profile_id = media_parent
    other_workflow = approve_content(client, identity, create(client, identity))["workflow"]
    ids = [start(identity, parent, profile_id) for parent in (workflow, other_workflow)]
    cost = Decimal(len(row(identity, "media_runs", ids[0])["script"]["text"])) / 1000
    budget(identity, budget_payload(per_run_usd="100", per_day_usd=str(cost)))

    def try_reserve(run_id):
        try:
            return reserve(identity, run_id, "SPEECH")
        except DBAPIError as error:
            assert getattr(error.orig, "sqlstate", None) == "23514"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(try_reserve, ids))
    assert sum(job is not None for job in jobs) == 1


def test_concurrent_same_run_reservation_never_duplicates_provider_attempt(media_run):
    identity, _, run_id = media_run

    def try_reserve(_):
        try:
            return reserve(identity, run_id, "SPEECH")
        except DBAPIError:
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = list(pool.map(try_reserve, range(3)))
    assert sum(job is not None for job in jobs) == 1
    assert len(records(identity, "media_jobs", run_id)) == 1


def test_unknown_provider_outcome_halts_without_refund_or_paid_resubmission(media_run):
    identity, _, run_id = media_run
    job_id = reserve(identity, run_id, "SPEECH")
    reserved = row(identity, "media_jobs", job_id)["expected_max_cost"]
    fail(identity, job_id, unknown=True, category="PROVIDER_RESPONSE_LOST")
    assert row(identity, "media_runs", run_id)["status"] == "UNKNOWN_OUTCOME"
    job = row(identity, "media_jobs", job_id)
    assert job["status"] == "UNKNOWN_OUTCOME" and not job["retryable"]
    assert job["actual_cost"] is None and job["expected_max_cost"] == reserved
    with pytest.raises(DBAPIError):
        reserve(identity, run_id, "SPEECH")
    with pytest.raises(DBAPIError):
        finish(identity, job_id, synthetic_result(identity, run_id, "SPEECH"))
    assert len(records(identity, "media_jobs", run_id)) == 1


def test_nonretryable_failure_is_terminal_and_cannot_be_approved(media_run):
    identity, _, run_id = media_run
    fail(identity, reserve(identity, run_id, "SPEECH"))
    assert row(identity, "media_runs", run_id)["status"] == "FAILED"
    for operation in (
        lambda: reserve(identity, run_id, "SPEECH"),
        lambda: decision(identity, run_id),
    ):
        with pytest.raises(DBAPIError):
            operation()


def test_new_profile_or_content_revision_prevents_completion_and_new_work(media_run, client):
    identity, workflow, run_id = media_run
    job_id = reserve(identity, run_id, "SPEECH")
    profile(identity, profile_payload(voice_id="different-voice"))
    with pytest.raises(DBAPIError):
        finish(identity, job_id, synthetic_result(identity, run_id, "SPEECH"))
    draft = artifact_data(client, identity, workflow)["content_asset_versions"][0]["payload"]
    revise(client, identity, workflow, draft)
    with pytest.raises(DBAPIError):
        start(identity, workflow, row(identity, "media_runs", run_id)["profile_id"])


def test_new_visual_configuration_invalidates_media_profile(media_parent, database):
    identity, workflow, profile_id = media_parent
    seed_visual_config(
        database,
        identity["tenant_id"],
        identity["influencer_id"],
        "Updated reference policy",
        "This is an AI creator.",
    )
    with pytest.raises(DBAPIError):
        start(identity, workflow, profile_id)


@pytest.mark.parametrize(
    "stage", ["IMAGE_UPLOAD", "AUDIO_UPLOAD", "AVATAR_SUBMIT", "AVATAR_POLL", "COMPOSE"]
)
def test_stage_cannot_skip_required_persisted_upstream_work(media_run, stage):
    identity, _, run_id = media_run
    with pytest.raises(DBAPIError):
        reserve(identity, run_id, stage)
    assert records(identity, "media_jobs", run_id) == []


def test_repeated_pending_avatar_polls_keep_distinct_successful_checkpoints(media_run, database):
    identity, _, run_id = media_run
    prepare_until(identity, run_id, database, stage="AVATAR_POLL")
    job_ids = []
    for status in ("QUEUED", "RUNNING", "COMPLETED"):
        with database.begin() as conn:
            conn.execute(
                text("UPDATE media_runs SET next_poll_at=now()-interval '1 second' WHERE id=:id"),
                {"id": run_id},
            )
        result = synthetic_result(identity, run_id, "AVATAR_POLL")
        result["status"] = status
        if status != "COMPLETED":
            result.update(output_url=None, duration_seconds=None)
        job_id = reserve(identity, run_id, "AVATAR_POLL")
        finish(identity, job_id, result)
        job_ids.append(job_id)
        assert row(identity, "media_runs", run_id)["status"] == (
            "AVATAR_READY" if status == "COMPLETED" else "AVATAR_PENDING"
        )
    jobs = [row(identity, "media_jobs", job_id) for job_id in job_ids]
    assert [job["attempt"] for job in jobs] == [1, 2, 3]
    skills = [row(identity, "skill_runs", job["skill_run_id"]) for job in jobs]
    assert all(skill["status"] == "SUCCEEDED" for skill in skills)
    assert len({skill["step_key"] for skill in skills}) == 3
    finish(
        identity,
        reserve(identity, run_id, "COMPOSE"),
        synthetic_result(identity, run_id, "COMPOSE"),
    )
    assert row(identity, "media_runs", run_id)["status"] == "AWAITING_APPROVAL"


@pytest.mark.parametrize(
    "change",
    [
        {"model": "unpriced-model"},
        {"narration_sha256": "0" * 64},
        {"audio_sha256": "invalid"},
        {"duration_seconds": 0},
        {"duration_seconds": 301},
        {"audio_size_bytes": True},
        {"character_count": -1},
        {"extra": "hallucinated"},
    ],
)
def test_speech_result_schema_cannot_change_narration_model_or_usage(media_run, change):
    identity, _, run_id = media_run
    job_id = reserve(identity, run_id, "SPEECH")
    with pytest.raises(DBAPIError):
        finish(identity, job_id, {**synthetic_result(identity, run_id, "SPEECH"), **change})
    assert row(identity, "media_jobs", job_id)["status"] == "RUNNING"
    assert row(identity, "media_runs", run_id)["status"] == "CREATED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("caption_text", "Unsupported factual statement."),
        ("disclosure", ""),
        ("disclosure_burned_in", False),
        ("audio_present", False),
        ("duration_seconds", 11.0),
        ("portrait_sha256", "0" * 64),
        ("speech_sha256", "0" * 64),
        ("width", 1920),
    ],
)
def test_manifest_cannot_claim_pass_for_unsupported_or_mismatched_media(
    media_run, database, field, value
):
    identity, _, run_id = media_run
    prepare_until(identity, run_id, database)
    job_id = reserve(identity, run_id, "COMPOSE")
    payload = synthetic_result(identity, run_id, "COMPOSE")
    payload[field] = value
    with pytest.raises(DBAPIError):
        finish(identity, job_id, payload)
    assert row(identity, "media_runs", run_id)["manifest"] is None
    with pytest.raises(DBAPIError):
        decision(identity, run_id)


@pytest.mark.parametrize("check", list(HUMAN_CHECKS))
def test_every_human_check_is_required_for_media_approval(media_run, database, check):
    identity, _, run_id = media_run
    complete(identity, run_id, database)
    checks = {**HUMAN_CHECKS, check: False}
    with pytest.raises(DBAPIError):
        decision(identity, run_id, checks=checks)
    assert records(identity, "media_approval_records", run_id) == []


def test_media_approval_requires_role_hash_latest_run_and_current_parent(
    media_run, media_parent, client, database
):
    identity, workflow, run_id = media_run
    complete(identity, run_id, database)
    with pytest.raises(DBAPIError) as unauthorized:
        decision(identity, run_id, role="OPERATOR")
    assert getattr(unauthorized.value.orig, "sqlstate", None) == "42501"
    with pytest.raises(DBAPIError):
        decision(identity, run_id, digest="0" * 64)
    start(identity, workflow, media_parent[2])
    with pytest.raises(DBAPIError):
        decision(identity, run_id)
    draft = artifact_data(client, identity, workflow)["content_asset_versions"][0]["payload"]
    revised = revise(client, identity, workflow, draft)
    approve_content(client, identity, revised)
    with pytest.raises(DBAPIError):
        decision(identity, run_id)


def test_media_rejection_is_immutable_and_cannot_be_reapproved(media_run, database):
    identity, _, run_id = media_run
    complete(identity, run_id, database)
    decision(identity, run_id, "REJECT", checks=dict.fromkeys(HUMAN_CHECKS, False))
    assert row(identity, "media_runs", run_id)["status"] == "REJECTED"
    with pytest.raises(DBAPIError):
        decision(identity, run_id)


def test_media_rls_and_guarded_writes_are_enforced_at_database_boundary(
    media_run, media_identities, database
):
    owner, _, run_id = media_run
    other = media_identities[1]
    job_id = reserve(owner, run_id, "SPEECH")
    with transaction(UUID(other["tenant_id"]), other["tokens"]["OPERATOR"]) as repo:
        assert (
            repo.connection.execute(
                text("SELECT * FROM media_runs WHERE id=:id"), {"id": run_id}
            ).all()
            == []
        )
        assert (
            repo.connection.execute(
                text("SELECT * FROM media_jobs WHERE id=:id"), {"id": job_id}
            ).all()
            == []
        )
    for operation in (
        lambda: sql(other, "SELECT reserve_media_job(:id,'SPEECH','{}'::jsonb)", {"id": run_id}),
        lambda: fail(other, job_id),
        lambda: sql(
            other,
            "SELECT approve_media_run(:id,:hash,'APPROVE',CAST(:checks AS jsonb),NULL)",
            {"id": run_id, "hash": "0" * 64, "checks": json.dumps(HUMAN_CHECKS)},
            "APPROVER",
        ),
    ):
        with pytest.raises(DBAPIError) as denied:
            operation()
        assert getattr(denied.value.orig, "sqlstate", None) == "P0002"
    with database.begin() as conn:
        for table in TABLES:
            flags = conn.execute(
                text(
                    "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)"
                ),
                {"table": table},
            ).one()
            assert flags[0] and flags[1]
    with engine().begin() as conn:
        for table in TABLES:
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                assert not conn.execute(
                    text("SELECT has_table_privilege(current_user,:table,:privilege)"),
                    {"table": table, "privilege": privilege},
                ).scalar_one()
    for statement in (
        "UPDATE media_runs SET status='APPROVED' WHERE id=:id",
        'UPDATE media_runs SET qa_result=\'{"status":"PASS"}\'::jsonb WHERE id=:id',
        "DELETE FROM media_jobs WHERE media_run_id=:id",
        "INSERT INTO media_runs OVERRIDING SYSTEM VALUE SELECT * FROM media_runs WHERE id=:id",
    ):
        with pytest.raises(DBAPIError) as denied:
            sql(owner, statement + " RETURNING id", {"id": run_id})
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"


def test_mock_and_deterministic_costs_cannot_be_relabelled_as_unknown(media_run):
    identity, workflow, _ = media_run
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        original = repo.all("skill_runs", workflow_run_id=UUID(workflow["id"]))[0]
    for provider in ("mock", "deterministic"):
        with pytest.raises(DBAPIError) as invalid:
            with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
                repo.insert(
                    "cost_events",
                    workflow_run_id=UUID(workflow["id"]),
                    skill_run_id=original["id"],
                    provider=provider,
                    model="none",
                    input_tokens=0,
                    output_tokens=0,
                    cost=None,
                    currency="USD",
                    price_version="invalid-null-cost-test",
                )
        assert getattr(invalid.value.orig, "sqlstate", None) == "23514"


def test_retry_after_is_persisted_and_cannot_be_bypassed(media_run):
    identity, _, run_id = media_run
    job_id = reserve(identity, run_id, "SPEECH")
    before = datetime.now(UTC)
    sql(identity, "SELECT fail_media_job(:job,'RATE_LIMITED',true,false,120)", {"job": job_id})
    failed = row(identity, "media_jobs", job_id)
    assert failed["retryable"] and failed["retry_at"] >= before + timedelta(seconds=120)
    skill = row(identity, "skill_runs", failed["skill_run_id"])
    assert skill["retry_at"] == failed["retry_at"]
    with pytest.raises(DBAPIError):
        reserve(identity, run_id, "SPEECH")
    assert len(records(identity, "media_jobs", run_id)) == 1


@pytest.mark.parametrize("delay", [-1, 86401])
def test_unbounded_retry_after_does_not_change_persisted_job(media_run, delay):
    identity, _, run_id = media_run
    job_id = reserve(identity, run_id, "SPEECH")
    with pytest.raises(DBAPIError):
        sql(
            identity,
            "SELECT fail_media_job(:job,'RATE_LIMITED',true,false,:delay)",
            {"job": job_id, "delay": delay},
        )
    assert row(identity, "media_jobs", job_id)["status"] == "RUNNING"


def test_safe_retry_attempts_are_bounded_without_releasing_old_reservations(media_run):
    identity, _, run_id = media_run
    for attempt in range(1, 4):
        job_id = reserve(identity, run_id, "SPEECH")
        fail(identity, job_id, retryable=True)
        job = row(identity, "media_jobs", job_id)
        assert job["attempt"] == attempt
        assert job["retryable"] is (attempt < 3)
        if attempt < 3:
            # Real bounded local backoff; immutable attempt timestamps are not edited.
            time.sleep(2 ** (attempt - 1) + 0.1)
    assert row(identity, "media_runs", run_id)["status"] == "FAILED"
    with pytest.raises(DBAPIError):
        reserve(identity, run_id, "SPEECH")
    jobs = records(identity, "media_jobs", run_id)
    assert len(jobs) == 3 and all(
        job["actual_cost"] is None and job["expected_max_cost"] > 0 for job in jobs
    )


def test_media_access_rechecks_manifest_approval_and_new_parent_revision(
    media_run, client, database
):
    identity, workflow, run_id = media_run
    with pytest.raises(DBAPIError):
        sql(identity, "SELECT check_media_access(:run,false)", {"run": run_id})
    complete(identity, run_id, database)
    sql(identity, "SELECT check_media_access(:run,false)", {"run": run_id})
    with pytest.raises(DBAPIError):
        sql(identity, "SELECT check_media_access(:run,true)", {"run": run_id})
    decision(identity, run_id)
    sql(identity, "SELECT check_media_access(:run,true)", {"run": run_id})
    draft = artifact_data(client, identity, workflow)["content_asset_versions"][0]["payload"]
    revise(client, identity, workflow, draft)
    for approved in (True, False):
        with pytest.raises(DBAPIError):
            sql(
                identity,
                "SELECT check_media_access(:run,:approved)",
                {"run": run_id, "approved": approved},
            )


def test_media_api_auth_scope_schema_and_disabled_live_execution(
    client, media_parent, media_identities
):
    identity, workflow, profile_id = media_parent
    route = f"/v1/workflow-runs/{workflow['id']}/media-runs"
    assert client.get(route).status_code == 401
    request = {
        "profile_id": str(profile_id),
        "selected_paths": ["slides.0.body"],
        "idempotency_key": str(uuid4()),
    }
    invalid = {**request, "script": "Unsupported claim"}
    assert client.post(route, headers=headers(identity), json=invalid).status_code == 422
    created = client.post(route, headers=headers(identity), json=request)
    assert created.status_code == 200, created.text
    run_id = created.json()["id"]
    assert client.post(route, headers=headers(identity), json=request).json()["id"] == run_id
    own = client.get(f"/v1/media-runs/{run_id}", headers=headers(identity))
    assert own.status_code == 200 and own.json()["status"] == "CREATED"
    other = media_identities[1]
    assert client.get(f"/v1/media-runs/{run_id}", headers=headers(other)).status_code == 404
    assert (
        client.post(f"/v1/media-runs/{run_id}/execute", headers=headers(other)).status_code == 404
    )
    assert client.get(f"/v1/media-runs/{run_id}/video", headers=headers(other)).status_code == 404
    assert (
        client.post(f"/v1/media-runs/{run_id}/execute", headers=headers(identity)).status_code
        == 409
    )
    assert records(identity, "media_jobs", UUID(run_id)) == []
    dependencies = client.get("/v1/media-dependencies", headers=headers(identity))
    assert dependencies.status_code == 200 and dependencies.json()["automatic_publishing"] is False
    assert (
        client.post(
            "/v1/media-spend-policy", headers=headers(identity), json=budget_payload()
        ).status_code
        == 403
    )


def test_media_api_does_not_approve_synthetic_receipts_without_real_validated_video(
    client, media_run, database
):
    identity, _, run_id = media_run
    complete(identity, run_id, database)
    saved = row(identity, "media_runs", run_id)
    response = client.post(
        f"/v1/media-runs/{run_id}/review",
        headers=headers(identity, "APPROVER"),
        json={
            "manifest_hash": saved["manifest_hash"],
            "decision": "APPROVE",
            "checks": HUMAN_CHECKS,
            "comment": "Synthetic receipt alone cannot pass actual server file validation.",
        },
    )
    assert response.status_code == 409, response.text
    assert records(identity, "media_approval_records", run_id) == []
    details = client.get(f"/v1/media-runs/{run_id}", headers=headers(identity))
    assert details.status_code == 200
    assert all("output_url" not in (job["result"] or {}) for job in details.json()["jobs"])
