import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.ai.policy import TextAIPolicy
from app.api.routes import Context, authenticated, body_as
from app.db.repository import transaction

router = APIRouter(prefix="/v1")


@router.get("/text-ai-policy")
def policies(ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        return repo.all("text_ai_policies")


def _set(ctx, data):
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.require("ADMIN")
        pid = repo.connection.execute(
            text("SELECT set_text_ai_policy(CAST(:p AS jsonb))"),
            {"p": json.dumps(data.model_dump(mode="json"))},
        ).scalar_one()
        return repo.one("text_ai_policies", id=pid)


@router.post("/text-ai-policy")
async def set_policy(request: Request, ctx: Annotated[Context, Depends(authenticated)]):
    data = await body_as(request, TextAIPolicy)
    return await run_in_threadpool(_set, ctx, data)


@router.get("/workflow-runs/{workflow_id}/text-ai-attempts")
def attempts(workflow_id: UUID, ctx: Annotated[Context, Depends(authenticated)]):
    with transaction(ctx.tenant, ctx.token) as repo:
        repo.one("workflow_runs", id=workflow_id)
        # Input/result are already available as the authorized workflow artifacts.
        # This operational endpoint avoids copying complete source/persona prompts.
        return [
            {k: v for k, v in row.items() if k not in {"request", "context", "result"}}
            for row in sorted(
                repo.all("text_ai_attempts", workflow_run_id=workflow_id),
                key=lambda a: a["attempt"],
            )
        ]
