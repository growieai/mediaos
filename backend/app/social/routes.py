from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.config import get_settings
from app.db.repository import transaction
from app.social import service
from app.social.schemas import (
    ConnectInput,
    DispatchInput,
    ObservationInput,
    PublishDecision,
    PublishInput,
    ReconcileInput,
)

router = APIRouter(prefix="/v1")


@router.get("/social/dependencies")
def dependencies(ctx: Annotated[Context, Depends(authenticated)]):
    return service.dependencies()


@router.get("/social/connections")
def connections(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return {
            "connections": repo.all("social_connections"),
            "revocations": repo.all("social_revocations"),
        }


@router.post("/social/connect")
async def connect(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, ConnectInput)
    return await run_in_threadpool(service.begin_connect, ctx.tenant, ctx.token, data)


@router.get("/social/oauth/callback")
def callback(state: str, code: str):
    return service.finish_connect(state, code)


@router.post("/social/connections/{cid}/refresh")
def refresh(cid: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return service.refresh_connection(ctx.tenant, ctx.token, cid)


@router.post("/social/connections/{cid}/revoke")
def revoke(cid: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    from sqlalchemy import text

    with transaction(ctx.tenant, ctx.token) as repo:
        repo.connection.execute(text("SELECT social_revoke(:id)"), {"id": cid})
    return {"status": "REVOKED"}


@router.get("/workflow-runs/{wid}/social-publishes")
def workflow_publishes(wid: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.one("workflow_runs", id=wid)
        return [
            service.details(repo, row["id"])
            for row in repo.all("social_publish_runs", workflow_run_id=wid)
        ]


@router.post("/renders/{rid}/social-publishes")
async def create(rid: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, PublishInput)
    return await run_in_threadpool(service.create, ctx.tenant, ctx.token, rid, data)


@router.get("/social-publishes/{pid}")
def detail(pid: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return service.details(repo, pid)


@router.get("/social-publishes/{pid}/slides/{index}")
def preview(pid: UUID, index: int, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return Response(service.preview(repo, pid, index), media_type="image/jpeg")


@router.post("/social-publishes/{pid}/review")
async def review(pid: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, PublishDecision)
    return await run_in_threadpool(service.review, ctx.tenant, ctx.token, pid, data)


@router.post("/social-publishes/{pid}/execute")
async def execute(pid: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    await body_as(request, DispatchInput)
    return await run_in_threadpool(service.execute, ctx.tenant, ctx.token, pid)


@router.post("/social-publishes/{pid}/insights")
async def insights(pid: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, ObservationInput)
    return await run_in_threadpool(
        service.observe, ctx.tenant, ctx.token, pid, data.idempotency_key
    )


@router.post("/social-publishes/{pid}/reconcile")
async def reconcile(pid: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, ReconcileInput)
    return await run_in_threadpool(service.reconcile, ctx.tenant, ctx.token, pid, data)


@router.post("/social-publishes/{pid}/comments/relink")
def relink_comments(pid: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return service.relink_comments(ctx.tenant, ctx.token, pid)


@router.get("/social/media/{capability}")
def public_media(capability: str):
    return Response(
        service.public_image(capability),
        media_type="image/jpeg",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.get("/social/webhook")
def challenge(request: Request):
    from app.social_provider import webhook_challenge

    settings = get_settings()
    if not settings.social_connect_enabled or not settings.social_webhook_verify_token:
        raise PermissionError("Webhook intake is disabled")
    q = request.query_params
    result = webhook_challenge(
        q.get("hub.mode", ""),
        q.get("hub.verify_token", ""),
        q.get("hub.challenge", ""),
        settings.social_webhook_verify_token,
    )
    return Response(result, media_type="text/plain")


@router.post("/social/webhook")
async def webhook(request: Request):
    return await run_in_threadpool(
        service.receive_webhook,
        await request.body(),
        request.headers.get("x-hub-signature-256", ""),
    )
