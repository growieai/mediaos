from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.conversion import service
from app.conversion.schemas import (
    ConversionExportInput,
    ConversionRequestInput,
    ConversionReviewInput,
    ConversionRevokeInput,
    DestinationInput,
)
from app.db.repository import transaction

router = APIRouter(prefix="/v1")


@router.get("/conversion-destinations")
def destinations(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return service.destinations(repo)


@router.post("/conversion-destinations")
async def create_destination(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, DestinationInput)
    return await run_in_threadpool(service.create_destination, ctx.tenant, ctx.token, data)


@router.get("/workflow-runs/{run_id}/conversion-requests")
def requests(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return service.workflow_requests(repo, run_id)


@router.post("/workflow-runs/{run_id}/conversion-requests")
async def create_request(
    run_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, ConversionRequestInput)
    return await run_in_threadpool(service.create_request, ctx.tenant, ctx.token, run_id, data)


@router.get("/conversion-requests/{request_id}")
def detail(request_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return service.request_details(repo, request_id)


@router.post("/conversion-requests/{request_id}/review")
async def review(
    request_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, ConversionReviewInput)
    return await run_in_threadpool(service.review_request, ctx.tenant, ctx.token, request_id, data)


@router.post("/conversion-requests/{request_id}/revoke")
async def revoke(
    request_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, ConversionRevokeInput)
    return await run_in_threadpool(service.revoke_request, ctx.tenant, ctx.token, request_id, data)


@router.post("/conversion-requests/{request_id}/export")
async def export(
    request_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, ConversionExportInput)
    return await run_in_threadpool(service.export_request, ctx.tenant, ctx.token, request_id, data)
