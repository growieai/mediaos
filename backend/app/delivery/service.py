"""Persisted internal delivery rehearsal with guarded exact-render eligibility."""

import hashlib
import json
import logging
import time
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.repository import Repository, canonical_hash, engine, transaction
from app.delivery import adapter
from app.delivery.schemas import DeliveryInput, DeliveryPlan, DeliveryReceipt
from app.rendering import service as rendering
from app.rendering.schemas import RenderManifest
from app.services.workflows import ConflictError, typed

log = logging.getLogger("mediaos")
MAX_ATTEMPTS = 3
TERMINAL = {"DRY_RUN_COMPLETE", "BLOCKED", "FAILED"}


def database_now_utc(connection) -> datetime:
    # PostgreSQL returns timestamptz in the session timezone. The wire contract
    # requires UTC, while preserving the exact database-controlled instant.
    return connection.execute(text("SELECT clock_timestamp()")).scalar_one().astimezone(UTC)


def delivery_details(repo: Repository, delivery_id: UUID):
    row = repo.one("delivery_runs", id=delivery_id)
    row["attempts"] = repo.all(
        "skill_runs",
        workflow_run_id=row["workflow_run_id"],
        step_key=f"delivery.dry_run:{delivery_id}",
    )
    return row


def workflow_deliveries(repo: Repository, run_id: UUID):
    repo.one("workflow_runs", id=run_id)
    return [
        delivery_details(repo, row["id"])
        for row in repo.all("delivery_runs", workflow_run_id=run_id)
    ]


def create_delivery(tenant: UUID, token: str, render_id: UUID, request: DeliveryInput):
    request = DeliveryInput.model_validate_json(request.model_dump_json(), strict=True)
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        render = repo.one("render_runs", id=render_id)
        repo.one("delivery_targets", id=request.target_id)
        input_hash = canonical_hash(
            {
                "render_run_id": render_id,
                "target_id": request.target_id,
                "manifest_hash": render["manifest_hash"],
                "mode": "DRY_RUN",
                "adapter_version": adapter.ADAPTER_VERSION,
            }
        )
        delivery_id = repo.connection.execute(
            text("SELECT start_delivery(:render,:target,:key,:hash)"),
            {
                "render": render_id,
                "target": request.target_id,
                "key": request.idempotency_key,
                "hash": input_hash,
            },
        ).scalar_one()
    return execute_delivery(tenant, token, delivery_id)


def _interrupt_attempts(repo: Repository, previous):
    now = database_now_utc(repo.connection)
    for old in previous:
        if old["status"] == "RUNNING":
            repo.update_skill(
                old["id"],
                status="INTERRUPTED",
                ended_at=now,
                latency_ms=max(0.0, (now - old["started_at"]).total_seconds() * 1000),
                error_category="PROCESS_INTERRUPTED",
                retryable=old["attempt"] < MAX_ATTEMPTS,
            )
            log.info(
                "delivery_attempt_interrupted",
                extra={
                    "tenant_id": str(repo.tenant_id),
                    "workflow_run_id": str(old["workflow_run_id"]),
                    "skill_run_id": str(old["id"]),
                    "attempt": old["attempt"],
                    "state": "INTERRUPTED",
                },
            )


def _failure(conn, tenant, token, row, attempt, category, retryable, started):
    with conn.begin():
        repo = Repository(conn, tenant, token)
        repo.connection.execute(
            text("SELECT fail_delivery(:id,:category,:retryable)"),
            {"id": row["id"], "category": category, "retryable": retryable},
        )
        current = repo.one("delivery_runs", id=row["id"])
        if attempt:
            repo.update_skill(
                attempt["id"],
                status="FAILED",
                ended_at=database_now_utc(conn),
                latency_ms=(time.monotonic() - started) * 1000,
                error_category=category,
                retryable=current["status"] == "RETRY_WAIT",
                retry_at=current["retry_at"],
            )
        result = delivery_details(repo, row["id"])
    _log_result(result, attempt)
    return result


def _log_result(row, attempt):
    log.info(
        "delivery_rehearsal_finished",
        extra={
            "tenant_id": str(row["tenant_id"]),
            "workflow_run_id": str(row["workflow_run_id"]),
            "skill_run_id": str(attempt["id"]) if attempt else None,
            "attempt": attempt["attempt"] if attempt else row["attempt_count"],
            "state": row["status"],
            "error_category": row["error_category"],
        },
    )


def _validate_result(payload, receipt, manifest, archive, row, validated_at):
    expected_slides = [
        {
            "index": index,
            "filename": f"slide-{index:02d}.png",
            "sha256": slide.sha256,
            "width": 1080,
            "height": 1350,
            "media_type": "image/png",
        }
        for index, slide in enumerate(manifest.slides, 1)
    ]
    if (
        payload.target_id != row["target_id"]
        or payload.render_run_id != row["render_run_id"]
        or payload.manifest_hash != row["manifest_hash"]
        or payload.language != manifest.language
        or payload.caption != manifest.caption
        or [slide.model_dump(mode="json") for slide in payload.slides] != expected_slides
        or payload.package_sha256 != hashlib.sha256(archive).hexdigest()
    ):
        raise ValueError("DELIVERY_OUTPUT_MISMATCH")
    expected_receipt = DeliveryReceipt(
        package_sha256=payload.package_sha256,
        payload_sha256=canonical_hash(payload.model_dump(mode="json")),
        manifest_hash=row["manifest_hash"],
        caption_sha256=hashlib.sha256(manifest.caption.text.encode("utf-8")).hexdigest(),
        slide_sha256=[slide.sha256 for slide in payload.slides],
        validated_at=validated_at,
    )
    if receipt != expected_receipt:
        raise ValueError("DELIVERY_RECEIPT_MISMATCH")


def execute_delivery(tenant: UUID, token: str, delivery_id: UUID):
    with engine().connect() as conn:
        lock_key = f"delivery:{tenant}:{delivery_id}"
        with conn.begin():
            repo = Repository(conn, tenant, token)
            repo.require("OPERATOR")
            repo.one("delivery_runs", id=delivery_id)
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key": lock_key}
            ).scalar_one()
        if not locked:
            raise ConflictError("Delivery rehearsal is already executing")
        try:
            return _execute(conn, tenant, token, delivery_id)
        finally:
            if conn.in_transaction():
                conn.rollback()
            with conn.begin():
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), {"key": lock_key}
                )


def _execute(conn, tenant, token, delivery_id):
    started = time.monotonic()
    with conn.begin():
        repo = Repository(conn, tenant, token)
        row = repo.one("delivery_runs", id=delivery_id)
        if row["status"] in TERMINAL:
            return delivery_details(repo, delivery_id)
        now = database_now_utc(conn)
        if row["status"] == "RETRY_WAIT" and row["retry_at"] and row["retry_at"] > now:
            return delivery_details(repo, delivery_id)
        previous = repo.all(
            "skill_runs",
            workflow_run_id=row["workflow_run_id"],
            step_key=f"delivery.dry_run:{delivery_id}",
        )
        _interrupt_attempts(repo, previous)
        if len(previous) >= MAX_ATTEMPTS or row["attempt_count"] >= MAX_ATTEMPTS:
            conn.execute(
                text("SELECT fail_delivery(:id,'ATTEMPTS_EXHAUSTED',false)"), {"id": delivery_id}
            )
            result = delivery_details(repo, delivery_id)
            _log_result(result, None)
            return result
    try:
        with conn.begin():
            repo = Repository(conn, tenant, token)
            conn.execute(text("SELECT claim_delivery(:id)"), {"id": delivery_id})
            row = repo.one("delivery_runs", id=delivery_id)
            attempt = repo.insert(
                "skill_runs",
                workflow_run_id=row["workflow_run_id"],
                step_key=f"delivery.dry_run:{delivery_id}",
                skill_identifier="delivery.dry_run",
                skill_version="1.0.0",
                input_schema_version=1,
                output_schema_version=1,
                provider="mock",
                model="none",
                adapter=adapter.ADAPTER_VERSION,
                attempt=row["attempt_count"],
                input_hash=row["input_hash"],
                status="RUNNING",
                is_mock=True,
            )
            repo.insert(
                "cost_events",
                workflow_run_id=row["workflow_run_id"],
                skill_run_id=attempt["id"],
                provider="mock",
                model="none",
                input_tokens=0,
                output_tokens=0,
                cost=0,
                currency="USD",
                price_version="mock-zero-v1",
            )
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != "23514":
            raise
        return _failure(conn, tenant, token, row, None, "POLICY_BLOCKED", False, started)

    log.info(
        "delivery_attempt_started",
        extra={
            "tenant_id": str(tenant),
            "workflow_run_id": str(row["workflow_run_id"]),
            "skill_run_id": str(attempt["id"]),
            "attempt": attempt["attempt"],
            "state": "VALIDATING",
        },
    )

    try:
        with conn.begin():
            repo = Repository(conn, tenant, token)
            # The existing export function holds revision/source locks while checking
            # exact bytes. Keep those locks through the guarded receipt commit.
            archive = rendering.export_render(repo, row["render_run_id"])
            render = repo.one("render_runs", id=row["render_run_id"])
            manifest = typed(RenderManifest, render["manifest"])
            now = database_now_utc(conn)
            payload, receipt = adapter.prepare_delivery(
                manifest, archive, row["target_id"], row["render_run_id"], row["manifest_hash"], now
            )
            # Revalidate returned objects even when an adapter implementation changes.
            # Never echo invalid adapter values through Pydantic's serializer warnings.
            # Strict validation still rejects every malformed field immediately below.
            payload = DeliveryPlan.model_validate_json(
                payload.model_dump_json(warnings=False), strict=True
            )
            receipt = DeliveryReceipt.model_validate_json(
                receipt.model_dump_json(warnings=False), strict=True
            )
            _validate_result(payload, receipt, manifest, archive, row, now)
            payload_data, receipt_data = (
                payload.model_dump(mode="json"),
                receipt.model_dump(mode="json"),
            )
            conn.execute(
                text(
                    "SELECT complete_delivery(:id,CAST(:payload AS jsonb),:phash,CAST(:receipt AS jsonb),:rhash)"
                ),
                {
                    "id": delivery_id,
                    "payload": json.dumps(payload_data, ensure_ascii=False),
                    "phash": canonical_hash(payload_data),
                    "receipt": json.dumps(receipt_data, ensure_ascii=False),
                    "rhash": canonical_hash(receipt_data),
                },
            )
            repo.update_skill(
                attempt["id"],
                status="SUCCEEDED",
                ended_at=database_now_utc(conn),
                latency_ms=(time.monotonic() - started) * 1000,
                output=receipt_data,
                asset_version_id=row["asset_version_id"],
            )
            result = delivery_details(repo, delivery_id)
    except DBAPIError as error:
        # Connectivity/uncertain-commit failures must reconnect and inspect persisted
        # state, rather than falsely marking an already committed receipt as failed.
        if getattr(error.orig, "sqlstate", None) != "23514":
            raise
        return _failure(conn, tenant, token, row, attempt, "POLICY_BLOCKED", False, started)
    except (adapter.RetryableDeliveryError, TimeoutError, BlockingIOError, InterruptedError):
        return _failure(conn, tenant, token, row, attempt, "LOCAL_TRANSIENT_FAILURE", True, started)
    except (adapter.DeliveryIntegrityError, ConflictError, OSError):
        return _failure(conn, tenant, token, row, attempt, "PACKAGE_INVALID", False, started)
    except (ValidationError, ValueError, TypeError, AttributeError):
        return _failure(
            conn, tenant, token, row, attempt, "INVALID_REHEARSAL_OUTPUT", False, started
        )
    except Exception:
        return _failure(conn, tenant, token, row, attempt, "EXECUTION_FAILED", False, started)
    _log_result(result, attempt)
    return result
