from fastapi import APIRouter

from app.models.schemas import DemoRun, SourceInput
from app.workflows.sofia_demo import run_sofia_demo

router = APIRouter(prefix="/v1")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/influencers/sofia/demo", response_model=DemoRun)
def sofia_demo(source: SourceInput) -> DemoRun:
    return run_sofia_demo(source)
