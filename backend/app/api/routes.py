import json
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.repository import transaction
from app.models.schemas import (
    ApprovalInput,
    CarouselDraft,
    CreateRun,
    RunResponse,
    VerificationInput,
)
from app.services.workflows import Runner, artifacts, create_run, revise

router = APIRouter(prefix="/v1")


@dataclass
class Context:
    tenant: UUID
    token: str


def authenticated(
    x_tenant_id: Annotated[UUID | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    if x_tenant_id is None or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Tenant and bearer credential required")
    token = authorization[7:]
    with transaction(x_tenant_id, token):
        pass
    return Context(x_tenant_id, token)


async def body_as(request: Request, schema):
    try:
        return schema.model_validate_json(await request.body(), strict=True)
    except (ValueError, TypeError):
        raise HTTPException(422, "Request does not satisfy the typed schema") from None


def run_response(run):
    return RunResponse.model_validate_json(
        json.dumps({key: run[key] for key in RunResponse.model_fields}, default=str), strict=True
    )


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/readiness")
def readiness(ctx: Annotated[Context, Depends(authenticated)]):
    try:
        with transaction(ctx.tenant, ctx.token) as repo:
            row = repo.connection.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            ).one()
            if row.current_user != "mediaos_runtime" or row.rolsuper or row.rolbypassrls:
                return Response(status_code=503)
            repo.all("workflow_runs")
        return {"status": "ready", "database": "ready", "schema": "0008", "mode": "deterministic"}
    except DBAPIError:
        return Response(status_code=503)


@router.get("/context")
def context_info(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return {
            "tenant_id": ctx.tenant,
            "principal_id": repo.principal_id,
            "memberships": repo.all("tenant_memberships", principal_id=repo.principal_id),
            "influencers": repo.all("influencers"),
            "missions": repo.all("missions"),
        }


@router.post("/workflow-runs", response_model=RunResponse)
async def submit(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, CreateRun)
    return run_response(create_run(ctx.tenant, ctx.token, data, request.state.correlation_id))


@router.get("/workflow-runs", response_model=list[RunResponse])
def list_runs(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return [run_response(r) for r in repo.all("workflow_runs")]


@router.get("/workflow-runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return run_response(repo.one("workflow_runs", id=run_id))


@router.get("/workflow-runs/{run_id}/artifacts")
def get_artifacts(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return artifacts(repo, run_id)


@router.get("/workflow-runs/{run_id}/audit")
def get_audit(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.one("workflow_runs", id=run_id)
        return repo.all("audit_events", workflow_run_id=run_id)


@router.post("/workflow-runs/{run_id}/execute", response_model=RunResponse)
def execute(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return run_response(Runner(ctx.tenant, ctx.token).execute(run_id))


@router.post("/source-snapshots/{source_id}/verify")
async def verify_source(
    source_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, VerificationInput)
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.require("APPROVER")
        repo.connection.execute(
            text("SELECT verify_source(:sid,:decision,:comment)"),
            {"sid": source_id, "decision": data.decision, "comment": data.comment},
        )
    return {"status": data.decision}


async def decide(run_id, decision, request, ctx):
    data = await body_as(request, ApprovalInput)
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.require("APPROVER")
        aid = repo.connection.execute(
            text("SELECT decide_approval(:rid,:aid,:rpid,:qid,:decision,:comment)"),
            {
                "rid": run_id,
                "aid": data.asset_version_id,
                "rpid": data.research_version_id,
                "qid": data.qa_report_id,
                "decision": decision,
                "comment": data.comment,
            },
        ).scalar_one()
        return {
            "approval_record_id": aid,
            "workflow": run_response(repo.one("workflow_runs", id=run_id)),
        }


@router.post("/workflow-runs/{run_id}/approve")
async def approve(run_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    return await decide(run_id, "APPROVE", request, ctx)


@router.post("/workflow-runs/{run_id}/reject")
async def reject(run_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    return await decide(run_id, "REJECT", request, ctx)


@router.post("/workflow-runs/{run_id}/revisions", response_model=RunResponse)
async def revision(run_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    draft = await body_as(request, CarouselDraft)
    return run_response(revise(ctx.tenant, ctx.token, run_id, draft))
