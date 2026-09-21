from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.db.repository import transaction
from app.social import replies

router = APIRouter(prefix="/v1")


@router.post("/community-reviews/{review_id}/reply-dispatches")
async def create(
    review_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, replies.ReplyInput)
    return await run_in_threadpool(replies.create, ctx.tenant, ctx.token, review_id, data)


@router.get("/community-reviews/{review_id}/reply-dispatches")
def list_for_review(review_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return replies.for_review(repo, review_id)


@router.get("/social-reply-runs/{reply_id}")
def details(reply_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return replies.details(repo, reply_id)


@router.post("/social-reply-runs/{reply_id}/review")
async def decide(reply_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, replies.ReplyDecision)
    return await run_in_threadpool(replies.decide, ctx.tenant, ctx.token, reply_id, data)


@router.post("/social-reply-runs/{reply_id}/execute")
def execute(reply_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return replies.execute(ctx.tenant, ctx.token, reply_id)
