"""Persisted, single-attempt creator execution. Provider I/O never holds a DB transaction."""

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.ai.policy import TextAIPolicy
from app.ai.structured import ModelSelectionError, OpenAISelectionAdapter, SelectionSettings
from app.config import get_settings
from app.db.repository import Repository, canonical_hash
from app.models.schemas import ContentBrief, ResearchPack
from app.services.workflows import ConflictError

log = logging.getLogger("mediaos")


def _finish(conn, tenant, token, attempt, result=None, error=None, retry=False, delay=None):
    with conn.begin():
        Repository(conn, tenant, token).require("OPERATOR")
        conn.execute(
            text("SELECT finish_text_ai_attempt(:id,CAST(:p AS jsonb),:error,:retry,:delay)"),
            {
                "id": attempt["id"],
                "p": json.dumps(result or {}),
                "error": error,
                "retry": retry,
                "delay": delay,
            },
        )
    log.info(
        "text_ai_finished",
        extra={
            "tenant_id": str(tenant),
            "workflow_run_id": str(attempt["workflow_run_id"]),
            "skill_run_id": str(attempt["skill_run_id"]),
            "attempt": attempt["attempt"],
            "state": error or "SUCCEEDED",
        },
    )


def execute_creator(conn, tenant, token, run_id):
    """Return true only after an artifact checkpoint; false means held/failed/retry pending.

    The caller owns the workflow session advisory lock. A process can die between
    reservation and send, or between response and commit: both are UNKNOWN_OUTCOME
    on restart. We never infer that the provider did not receive a paid request.
    """
    from app.services.workflows import config_for, save_asset, typed

    with conn.begin():
        repo = Repository(conn, tenant, token)
        repo.require("OPERATOR")
        run = repo.one("workflow_runs", id=run_id)
        history = sorted(
            repo.all("text_ai_attempts", workflow_run_id=run_id), key=lambda a: a["attempt"]
        )
        previous = history[-1] if history else None
        if previous and previous["status"] == "SUCCEEDED":
            return True
        if previous and previous["status"] != "RUNNING":
            if not previous["retryable"] or previous["retry_at"] > datetime.now(UTC):
                return False
    if previous and previous["status"] == "RUNNING":
        _finish(conn, tenant, token, previous, error="UNKNOWN_OUTCOME")
        return False

    settings = get_settings()
    if settings.ai_mock_mode or not settings.openai_api_key:
        raise ConflictError(
            "Real text execution is disabled; an existing paid attempt cannot switch mode"
        )
    with conn.begin():
        repo = Repository(conn, tenant, token)
        policies = repo.all("text_ai_policies")
        if not policies:
            raise ConflictError("An administrator must configure a dated model policy and budget")
        policy_row = max(policies, key=lambda p: p["version"])
        policy = typed(TextAIPolicy, policy_row["payload"])
        research = typed(
            ResearchPack,
            repo.one("research_pack_versions", id=run["research_version_id"])["payload"],
        )
        brief = typed(ContentBrief, repo.one("content_briefs", id=run["brief_id"])["payload"])
        config = config_for(repo, run)
        adapter = OpenAISelectionAdapter(
            SelectionSettings(
                api_key=settings.openai_api_key,
                model=policy.model,
                max_output_tokens=policy.max_output_tokens,
                max_input_bytes=policy.max_input_bytes,
            )
        )
        try:
            payload = adapter.prepare(research, brief, config, run["influencer_version_id"])
        except ModelSelectionError:
            raise ConflictError(
                "Verified bounded creator input required before reserving model usage"
            ) from None
        aid = conn.execute(
            text("SELECT reserve_text_ai_attempt(:wid,:policy,CAST(:p AS jsonb),:digest)"),
            {
                "wid": run_id,
                "policy": policy_row["id"],
                "p": json.dumps(payload),
                "digest": canonical_hash(payload),
            },
        ).scalar_one()
        attempt = repo.one("text_ai_attempts", id=aid)

    # This is intentionally outside both the reservation and artifact transactions.
    assert not conn.in_transaction()
    try:
        result = adapter.select(
            research,
            brief,
            config,
            run["influencer_version_id"],
            expected_request_hash=attempt["request_hash"],
            client_request_id=attempt["id"],
        )
    except ModelSelectionError as error:
        metadata = {"execution": error.execution.model_dump(mode="json")} if error.execution else {}
        _finish(
            conn,
            tenant,
            token,
            attempt,
            metadata,
            error.category,
            error.retryable,
            error.retry_after_seconds,
        )
        return False
    except Exception:
        _finish(conn, tenant, token, attempt, error="UNKNOWN_OUTCOME")
        return False

    try:
        with conn.begin():
            repo = Repository(conn, tenant, token)
            conn.execute(text("SELECT check_text_ai_attempt(:id)"), {"id": aid})
            current = repo.one("workflow_runs", id=run_id)
            save_asset(repo, current, result.draft)
            conn.execute(
                text("SELECT finish_text_ai_attempt(:id,CAST(:p AS jsonb))"),
                {"id": aid, "p": result.model_dump_json()},
            )
    except DBAPIError as error:
        # Rejected stale evidence is known. A connection failure around COMMIT is
        # ambiguous: leave the persisted checkpoint for the next execute to inspect.
        code = getattr(error.orig, "sqlstate", None)
        if code not in {"23514", "23503", "42501"}:
            raise ConflictError(
                "Model result commit requires checkpoint inspection before retry"
            ) from None
        _finish(
            conn,
            tenant,
            token,
            attempt,
            {"execution": result.execution.model_dump(mode="json")},
            "INPUT_CHANGED",
        )
        return False
    log.info(
        "text_ai_finished",
        extra={
            "tenant_id": str(tenant),
            "workflow_run_id": str(run_id),
            "skill_run_id": str(attempt["skill_run_id"]),
            "attempt": attempt["attempt"],
            "state": "CONTENT_COMPLETE",
        },
    )
    return True
