"""Persisted internal reply review with no communication or provider side effects."""

import json
import logging
import time
from datetime import UTC
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.community import policy
from app.community.schemas import (
    CommunityClassification,
    CommunityDecisionInput,
    CommunityDraft,
    CommunityEventInput,
    CommunityEventPayload,
    CommunityReviewInput,
)
from app.db.repository import Repository, canonical_hash, engine, transaction
from app.models.schemas import CharacterConfig, QAReport
from app.services.workflows import ConflictError, typed

log = logging.getLogger("mediaos")
MAX_ATTEMPTS = 3
TERMINAL = {
    "HUMAN_REVIEW",
    "BLOCKED",
    "REVISION_REQUIRED",
    "AWAITING_REVIEW",
    "REVIEWED_DRAFT",
    "REJECTED",
    "FAILED",
}
STAGE_STATES = {
    "CREATED": "classify",
    "CLASSIFYING": "classify",
    "CLASSIFIED": "draft",
    "DRAFTING": "draft",
    "DRAFT_COMPLETE": "qa",
    "QA_RUNNING": "qa",
}


def _now(connection):
    return connection.execute(text("SELECT clock_timestamp()")).scalar_one().astimezone(UTC)


def _event(row):
    payload = typed(CommunityEventPayload, row["payload"])
    if canonical_hash(row["payload"]) != row["content_hash"] or payload.mode != row["mode"]:
        raise ValueError("Community event provenance checksum mismatch")
    return row


def review_details(repo: Repository, review_id: UUID):
    row = repo.one("community_reviews", id=review_id)
    for field, schema in (
        ("classification", CommunityClassification),
        ("draft", CommunityDraft),
        ("qa", QAReport),
    ):
        if row[field] is not None:
            typed(schema, row[field])
            if canonical_hash(row[field]) != row[f"{field}_hash"]:
                raise ValueError("Community review checkpoint checksum mismatch")
    row["decisions"] = repo.all("community_decisions", review_id=review_id)
    row["claims"] = repo.all("community_reply_claims", review_id=review_id)
    keys = [f"community.{stage}:{review_id}" for stage in ("classify", "draft", "qa")]
    skills = repo.table("skill_runs")
    query = (
        select(skills)
        .where(
            skills.c.tenant_id == repo.tenant_id,
            skills.c.workflow_run_id == row["workflow_run_id"],
            skills.c.step_key.in_(keys),
        )
        .order_by(skills.c.created_at, skills.c.id)
    )
    row["attempts"] = [dict(attempt) for attempt in repo.connection.execute(query).mappings()]
    return row


def event_details(repo: Repository, event_id: UUID):
    row = _event(repo.one("community_events", id=event_id))
    row["platform_links"] = (
        repo.all("social_comment_links", event_id=event_id) if row["mode"] == "PLATFORM" else []
    )
    row["reviews"] = [
        review_details(repo, review["id"])
        for review in repo.all("community_reviews", event_id=event_id)
    ]
    return row


def workflow_events(repo: Repository, run_id: UUID):
    repo.one("workflow_runs", id=run_id)
    return [
        event_details(repo, row["id"])
        for row in repo.all("community_events", workflow_run_id=run_id)
    ]


def create_event(tenant: UUID, token: str, run_id: UUID, request: CommunityEventInput):
    request = CommunityEventInput.model_validate_json(
        request.model_dump_json(warnings=False), strict=True
    )
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        repo.one("workflow_runs", id=run_id)
        event_id = repo.connection.execute(
            text("SELECT register_community_event(:id,CAST(:payload AS jsonb),:key,:hash)"),
            {
                "id": run_id,
                "payload": json.dumps(payload, ensure_ascii=False),
                "key": request.idempotency_key,
                "hash": canonical_hash({"workflow_run_id": run_id, "payload": payload}),
            },
        ).scalar_one()
        return event_details(repo, event_id)


def create_review(tenant: UUID, token: str, event_id: UUID, request: CommunityReviewInput):
    request = CommunityReviewInput.model_validate_json(
        request.model_dump_json(warnings=False), strict=True
    )
    facts = [str(value) for value in request.fact_ids]
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        repo.one("community_events", id=event_id)
        review_id = repo.connection.execute(
            text("SELECT start_community_review(:id,CAST(:facts AS jsonb),:key,:hash)"),
            {
                "id": event_id,
                "facts": json.dumps(facts),
                "key": request.idempotency_key,
                "hash": canonical_hash({"event_id": event_id, "fact_ids": facts}),
            },
        ).scalar_one()
    try:
        return execute_review(tenant, token, review_id)
    except ConflictError:
        # Concurrent identical creation recovers the same persisted identity even
        # when another request holds its execution lease. Explicit execute still
        # reports a busy lease so callers do not start competing workers.
        with transaction(tenant, token) as repo:
            return review_details(repo, review_id)


def decide_review(
    repo: Repository, review_id: UUID, request: CommunityDecisionInput, decision: str
):
    request = CommunityDecisionInput.model_validate_json(
        request.model_dump_json(warnings=False), strict=True
    )
    repo.require("APPROVER")
    repo.one("community_reviews", id=review_id)
    repo.connection.execute(
        text("SELECT decide_community_review(:id,:draft,:qa,:decision,:comment)"),
        {
            "id": review_id,
            "draft": request.draft_hash,
            "qa": request.qa_hash,
            "decision": decision,
            "comment": request.comment,
        },
    )
    return review_details(repo, review_id)


def execute_review(tenant: UUID, token: str, review_id: UUID):
    with engine().connect() as conn:
        lock_key = f"community:{tenant}:{review_id}"
        with conn.begin():
            repo = Repository(conn, tenant, token)
            repo.require("OPERATOR")
            repo.one("community_reviews", id=review_id)
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key": lock_key}
            ).scalar_one()
        if not locked:
            raise ConflictError("Community review is already executing")
        try:
            return _execute(conn, tenant, token, review_id)
        finally:
            if conn.in_transaction():
                conn.rollback()
            with conn.begin():
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"), {"key": lock_key}
                )


def _log(row, attempt, event):
    log.info(
        event,
        extra={
            "tenant_id": str(row["tenant_id"]),
            "workflow_run_id": str(row["workflow_run_id"]),
            "skill_run_id": str(attempt["id"]) if attempt else None,
            "attempt": attempt["attempt"] if attempt else row["attempt_count"],
            "state": row["status"],
            "error_category": row["error_category"],
        },
    )


def _failure(conn, tenant, token, row, attempt, category, retryable, started):
    with conn.begin():
        repo = Repository(conn, tenant, token)
        conn.execute(
            text("SELECT fail_community_stage(:id,:category,:retryable)"),
            {"id": row["id"], "category": category, "retryable": retryable},
        )
        current = repo.one("community_reviews", id=row["id"])
        if attempt:
            repo.update_skill(
                attempt["id"],
                status="FAILED",
                ended_at=_now(conn),
                latency_ms=(time.monotonic() - started) * 1000,
                error_category=category,
                retryable=current["status"] == "RETRY_WAIT",
                retry_at=current["retry_at"],
            )
        result = review_details(repo, row["id"])
    _log(result, attempt, "community_stage_failed")
    return result


def _output(repo, row, stage):
    event = _event(repo.one("community_events", id=row["event_id"]))
    config = typed(
        CharacterConfig,
        repo.one("character_config_versions", id=row["character_config_version_id"])["payload"],
    )
    facts = [repo.one("facts", id=UUID(value)) for value in row["fact_ids"]]
    # Acquires the same parent/opportunity locks used by protected approval. They
    # remain held through output validation, checkpoint and successful attempt.
    parent_findings = repo.connection.execute(
        text("SELECT community_parent_findings(:id)"), {"id": row["id"]}
    ).scalar_one()
    if stage == "classify":
        classification = policy.classify(event["comment_text"], config)
        return CommunityClassification.model_validate_json(
            classification.model_dump_json(warnings=False), strict=True
        )
    if stage == "draft":
        draft = policy.build_draft(row, config, facts)
        return CommunityDraft.model_validate_json(
            draft.model_dump_json(warnings=False), strict=True
        )
    output = policy.validate_reply(row, event, config, facts, parent_findings)
    return QAReport.model_validate_json(output.model_dump_json(warnings=False), strict=True)


def _execute(conn, tenant, token, review_id):
    while True:
        started = time.monotonic()
        attempt = None
        with conn.begin():
            repo = Repository(conn, tenant, token)
            row = repo.one("community_reviews", id=review_id)
            if row["status"] in TERMINAL:
                return review_details(repo, review_id)
            if row["retry_at"] and row["retry_at"] > _now(conn):
                return review_details(repo, review_id)
            stage = (
                row["active_stage"]
                if row["status"] == "RETRY_WAIT"
                else STAGE_STATES[row["status"]]
            )
            previous = repo.all(
                "skill_runs",
                workflow_run_id=row["workflow_run_id"],
                step_key=f"community.{stage}:{review_id}",
            )
            for old in previous:
                if old["status"] == "RUNNING":
                    now = _now(conn)
                    repo.update_skill(
                        old["id"],
                        status="INTERRUPTED",
                        ended_at=now,
                        latency_ms=max(0.0, (now - old["started_at"]).total_seconds() * 1000),
                        error_category="PROCESS_INTERRUPTED",
                        retryable=old["attempt"] < MAX_ATTEMPTS,
                    )
            if len(previous) >= MAX_ATTEMPTS or row["attempt_count"] >= MAX_ATTEMPTS:
                conn.execute(
                    text("SELECT fail_community_stage(:id,'ATTEMPTS_EXHAUSTED',false)"),
                    {"id": review_id},
                )
                return review_details(repo, review_id)
        try:
            with conn.begin():
                repo = Repository(conn, tenant, token)
                conn.execute(
                    text("SELECT claim_community_stage(:id,:stage)"),
                    {"id": review_id, "stage": stage},
                )
                row = repo.one("community_reviews", id=review_id)
                attempt = repo.insert(
                    "skill_runs",
                    workflow_run_id=row["workflow_run_id"],
                    step_key=f"community.{stage}:{review_id}",
                    skill_identifier=f"community.{stage}",
                    skill_version="1.0.0",
                    input_schema_version=1,
                    output_schema_version=1,
                    provider="deterministic",
                    model="none",
                    adapter="community-exact-v1",
                    attempt=row["attempt_count"],
                    input_hash=row["input_hash"],
                    status="RUNNING",
                    is_mock=False,
                )
                repo.insert(
                    "cost_events",
                    workflow_run_id=row["workflow_run_id"],
                    skill_run_id=attempt["id"],
                    provider="deterministic",
                    model="none",
                    input_tokens=0,
                    output_tokens=0,
                    cost=0,
                    currency="USD",
                    price_version="deterministic-zero-v1",
                )
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) != "23514":
                raise
            return _failure(conn, tenant, token, row, None, "POLICY_BLOCKED", False, started)
        _log(row, attempt, "community_stage_started")
        try:
            with conn.begin():
                repo = Repository(conn, tenant, token)
                row = repo.one("community_reviews", id=review_id)
                output = _output(repo, row, stage)
                payload = output.model_dump(mode="json")
                conn.execute(
                    text(
                        "SELECT complete_community_stage(:id,:stage,CAST(:output AS jsonb),:hash)"
                    ),
                    {
                        "id": review_id,
                        "stage": stage,
                        "output": json.dumps(payload, ensure_ascii=False),
                        "hash": canonical_hash(payload),
                    },
                )
                repo.update_skill(
                    attempt["id"],
                    status="SUCCEEDED",
                    ended_at=_now(conn),
                    latency_ms=(time.monotonic() - started) * 1000,
                    output=payload,
                    asset_version_id=row["asset_version_id"],
                    research_version_id=row["research_version_id"],
                )
                row = repo.one("community_reviews", id=review_id)
        except DBAPIError as error:
            # An uncertain commit is recovered by a fresh execute; do not blindly
            # record a failure over an already committed checkpoint.
            if getattr(error.orig, "sqlstate", None) != "23514":
                raise
            return _failure(conn, tenant, token, row, attempt, "POLICY_BLOCKED", False, started)
        except (TimeoutError, BlockingIOError, InterruptedError):
            return _failure(
                conn, tenant, token, row, attempt, "LOCAL_TRANSIENT_FAILURE", True, started
            )
        except (ValidationError, ValueError, TypeError, AttributeError, KeyError):
            return _failure(
                conn, tenant, token, row, attempt, "INVALID_COMMUNITY_OUTPUT", False, started
            )
        except Exception:
            return _failure(conn, tenant, token, row, attempt, "EXECUTION_FAILED", False, started)
        _log(row, attempt, "community_stage_completed")
