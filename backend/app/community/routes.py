from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.community.schemas import CommunityDecisionInput, CommunityEventInput, CommunityReviewInput
from app.community.service import (
    create_event,
    create_review,
    decide_review,
    event_details,
    execute_review,
    review_details,
    workflow_events,
)
from app.db.repository import transaction

router = APIRouter(prefix="/v1")


@router.post("/workflow-runs/{run_id}/community-events")
async def create(run_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, CommunityEventInput)
    return await run_in_threadpool(create_event, ctx.tenant, ctx.token, run_id, data)


@router.get("/workflow-runs/{run_id}/community-events")
def list_for_workflow(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return workflow_events(repo, run_id)


@router.get("/community-events/{event_id}")
def event(event_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return event_details(repo, event_id)


@router.post("/community-events/{event_id}/reviews")
async def review(event_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, CommunityReviewInput)
    return await run_in_threadpool(create_review, ctx.tenant, ctx.token, event_id, data)


@router.get("/community-reviews/{review_id}")
def details(review_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return review_details(repo, review_id)


@router.post("/community-reviews/{review_id}/execute")
def execute(review_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return execute_review(ctx.tenant, ctx.token, review_id)


def _decide(ctx, review_id, data, decision):
    with transaction(ctx.tenant, ctx.token) as repo:
        return decide_review(repo, review_id, data, decision)


@router.post("/community-reviews/{review_id}/approve")
async def approve(
    review_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, CommunityDecisionInput)
    return await run_in_threadpool(_decide, ctx, review_id, data, "APPROVE")


@router.post("/community-reviews/{review_id}/reject")
async def reject(
    review_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, CommunityDecisionInput)
    return await run_in_threadpool(_decide, ctx, review_id, data, "REJECT")
