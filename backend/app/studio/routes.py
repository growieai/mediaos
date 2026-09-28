from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.routes import Context, authenticated, body_as
from app.db.repository import transaction
from app.studio.onboarding import onboarding_drafts
from app.studio.schemas import (
    CategoryCatalog,
    CreateInfluencer,
    InfluencerCatalog,
    OnboardingDraftRequest,
    OnboardingDrafts,
    StudioInfluencer,
    StudioOverview,
)
from app.studio.service import (
    categories,
    create_influencer,
    influencer,
    influencers,
    overview,
    portrait,
)

router = APIRouter(prefix="/v1/studio", tags=["studio"])


@router.post("/onboarding-drafts", response_model=OnboardingDrafts)
async def suggest_onboarding(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, OnboardingDraftRequest)
    with transaction(ctx.tenant, ctx.token) as repo:
        return onboarding_drafts(repo, data)


@router.get("/overview", response_model=StudioOverview)
def get_overview(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return overview(repo)


@router.get("/categories", response_model=CategoryCatalog)
def list_categories(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return categories(repo)


@router.get("/influencers", response_model=InfluencerCatalog)
def list_influencers(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return influencers(repo)


@router.get("/influencers/{influencer_id}", response_model=StudioInfluencer)
def get_influencer(influencer_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return influencer(repo, influencer_id)


@router.post("/influencers", response_model=StudioInfluencer)
async def submit_influencer(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, CreateInfluencer)
    return create_influencer(ctx.tenant, ctx.token, data, request.state.correlation_id)


@router.get("/influencers/{influencer_id}/portrait")
def get_portrait(influencer_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return Response(
            content=portrait(repo, influencer_id),
            media_type="image/png",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
