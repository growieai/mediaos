import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.ai.client import DeterministicAdapter, MockAdapter
from app.config import get_settings
from app.db.repository import Repository, canonical_hash, engine, transaction
from app.models.schemas import (
    CarouselDraft,
    CharacterConfig,
    ContentBrief,
    CreateRun,
    OpportunityResearch,
    QAReport,
    ResearchPack,
    SourceInput,
)
from app.observability import request_id
from app.skills import creator, editor, qa, research

log = logging.getLogger("mediaos")


class ConflictError(Exception):
    pass


class RetryableSkillError(Exception):
    pass


class SkillFailed(Exception):
    pass


def typed(schema, value):
    return schema.model_validate_json(json.dumps(value, default=str), strict=True)


def create_run(
    tenant_id: UUID, token: str, request: CreateRun, correlation_id: UUID, after_create=None
):
    data = request.model_dump(mode="json", exclude={"idempotency_key"})
    digest = canonical_hash(data)
    with transaction(tenant_id, token) as repo:
        repo.require("OPERATOR")
        influencer = repo.one("influencers", id=request.influencer_id)
        mission = repo.one("missions", id=request.mission_id, influencer_id=influencer["id"])
        versions = repo.all("influencer_versions", influencer_id=influencer["id"])
        if not versions:
            raise ConflictError("Influencer has no version")
        version = max(versions, key=lambda v: v["version"])
        table = repo.table("workflow_runs")
        row = (
            repo.connection.execute(
                insert(table)
                .values(
                    tenant_id=tenant_id,
                    influencer_id=influencer["id"],
                    influencer_version_id=version["id"],
                    mission_id=mission["id"],
                    created_by=repo.principal_id,
                    idempotency_key=request.idempotency_key,
                    canonical_input_hash=digest,
                    correlation_id=correlation_id,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
                .returning(table)
            )
            .mappings()
            .first()
        )
        if row is None:
            existing = repo.one("workflow_runs", idempotency_key=request.idempotency_key)
            if existing["canonical_input_hash"] != digest:
                raise ConflictError("Idempotency key already used with a different payload")
            if after_create:
                after_create(repo, existing)
            return existing
        run = dict(row)
        source = request.source.model_dump(mode="python", exclude={"evidence"})
        source["metadata"] = request.source.metadata
        source["checksum"] = hashlib.sha256(request.source.raw_content.encode()).hexdigest()
        snapshot = repo.insert(
            "source_snapshots",
            workflow_run_id=run["id"],
            **source,
            evidence_input=[e.model_dump(mode="json") for e in request.source.evidence],
            environment=get_settings().app_env,
            created_by=repo.principal_id,
        )
        repo.checkpoint(run["id"], "SOURCE_CAPTURED", snapshot["id"])
        result = repo.one("workflow_runs", id=run["id"])
        if after_create:
            after_create(repo, result)
        return result


def config_for(repo, run):
    version = repo.one("influencer_versions", id=run["influencer_version_id"])
    return typed(
        CharacterConfig,
        repo.one("character_config_versions", id=version["character_config_version_id"])["payload"],
    )


def source_input(snapshot):
    fields = SourceInput.model_fields.keys()
    data = {key: snapshot[key] for key in fields if key != "evidence"}
    data["evidence"] = snapshot["evidence_input"]
    return typed(SourceInput, data)


def save_research(repo, run, output):
    bindings = repo.all("workflow_opportunities", workflow_run_id=run["id"])
    if bindings:
        output.opportunity_context = typed(OpportunityResearch, bindings[0]["research_context"])
    pack = repo.insert("research_packs", workflow_run_id=run["id"])
    version = repo.insert(
        "research_pack_versions",
        research_pack_id=pack["id"],
        workflow_run_id=run["id"],
        version=1,
        schema_version=1,
        payload=output.model_dump(mode="json"),
        content_hash=canonical_hash(output.model_dump(mode="json")),
        verification_status=output.verification_status,
    )
    for sid in {f.source_snapshot_id for f in output.facts}:
        repo.insert(
            "research_pack_sources",
            research_version_id=version["id"],
            workflow_run_id=run["id"],
            source_snapshot_id=sid,
        )
    for f in output.facts:
        repo.insert(
            "facts",
            id=f.id,
            source_snapshot_id=f.source_snapshot_id,
            span_start=f.start,
            span_end=f.end,
            statement=f.statement,
            confidence=f.confidence,
            verification_status=f.verification_status,
            fact_type=f.fact_type,
            structured_data=f.grant.model_dump(mode="json") if f.grant else None,
        )
        repo.insert(
            "research_pack_facts",
            research_version_id=version["id"],
            fact_id=f.id,
            source_snapshot_id=f.source_snapshot_id,
        )
    repo.checkpoint(run["id"], "RESEARCH_COMPLETE", version["id"])
    return {"research_version_id": version["id"]}


def save_brief(repo, run, output):
    brief = repo.insert(
        "content_briefs",
        workflow_run_id=run["id"],
        research_version_id=run["research_version_id"],
        influencer_id=run["influencer_id"],
        influencer_version_id=run["influencer_version_id"],
        mission_id=run["mission_id"],
        schema_version=1,
        payload=output.model_dump(mode="json"),
        content_hash=canonical_hash(output.model_dump(mode="json")),
    )
    for fid in output.allowed_fact_ids:
        repo.insert(
            "brief_facts",
            brief_id=brief["id"],
            research_version_id=run["research_version_id"],
            fact_id=fid,
        )
    repo.checkpoint(run["id"], "BRIEF_COMPLETE", brief["id"])
    return {"brief_id": brief["id"]}


def blocks(draft):
    yield "caption", draft.caption
    yield "cta", draft.cta
    for i, slide in enumerate(draft.slides):
        yield f"slides.{i}.headline", slide.headline
        yield f"slides.{i}.body", slide.body


def save_asset(repo, run, draft):
    assets = repo.all("content_assets", workflow_run_id=run["id"])
    asset = (
        assets[0]
        if assets
        else repo.insert("content_assets", workflow_run_id=run["id"], type="CAROUSEL")
    )
    versions = repo.all("content_asset_versions", content_asset_id=asset["id"])
    version = repo.insert(
        "content_asset_versions",
        content_asset_id=asset["id"],
        workflow_run_id=run["id"],
        brief_id=run["brief_id"],
        research_version_id=run["research_version_id"],
        version=max([v["version"] for v in versions], default=0) + 1,
        schema_version=1,
        payload=draft.model_dump(mode="json"),
        content_hash=canonical_hash(draft.model_dump(mode="json")),
    )
    for path, block in blocks(draft):
        for fid in set(block.fact_ids):
            # Foreign keys reject fabricated, unrelated and cross-tenant facts at write time.
            repo.insert(
                "content_claims",
                asset_version_id=version["id"],
                brief_id=run["brief_id"],
                research_version_id=run["research_version_id"],
                fact_id=fid,
                field_path=path,
            )
    repo.checkpoint(run["id"], "CONTENT_COMPLETE", version["id"])
    return {"asset_version_id": version["id"]}


def revise(tenant_id, token, run_id, draft: CarouselDraft):
    with transaction(tenant_id, token) as repo:
        repo.require("OPERATOR")
        run = repo.one("workflow_runs", id=run_id)
        repo.connection.execute(text("SELECT begin_revision(:rid)"), {"rid": run_id})
        save_asset(repo, run, draft)
        return repo.one("workflow_runs", id=run_id)


class Runner:
    def __init__(self, tenant_id, token, adapter=None):
        self.tenant_id = tenant_id
        self.token = token
        self.adapter = adapter or MockAdapter()

    def attempt(self, conn, run_id, key, skill, schema, inputs, operation, persist=None):
        while True:
            with conn.begin():
                repo = Repository(conn, self.tenant_id, self.token)
                rows = repo.all("skill_runs", workflow_run_id=run_id, step_key=key)
                succeeded = [row for row in rows if row["status"] == "SUCCEEDED"]
                if succeeded:
                    if succeeded[0]["input_hash"] != canonical_hash(inputs):
                        raise ConflictError("Checkpoint input hash mismatch")
                    return typed(schema, succeeded[0]["output"])
                for orphan in rows:
                    if orphan["status"] == "RUNNING":
                        repo.update_skill(
                            orphan["id"],
                            status="INTERRUPTED",
                            ended_at=datetime.now(UTC),
                            error_category="PROCESS_INTERRUPTED",
                            latency_ms=(datetime.now(UTC) - orphan["started_at"]).total_seconds()
                            * 1000,
                            retryable=True,
                        )
                if rows and (
                    len(rows) >= get_settings().max_skill_attempts
                    or (rows[-1]["status"] == "FAILED" and not rows[-1]["retryable"])
                ):
                    repo.checkpoint(run_id, "FAILED")
                    return None
                retry_at = rows[-1]["retry_at"] if rows else None
            if retry_at:
                time.sleep(max(0, (retry_at - datetime.now(UTC)).total_seconds()))
            with conn.begin():
                repo = Repository(conn, self.tenant_id, self.token)
                attempt = repo.insert(
                    "skill_runs",
                    workflow_run_id=run_id,
                    step_key=key,
                    skill_identifier=skill,
                    skill_version="1.0.0",
                    input_schema_version=1,
                    output_schema_version=1,
                    provider=self.adapter.provider,
                    model=self.adapter.model,
                    adapter=self.adapter.version,
                    attempt=len(rows) + 1,
                    input_hash=canonical_hash(inputs),
                    status="RUNNING",
                    is_mock=self.adapter.is_mock,
                )
                repo.insert(
                    "cost_events",
                    workflow_run_id=run_id,
                    skill_run_id=attempt["id"],
                    provider=self.adapter.provider,
                    model=self.adapter.model,
                    input_tokens=0,
                    output_tokens=0,
                    cost=0,
                    currency="USD",
                    price_version="mock-zero-v1"
                    if self.adapter.is_mock
                    else "deterministic-zero-v1",
                )
            started = time.monotonic()
            try:
                with conn.begin():
                    repo = Repository(conn, self.tenant_id, self.token)
                    run = repo.one("workflow_runs", id=run_id)
                    if skill == "qa.validate":
                        qid, output = qa.validate(repo, run_id)
                        refs = {"qa_report_id": qid}
                    else:
                        output = self.adapter.generate(schema, partial(operation, repo, run))
                        refs = persist(repo, run, output) if persist else {}
                    repo.update_skill(
                        attempt["id"],
                        status="SUCCEEDED",
                        ended_at=datetime.now(UTC),
                        latency_ms=(time.monotonic() - started) * 1000,
                        output=output.model_dump(mode="json"),
                        **refs,
                    )
                    state = repo.one("workflow_runs", id=run_id)["state"]
                log.info(
                    "skill_finished",
                    extra={
                        "tenant_id": str(self.tenant_id),
                        "workflow_run_id": str(run_id),
                        "skill_run_id": str(attempt["id"]),
                        "attempt": attempt["attempt"],
                        "state": state,
                    },
                )
                return output
            except Exception as exc:
                retryable = isinstance(exc, (RetryableSkillError, TimeoutError, ConnectionError))
                can_retry = retryable and attempt["attempt"] < get_settings().max_skill_attempts
                with conn.begin():
                    repo = Repository(conn, self.tenant_id, self.token)
                    repo.update_skill(
                        attempt["id"],
                        status="FAILED",
                        ended_at=datetime.now(UTC),
                        latency_ms=(time.monotonic() - started) * 1000,
                        error_category=type(exc).__name__,
                        retryable=retryable,
                        retry_at=datetime.now(UTC)
                        + timedelta(seconds=2 ** (attempt["attempt"] - 1))
                        if can_retry
                        else None,
                    )
                    if not can_retry:
                        repo.checkpoint(run_id, "FAILED")
                log.warning(
                    "skill_failed",
                    extra={
                        "tenant_id": str(self.tenant_id),
                        "workflow_run_id": str(run_id),
                        "skill_run_id": str(attempt["id"]),
                        "attempt": attempt["attempt"],
                        "state": "RETRY_PENDING" if can_retry else "FAILED",
                        "error_category": type(exc).__name__,
                    },
                )
                if not can_retry:
                    raise SkillFailed(type(exc).__name__) from None

    def execute(self, run_id):
        with engine().connect() as conn:
            with conn.begin():
                repo = Repository(conn, self.tenant_id, self.token)
                repo.require("OPERATOR")
                repo.one("workflow_runs", id=run_id)
                if type(self.adapter) is MockAdapter and repo.all(
                    "workflow_opportunities", workflow_run_id=run_id
                ):
                    self.adapter = DeterministicAdapter()
                locked = conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
                    {"key": str(run_id)},
                ).scalar_one()
            if not locked:
                raise ConflictError("Workflow is already executing")
            try:
                correlation_context = request_id.set(request_id.get() or str(run_id))
                return self._execute(conn, run_id)
            finally:
                request_id.reset(correlation_context)
                if conn.in_transaction():
                    conn.rollback()
                with conn.begin():
                    conn.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                        {"key": str(run_id)},
                    )

    def _execute(self, conn, run_id):
        while True:
            with conn.begin():
                repo = Repository(conn, self.tenant_id, self.token)
                run = repo.one("workflow_runs", id=run_id)
                state = run["state"]
                config = config_for(repo, run)
                mission = repo.one("missions", id=run["mission_id"])
                snapshot = repo.one("source_snapshots", id=run["source_snapshot_id"])
                if state in {
                    "SOURCE_CAPTURED",
                    "RESEARCH_COMPLETE",
                    "BRIEF_COMPLETE",
                    "CONTENT_COMPLETE",
                }:
                    target = {
                        "SOURCE_CAPTURED": "RESEARCHING",
                        "RESEARCH_COMPLETE": "BRIEFING",
                        "BRIEF_COMPLETE": "CONTENT_GENERATING",
                        "CONTENT_COMPLETE": "QA_RUNNING",
                    }[state]
                    repo.checkpoint(run_id, target)
                    state = target
                if state in {
                    "APPROVED",
                    "AWAITING_APPROVAL",
                    "BLOCKED",
                    "REVISION_REQUIRED",
                    "FAILED",
                }:
                    return run
                pack_row = (
                    repo.one("research_pack_versions", id=run["research_version_id"])
                    if run["research_version_id"]
                    else None
                )
            if state == "RESEARCHING":
                extracted = self.attempt(
                    conn,
                    run_id,
                    "research.extract",
                    "research.extract",
                    ResearchPack,
                    {"source": str(snapshot["id"]), "checksum": snapshot["checksum"]},
                    lambda repo, r, snapshot=snapshot: research.extract(
                        snapshot["id"], source_input(snapshot)
                    ),
                )
                if extracted is None:
                    continue
                self.attempt(
                    conn,
                    run_id,
                    "research.verify",
                    "research.verify",
                    ResearchPack,
                    {
                        "extracted": extracted.model_dump(mode="json"),
                        "verification": snapshot["verification_status"],
                    },
                    lambda repo, r, extracted=extracted, snapshot=snapshot: research.verify(
                        extracted, snapshot
                    ),
                    save_research,
                )
            elif state == "BRIEFING":
                assert pack_row is not None
                pack = typed(ResearchPack, pack_row["payload"])
                audience = config.audience
                if pack.opportunity_context:
                    with conn.begin():
                        repo = Repository(conn, self.tenant_id, self.token)
                        segment = repo.one(
                            "audience_segments", id=pack.opportunity_context.audience_segment_id
                        )
                        audience = [segment["payload"]["name"]]
                self.attempt(
                    conn,
                    run_id,
                    "editor.build_brief",
                    "editor.build_brief",
                    ContentBrief,
                    {
                        "research": str(run["research_version_id"]),
                        "config": config.model_dump(mode="json"),
                        "mission_id": str(mission["id"]),
                        "mission_objective": mission["objective"],
                        "audience": audience,
                    },
                    lambda repo,
                    r,
                    pack=pack,
                    config=config,
                    mission=mission,
                    audience=audience: editor.build_brief(
                        pack, config, mission["objective"], audience=audience
                    ),
                    save_brief,
                )
            elif state == "CONTENT_GENERATING":
                assert pack_row is not None
                pack = typed(ResearchPack, pack_row["payload"])
                self.attempt(
                    conn,
                    run_id,
                    "content.carousel",
                    "content.carousel",
                    CarouselDraft,
                    {"brief": str(run["brief_id"]), "config": config.model_dump(mode="json")},
                    lambda repo, r, pack=pack, config=config: creator.create_carousel(
                        pack, config, r["influencer_version_id"]
                    ),
                    save_asset,
                )
            elif state == "QA_RUNNING":
                self.attempt(
                    conn,
                    run_id,
                    f"qa.validate:{run['asset_version_id']}",
                    "qa.validate",
                    QAReport,
                    {
                        "asset": str(run["asset_version_id"]),
                        "research": str(run["research_version_id"]),
                    },
                    None,
                )
            else:
                raise ConflictError("Workflow has no resumable checkpoint")


def artifacts(repo, run_id):
    repo.one("workflow_runs", id=run_id)
    names = (
        "source_snapshots",
        "research_pack_versions",
        "content_briefs",
        "content_asset_versions",
        "qa_reports",
        "approval_records",
        "skill_runs",
        "cost_events",
        "workflow_opportunities",
    )
    result = {name: repo.all(name, workflow_run_id=run_id) for name in names}
    for attempt in result["skill_runs"]:
        if attempt["skill_identifier"].startswith("media.") and isinstance(attempt["output"], dict):
            attempt["output"] = {k: v for k, v in attempt["output"].items() if k != "output_url"}
        if attempt["skill_identifier"] == "conversion.export" and isinstance(
            attempt["output"], dict
        ):
            # Historical operational evidence is visible, but retrieving a handoff
            # requires current consent/role checks on the dedicated export endpoint.
            allowed = {
                "schema_version",
                "export_id",
                "request_id",
                "content_hash",
                "request_hash",
                "mode",
                "status",
                "delivered",
                "network_performed",
                "audit_completed",
            }
            attempt["output"] = {k: v for k, v in attempt["output"].items() if k in allowed}
    return result
