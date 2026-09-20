from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.db.repository import transaction
from app.delivery.schemas import DeliveryInput
from app.delivery.service import (
    create_delivery,
    delivery_details,
    execute_delivery,
    workflow_deliveries,
)

router = APIRouter(prefix="/v1")


@router.get("/delivery-targets")
def targets(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return repo.all("delivery_targets")


@router.post("/renders/{render_id}/deliveries")
async def create(
    render_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, DeliveryInput)
    return await run_in_threadpool(create_delivery, ctx.tenant, ctx.token, render_id, data)


@router.get("/workflow-runs/{run_id}/deliveries")
def list_for_workflow(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return workflow_deliveries(repo, run_id)


@router.get("/deliveries/{delivery_id}")
def details(delivery_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return delivery_details(repo, delivery_id)


@router.post("/deliveries/{delivery_id}/execute")
def execute(delivery_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return execute_delivery(ctx.tenant, ctx.token, delivery_id)
