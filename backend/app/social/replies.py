"""Explicitly authorized exact replies. Webhook ingestion never triggers dispatch."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.community.schemas import Digest, Key
from app.community.service import event_details
from app.config import get_settings
from app.db.repository import Repository, canonical_hash, engine, transaction
from app.media.service import _receipt
from app.models.schemas import StrictModel
from app.services.workflows import ConflictError, typed
from app.social import files, service
from app.social_provider import SocialProviderError
from app.social_provider.schemas import CommentEvent

log = logging.getLogger("mediaos")
TERMINAL = {"SENT", "REJECTED", "BLOCKED", "FAILED", "UNKNOWN_OUTCOME"}


class ReplyInput(StrictModel):
    idempotency_key: Key


class ReplyDecision(StrictModel):
    decision: Literal["AUTHORIZE_REPLY", "REJECT"]
    text_hash: Digest
    comment: str | None = Field(default=None, max_length=2000)


class ReplyReceipt(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[0-9]{1,64}$")
    response_hash: Digest
    captured_at: datetime
    raw: dict
    raw_hash: Digest
    text_hash: Digest

    @model_validator(mode="after")
    def exact_response(self):
        if (
            self.captured_at.utcoffset() != timedelta(0)
            or self.raw_hash != canonical_hash(self.raw)
            or self.raw.get("id") != self.id
            or len(json.dumps(self.raw, ensure_ascii=False).encode()) > 100_000
        ):
            raise ValueError("Invalid exact reply receipt")
        return self


def ingest_webhook(repo: Repository, webhook_id: UUID):
    """Called only after signature verification and durable social_record_webhook."""
    repo.require("SOCIAL")
    observed = repo.one("social_webhook_events", id=webhook_id)
    typed(CommentEvent, observed["payload"])
    if canonical_hash(observed["payload"]) != observed["content_hash"]:
        raise ValueError("Webhook checksum mismatch")
    event_id = repo.connection.execute(
        text("SELECT social_link_comment(:id)"), {"id": webhook_id}
    ).scalar_one()
    return event_details(repo, event_id) if event_id else None


def details(repo: Repository, reply_id: UUID):
    row = repo.one("social_reply_runs", id=reply_id)
    row["decisions"] = repo.all("social_reply_decisions", reply_run_id=reply_id)
    row["jobs"] = repo.all("social_reply_jobs", reply_run_id=reply_id)
    return row


def for_review(repo: Repository, review_id: UUID):
    repo.one("community_reviews", id=review_id)
    return [details(repo, row["id"]) for row in repo.all("social_reply_runs", review_id=review_id)]


def create(tenant: UUID, caller: str, review_id: UUID, request: ReplyInput):
    with transaction(tenant, caller) as repo:
        repo.require("OPERATOR")
        reply_id = repo.connection.execute(
            text("SELECT social_start_reply(:id,:key,:hash)"),
            {
                "id": review_id,
                "key": request.idempotency_key,
                "hash": canonical_hash({"review_id": review_id}),
            },
        ).scalar_one()
        return details(repo, reply_id)


def decide(tenant: UUID, caller: str, reply_id: UUID, request: ReplyDecision):
    with transaction(tenant, caller) as repo:
        repo.require("APPROVER")
        repo.connection.execute(
            text("SELECT social_decide_reply(:id,CAST(:p AS jsonb))"),
            {"id": reply_id, "p": request.model_dump_json()},
        )
        return details(repo, reply_id)


def _failure(conn, tenant, token, job, category, *, retry=False, unknown=False, delay=0):
    with conn.begin():
        Repository(conn, tenant, token)
        conn.execute(
            text("SELECT social_fail_reply(:id,:category,:retry,:unknown,:delay)"),
            {
                "id": job["id"],
                "category": category,
                "retry": retry,
                "unknown": unknown,
                "delay": delay or 0,
            },
        )
    log.warning(
        "social_reply_failed",
        extra={
            "tenant_id": str(tenant),
            "workflow_run_id": str(job["workflow_run_id"]),
            "skill_run_id": str(job["skill_run_id"]),
            "attempt": job["attempt"],
            "error_category": category,
        },
    )


def _complete(conn, job, receipt):
    conn.execute(
        text("SELECT social_finish_reply(:id,CAST(:p AS jsonb))"),
        {"id": job["id"], "p": json.dumps(receipt, ensure_ascii=False)},
    )


def _recover(conn, tenant, token, folder, job):
    try:
        saved = _receipt(folder, job)
        receipt = typed(ReplyReceipt, saved).model_dump(mode="json") if saved is not None else None
    except (ValueError, OSError, ConflictError):
        receipt = None
    if receipt is None:
        _failure(conn, tenant, token, job, "PROCESS_INTERRUPTED", unknown=True)
        return
    try:
        with conn.begin():
            Repository(conn, tenant, token)
            _complete(conn, job, receipt)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23514":
            raise
        _failure(conn, tenant, token, job, "INVALID_REPLY_RECEIPT", unknown=True)


def execute(tenant: UUID, caller: str, reply_id: UUID):
    if not get_settings().social_reply_enabled:
        raise ConflictError("Explicit Instagram reply dispatch is disabled")
    with transaction(tenant, caller) as repo:
        repo.require("OPERATOR")
        row = details(repo, reply_id)
        if row["status"] in TERMINAL:
            return row
    token = service.service_token(tenant)
    folder = files.directory(get_settings().social_storage_path, tenant, reply_id)
    lease = f"social-reply:{tenant}:{reply_id}"
    with engine().connect() as conn:
        with conn.begin():
            Repository(conn, tenant, token)
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key": lease}
            ).scalar_one()
        if not locked:
            raise ConflictError("Reply dispatch is already executing")
        try:
            with conn.begin():
                row = details(Repository(conn, tenant, token), reply_id)
            active = next((j for j in row["jobs"] if j["status"] == "RUNNING"), None)
            if active:
                _recover(conn, tenant, token, folder, active)
            elif row["status"] == "AUTHORIZED":
                if any(j["retry_at"] and j["retry_at"] > datetime.now(UTC) for j in row["jobs"]):
                    return row
                client, _ = service.connected_provider(tenant, row["connection_id"])
                with conn.begin():
                    repo = Repository(conn, tenant, token)
                    job_id = conn.execute(
                        text("SELECT social_reserve_reply(:id)"), {"id": reply_id}
                    ).scalar_one()
                    job = repo.one("social_reply_jobs", id=job_id)
                began_post = False
                try:
                    with conn.begin():
                        Repository(conn, tenant, token)
                        conn.execute(text("SELECT social_guard_reply(:id)"), {"id": job_id})
                        # Existing workflow/source/connection/comment locks remain held
                        # through this single bounded POST and its exact checkpoint.
                        began_post = True
                        observation = client.reply(row["comment_id"], row["exact_text"])
                        receipt = ReplyReceipt(
                            id=observation.payload.id,
                            response_hash=observation.response_hash,
                            captured_at=observation.captured_at,
                            raw=observation.raw,
                            raw_hash=observation.raw_hash,
                            text_hash=row["text_hash"],
                        ).model_dump(mode="json")
                        _receipt(folder, job, receipt)
                        _complete(conn, job, receipt)
                except SocialProviderError as exc:
                    _failure(
                        conn,
                        tenant,
                        token,
                        job,
                        exc.category,
                        retry=exc.retryable and exc.category == "RATE_LIMITED",
                        unknown=exc.category == "UNKNOWN_OUTCOME",
                        delay=exc.retry_after_seconds,
                    )
                except DBAPIError as exc:
                    if getattr(exc.orig, "sqlstate", None) != "23514":
                        # Receipt stays on disk and RUNNING stays recoverable; no blind replay.
                        raise
                    _failure(
                        conn,
                        tenant,
                        token,
                        job,
                        "INVALID_REPLY_RECEIPT" if began_post else "POLICY_BLOCKED",
                        unknown=began_post,
                    )
                except (ValueError, OSError, RuntimeError, ConflictError, PermissionError):
                    _failure(
                        conn,
                        tenant,
                        token,
                        job,
                        "LOCAL_RECEIPT_FAILED" if began_post else "POLICY_BLOCKED",
                        unknown=began_post,
                    )
            with conn.begin():
                return details(Repository(conn, tenant, token), reply_id)
        finally:
            if conn.in_transaction():
                conn.rollback()
            with conn.begin():
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), {"key": lease}
                )
