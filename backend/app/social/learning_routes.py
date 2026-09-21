from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.db.repository import transaction
from app.metrics.schemas import MetricLearningInput
from app.social import learning

router = APIRouter(prefix="/v1")


@router.get("/social-publishes/{pid}/learning")
def reports(pid: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return learning.list_reports(repo, pid)


@router.post("/social-publishes/{pid}/learning")
async def create(pid: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, MetricLearningInput)
    return await run_in_threadpool(learning.create, ctx.tenant, ctx.token, pid, data)
