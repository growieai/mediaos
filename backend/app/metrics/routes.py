from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.db.repository import transaction
from app.metrics.schemas import MetricLearningInput, MetricSnapshotInput, MetricSubjectInput
from app.metrics.service import (
    create_learning,
    create_subject,
    import_snapshot,
    learning_reports,
    subject_details,
    workflow_subjects,
)

router = APIRouter(prefix="/v1")


@router.post("/workflow-runs/{run_id}/metric-subjects")
async def register_subject(
    run_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, MetricSubjectInput)
    return await run_in_threadpool(create_subject, ctx.tenant, ctx.token, run_id, data)


@router.get("/workflow-runs/{run_id}/metric-subjects")
def list_subjects(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return workflow_subjects(repo, run_id)


@router.get("/metric-subjects/{subject_id}")
def get_subject(subject_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return subject_details(repo, subject_id)


@router.post("/metric-subjects/{subject_id}/snapshots")
async def add_snapshot(
    subject_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, MetricSnapshotInput)
    return await run_in_threadpool(import_snapshot, ctx.tenant, ctx.token, subject_id, data)


@router.post("/metric-subjects/{subject_id}/learning-reports")
async def add_learning(
    subject_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, MetricLearningInput)
    return await run_in_threadpool(create_learning, ctx.tenant, ctx.token, subject_id, data)


@router.get("/metric-subjects/{subject_id}/learning-reports")
def list_learning(subject_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return learning_reports(repo, subject_id)
