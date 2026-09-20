from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.models.schemas import DemoRun, SourceInput, WorkflowStatus
from app.skills.creator import create_carousel_mock
from app.skills.editor import build_brief_mock
from app.skills.qa import qa_carousel_mock
from app.skills.research import research_source_mock


def run_sofia_demo(source: SourceInput) -> DemoRun:
    research = research_source_mock(source)
    brief = build_brief_mock(research)
    draft = create_carousel_mock(brief, research)
    qa = qa_carousel_mock(draft, research)
    status = WorkflowStatus.APPROVED if qa.status == "APPROVE" else WorkflowStatus.BLOCKED

    return DemoRun(
        tenant_id="growie",
        influencer_id="sofia_es",
        status=status,
        research=research,
        brief=brief,
        draft=draft,
        qa=qa,
    )


def save_run(run: DemoRun) -> Path:
    root = get_settings().repo_root
    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = runs_dir / f"sofia_demo_{stamp}.json"
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    return path


def main() -> None:
    source = SourceInput(
        source_id="demo_official_001",
        title="Demo: Spain SMB digitalisation support update",
        source_type="manual_test",
        market="ES",
        raw_text=(
            "DEMO SOURCE ONLY. A public programme announces support for eligible small businesses "
            "to adopt digital tools. Eligibility, amount, dates and covered expenses must be checked "
            "against the official programme notice before any public factual claim is made."
        ),
    )
    run = run_sofia_demo(source)
    path = save_run(run)
    print(run.model_dump_json(indent=2))
    print(f"\nSaved run: {path}")


if __name__ == "__main__":
    main()
