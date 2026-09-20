from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.routes import Context, authenticated, body_as, run_response
from app.db.repository import transaction
from app.intelligence.editorial import evaluate, start_opportunity_workflow
from app.intelligence.ingestion import IngestionRunner, create_ingestion
from app.intelligence.schemas import DiscoveryRequest, OpportunityWorkflowRequest
from app.models.schemas import RunResponse, StrictModel

router = APIRouter(prefix="/v1/intelligence")


class EvaluateRequest(StrictModel):
    mission_id: UUID


@router.get("/sources")
def sources(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return repo.all("source_definitions")


@router.get("/audiences")
def audiences(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return repo.all("audience_segments")


@router.post("/ingestions")
async def ingest(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, DiscoveryRequest)
    return create_ingestion(ctx.tenant, ctx.token, data, request.state.correlation_id)


@router.get("/ingestions/{run_id}")
def ingestion(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return {
            "run": repo.one("ingestion_runs", id=run_id),
            "attempts": repo.all("ingestion_attempts", ingestion_run_id=run_id),
        }


@router.post("/ingestions/{run_id}/execute")
def execute(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.require("OPERATOR")
        repo.one("ingestion_runs", id=run_id)
    return IngestionRunner(ctx.tenant).execute(run_id)


@router.get("/opportunities")
def opportunities(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return [
            {**o, "current_version": repo.one("opportunity_versions", id=o["current_version_id"])}
            for o in repo.all("opportunities")
            if o["current_version_id"]
        ]


@router.get("/opportunities/{opportunity_id}")
def opportunity(opportunity_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        root = repo.one("opportunities", id=opportunity_id)
        result = {"opportunity": root}
        for table in (
            "opportunity_versions",
            "source_links",
            "opportunity_facts",
            "verification_conflicts",
            "change_events",
            "duplicate_candidates",
            "workflow_opportunities",
        ):
            result[table] = repo.all(table, opportunity_id=opportunity_id)
        links = result["source_links"]
        result["source_snapshots"] = [
            repo.one("source_snapshots", id=link["source_snapshot_id"]) for link in links
        ]
        result["raw_source_documents"] = [
            repo.one("raw_source_documents", id=s["raw_document_id"])
            for s in result["source_snapshots"]
        ]
        result["raw_source_snapshots"] = [
            repo.one("source_snapshots", id=s["parent_snapshot_id"])
            for s in result["source_snapshots"]
        ]
        return result


@router.post("/opportunities/{opportunity_id}/evaluate")
async def score(
    opportunity_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, EvaluateRequest)
    with transaction(ctx.tenant, ctx.token) as repo:
        return evaluate(repo, repo.one("opportunities", id=opportunity_id), data.mission_id)


@router.post("/opportunities/{opportunity_id}/workflow", response_model=RunResponse)
async def workflow(
    opportunity_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, OpportunityWorkflowRequest)
    return run_response(
        start_opportunity_workflow(
            ctx.tenant, ctx.token, opportunity_id, data, request.state.correlation_id
        )
    )
