from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.db.repository import transaction
from app.media import service
from app.media.schemas import MediaDecision, MediaInput, ProfileInput, SpendPolicy

router = APIRouter(prefix="/v1")


@router.get("/media-dependencies")
def dependencies(ctx: Annotated[Context, Depends(authenticated)]):
    return service.dependencies()


@router.get("/influencers/{influencer_id}/media-profiles")
def profiles(influencer_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.one("influencers", id=influencer_id)
        return repo.all("media_profiles", influencer_id=influencer_id)


@router.post("/influencers/{influencer_id}/media-profiles")
async def create_profile(
    influencer_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, ProfileInput)
    return await run_in_threadpool(
        service.create_profile, ctx.tenant, ctx.token, influencer_id, data
    )


@router.get("/media-spend-policy")
def policies(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return repo.all("media_spend_policies")


@router.post("/media-spend-policy")
async def set_policy(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, SpendPolicy)
    return await run_in_threadpool(service.spend_policy, ctx.tenant, ctx.token, data)


@router.get("/workflow-runs/{workflow_id}/media-runs")
def workflow_media(workflow_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return service.workflow_media(repo, workflow_id)


@router.post("/workflow-runs/{workflow_id}/media-runs")
async def create(
    workflow_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    data = await body_as(request, MediaInput)
    return await run_in_threadpool(service.create, ctx.tenant, ctx.token, workflow_id, data)


@router.get("/media-runs/{media_id}")
def detail(media_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return service.details(repo, media_id)


@router.post("/media-runs/{media_id}/execute")
def execute(media_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return service.execute(ctx.tenant, ctx.token, media_id)


@router.post("/media-runs/{media_id}/review")
async def review(media_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, MediaDecision)
    return await run_in_threadpool(service.review, ctx.tenant, ctx.token, media_id, data)


@router.get("/media-runs/{media_id}/video")
def video(media_id: UUID, ctx: Annotated[Context, Depends(authenticated)], export: bool = False):
    with transaction(ctx.tenant, ctx.token) as repo:
        content = service.video(repo, media_id, export)
    return Response(
        content,
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'{"attachment" if export else "inline"}; filename="video.mp4"',
            "X-Content-Type-Options": "nosniff",
        },
    )
