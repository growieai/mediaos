"""Authenticated, coherent snapshot of readiness; no new approval or workflow writes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.routes import Context, authenticated
from app.db.repository import Repository, engine
from app.models.schemas import (
    CarouselDraft,
    ContentBrief,
    QAFinding,
    QAReport,
    ResearchPack,
    WorkflowStatus,
)
from app.rendering.schemas import RenderManifest
from app.services.workflows import config_for, typed
from app.studio.readiness import ContentReadiness, ReadinessInput, RevisionIds, score_readiness

router = APIRouter(prefix="/v1/studio")


def load_readiness(repo: Repository, run_id: UUID) -> ReadinessInput:
    run = repo.one("workflow_runs", id=run_id)
    asset = (
        repo.one("content_asset_versions", id=run["asset_version_id"])
        if run["asset_version_id"]
        else None
    )
    research = (
        repo.one("research_pack_versions", id=run["research_version_id"])
        if run["research_version_id"]
        else None
    )
    brief = repo.one("content_briefs", id=run["brief_id"]) if run["brief_id"] else None
    qa = repo.one("qa_reports", id=run["qa_report_id"]) if run["qa_report_id"] else None
    renders = repo.all("render_runs", workflow_run_id=run_id)
    latest = max(renders, key=lambda row: row["sequence"]) if renders else None
    configs = repo.all("visual_config_versions", influencer_id=run["influencer_id"])
    latest_config = max(configs, key=lambda row: row["version"]) if configs else None
    # Reuse the real current policy, including expiry, conflicts, fixture status and RLS.
    # qa_findings checks data and may take an opportunity row lock; it persists no QA or state.
    findings = (
        repo.connection.execute(text("SELECT qa_findings(:id)"), {"id": run_id}).scalar_one()
        if asset
        else []
    )
    blocked_history = (
        repo.all("qa_reports", asset_version_id=asset["id"], status="BLOCKED") if asset else []
    )
    return ReadinessInput(
        workflow_run_id=run_id,
        state=WorkflowStatus(run["state"]),
        evaluated_at=repo.connection.execute(text("SELECT clock_timestamp()")).scalar_one(),
        current_ids=RevisionIds(
            asset_version_id=run["asset_version_id"],
            research_version_id=run["research_version_id"],
            qa_report_id=run["qa_report_id"],
            render_run_id=latest["id"] if latest else None,
        ),
        draft=typed(CarouselDraft, asset["payload"]) if asset else None,
        research=typed(ResearchPack, research["payload"]) if research else None,
        brief=typed(ContentBrief, brief["payload"]) if brief else None,
        config=config_for(repo, run),
        qa=typed(QAReport, qa["payload"]) if qa else None,
        qa_asset_version_id=qa["asset_version_id"] if qa else None,
        qa_research_version_id=qa["research_version_id"] if qa else None,
        current_findings=[typed(QAFinding, finding) for finding in findings],
        policy_findings_checked=bool(asset),
        current_asset_was_blocked=bool(blocked_history),
        render=typed(RenderManifest, latest["manifest"]) if latest and latest["manifest"] else None,
        render_asset_version_id=latest["asset_version_id"] if latest else None,
        render_research_version_id=latest["research_version_id"] if latest else None,
        render_qa_report_id=latest["qa_report_id"] if latest else None,
        render_config_current=bool(
            latest and latest_config and latest["visual_config_version_id"] == latest_config["id"]
        ),
    )


@router.get("/workflow-runs/{run_id}/readiness", response_model=ContentReadiness)
def workflow_readiness(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    # A consistent snapshot prevents a GET from mixing a new asset with an older QA/config.
    # Dispatch still performs its own locked guards; this response is never permission.
    with engine().connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            return score_readiness(
                load_readiness(Repository(connection, ctx.tenant, ctx.token), run_id)
            )
