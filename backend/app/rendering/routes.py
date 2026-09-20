from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.routes import Context, authenticated, body_as
from app.db.repository import transaction
from app.rendering.service import (
    RenderInput,
    VisualDecision,
    create_render,
    decide_visual,
    execute_render,
    export_render,
    preview_slide,
    render_details,
    workflow_renders,
)

router = APIRouter(prefix="/v1")


@router.get("/workflow-runs/{run_id}/renders")
def list_renders(run_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return workflow_renders(repo, run_id)


@router.post("/workflow-runs/{run_id}/renders")
async def render(run_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, RenderInput)
    return await run_in_threadpool(create_render, ctx.tenant, ctx.token, run_id, data)


@router.get("/renders/{render_id}")
def get_render(render_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return render_details(repo, render_id)


@router.post("/renders/{render_id}/execute")
def resume(render_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    return execute_render(ctx.tenant, ctx.token, render_id)


@router.get("/renders/{render_id}/slides/{index}")
def slide(render_id: UUID, index: int, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return Response(
            preview_slide(repo, render_id, index),
            media_type="image/png",
            headers={"X-Content-Type-Options": "nosniff"},
        )


@router.get("/renders/{render_id}/export")
def export(render_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return Response(
            export_render(repo, render_id),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="carousel-{render_id}.zip"',
                "X-Content-Type-Options": "nosniff",
            },
        )


async def decide(render_id, decision, request, ctx):
    data = await body_as(request, VisualDecision)
    return await run_in_threadpool(decide_visual, ctx.tenant, ctx.token, render_id, decision, data)


@router.post("/renders/{render_id}/approve")
async def approve(
    render_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    return await decide(render_id, "APPROVE", request, ctx)


@router.post("/renders/{render_id}/reject")
async def reject(
    render_id: UUID, request: Request, ctx: Annotated[Context, Depends(authenticated)]
):
    return await decide(render_id, "REJECT", request, ctx)
