from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.config import get_settings
from app.conversion import delivery, service
from app.conversion.delivery_schemas import (
    BusinessIdentityInput,
    BusinessReviewInput,
    DeliveryAuthorization,
    DeliveryInput,
    RevocationDeliveryInput,
)
from app.conversion.schemas import (
    ConversionExportInput,
    ConversionRequestInput,
    ConversionReviewInput,
    ConversionRevokeInput,
    DestinationInput,
)
from app.db.repository import transaction

router = APIRouter(prefix="/v1")


@router.get("/conversion-dependencies")
def dependencies(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token):
        settings = get_settings()
        return {
            "live_enabled": settings.conversion_delivery_enabled,
            "credentials_configured": bool(settings.conversion_delivery_credentials),
            "local_retention_purge_implemented": False,
        }


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


@router.get("/business-identities")
def businesses(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return [
            delivery.business_details(repo, row["id"])
            for row in delivery.rows(repo, "business_identities")
        ]


@router.post("/business-identities")
async def create_business(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, BusinessIdentityInput)
    return await run_in_threadpool(delivery.create_business, ctx.tenant, ctx.token, data)


@router.post("/business-identities/{identity_id}/review")
async def review_business(
    identity_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, BusinessReviewInput)
    return await run_in_threadpool(
        delivery.review_business, ctx.tenant, ctx.token, identity_id, data
    )


@router.get("/conversion-transports")
def transports(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return delivery.rows(repo, "conversion_transports")


@router.get("/conversion-requests/{request_id}/deliveries")
def deliveries(request_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.one("conversion_requests", id=request_id)
        return [
            delivery.details(repo, row["id"])
            for row in delivery.rows(repo, "conversion_deliveries", request_id=request_id)
        ]


@router.post("/conversion-requests/{request_id}/deliveries")
async def create_delivery(
    request_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, DeliveryInput)
    return await run_in_threadpool(delivery.create, ctx.tenant, ctx.token, request_id, data)


@router.get("/conversion-deliveries/{delivery_id}")
def delivery_detail(delivery_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return delivery.details(repo, delivery_id)


@router.post("/conversion-deliveries/{delivery_id}/authorize")
async def authorize_delivery(
    delivery_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, DeliveryAuthorization)
    return await run_in_threadpool(delivery.authorize, ctx.tenant, ctx.token, delivery_id, data)


@router.post("/conversion-deliveries/{delivery_id}/dispatch")
async def dispatch_delivery(delivery_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return await run_in_threadpool(delivery.execute, ctx.tenant, ctx.token, delivery_id)


@router.post("/conversion-deliveries/{delivery_id}/reconcile")
async def reconcile_delivery(delivery_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return await run_in_threadpool(
        delivery.execute, ctx.tenant, ctx.token, delivery_id, reconcile=True
    )


@router.post("/conversion-deliveries/{delivery_id}/revocation")
async def revoke_delivery(
    delivery_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, RevocationDeliveryInput)
    return await run_in_threadpool(delivery.revoke, ctx.tenant, ctx.token, delivery_id, data)
