"""Read-only editorial heuristics. This module never grants approval or predicts reach."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.db.repository import canonical_hash
from app.models.schemas import (
    CarouselDraft,
    CharacterConfig,
    ContentBrief,
    QAFinding,
    QAReport,
    ResearchPack,
    StrictModel,
    WorkflowStatus,
)
from app.rendering.schemas import RenderManifest

DISCLAIMER = (
    "An editorial checklist, not a probability of going viral or a platform ranking signal. "
    "No engagement is predicted. This score cannot approve or publish content."
)
COMPONENTS = (
    ("hook", "Opening hook", 15),
    ("readability", "Reading density", 15),
    ("flow", "Slide structure", 10),
    ("cta", "Call to action", 10),
    ("format", "Instagram portrait format", 10),
    ("evidence", "Evidence and QA", 20),
    ("disclosure", "AI disclosure", 10),
    ("render", "Visual checks", 10),
)
Gate = Literal["BLOCKED", "REVISION_REQUIRED", "NOT_READY", "REQUIRES_APPROVAL_CHECKS"]


class RevisionIds(StrictModel):
    asset_version_id: UUID | None = None
    research_version_id: UUID | None = None
    qa_report_id: UUID | None = None
    render_run_id: UUID | None = None


class ReadinessInput(StrictModel):
    workflow_run_id: UUID
    state: WorkflowStatus
    evaluated_at: datetime
    current_ids: RevisionIds = Field(default_factory=RevisionIds)
    draft: CarouselDraft | None = None
    brief: ContentBrief | None = None
    research: ResearchPack | None = None
    config: CharacterConfig | None = None
    qa: QAReport | None = None
    qa_asset_version_id: UUID | None = None
    qa_research_version_id: UUID | None = None
    current_findings: list[QAFinding] = Field(default_factory=list)
    policy_findings_checked: bool = False
    current_asset_was_blocked: bool = False
    render: RenderManifest | None = None
    render_asset_version_id: UUID | None = None
    render_research_version_id: UUID | None = None
    render_qa_report_id: UUID | None = None
    render_config_current: bool = False

    @field_validator("evaluated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Readiness evaluation requires a timezone-aware timestamp")
        return value


class ReadinessIssue(StrictModel):
    code: str
    message: str


class ReadinessComponent(StrictModel):
    id: str
    label: str
    score: int | None = Field(ge=0, le=20)
    max_points: int = Field(ge=1, le=20)
    status: Literal["PASS", "IMPROVE", "BLOCKED", "NOT_SCORED"]
    reason: str
    suggestion: str | None = None


class ContentReadiness(StrictModel):
    schema_version: Literal[1] = 1
    algorithm_version: Literal["content-readiness-v1"] = "content-readiness-v1"
    workflow_run_id: UUID
    current_ids: RevisionIds
    evaluated_at: datetime
    score: int | None = Field(ge=0, le=100)
    scored_max_points: int = Field(ge=0, le=100)
    gate: Gate
    summary: str
    disclaimer: str = DISCLAIMER
    components: list[ReadinessComponent]
    blockers: list[ReadinessIssue]
    grants_publication_permission: Literal[False] = False


def score_readiness(data: ReadinessInput) -> ContentReadiness:
    """Summarize exact saved content; missing checks never receive invented points."""
    components: list[ReadinessComponent] = []
    blockers: dict[str, ReadinessIssue] = {}
    revision_needed = data.state == WorkflowStatus.REVISION_REQUIRED
    not_ready = False

    def block(code: str, message: str) -> None:
        blockers[code] = ReadinessIssue(code=code, message=message)

    def add(
        key: str,
        score: int | None,
        reason: str,
        suggestion: str | None = None,
        blocked: bool = False,
    ) -> None:
        _, label, maximum = next(row for row in COMPONENTS if row[0] == key)
        components.append(
            ReadinessComponent(
                id=key,
                label=label,
                score=score,
                max_points=maximum,
                status="NOT_SCORED"
                if score is None
                else "BLOCKED"
                if blocked
                else "PASS"
                if score == maximum
                else "IMPROVE",
                reason=reason,
                suggestion=suggestion,
            )
        )

    draft = data.draft
    if draft is None:
        for key, _, _ in COMPONENTS:
            add(key, None, "Create a saved carousel draft to evaluate this check.")
        return ContentReadiness(
            workflow_run_id=data.workflow_run_id,
            current_ids=data.current_ids,
            evaluated_at=data.evaluated_at,
            score=None,
            scored_max_points=0,
            gate="BLOCKED" if data.state == WorkflowStatus.BLOCKED else "NOT_READY",
            summary="No saved carousel draft to score.",
            components=components,
            blockers=[ReadinessIssue(code="WORKFLOW_BLOCKED", message="The workflow is blocked.")]
            if data.state == WorkflowStatus.BLOCKED
            else [],
        )

    ids = data.current_ids
    if not ids.asset_version_id or not ids.research_version_id or not data.brief or not data.config:
        block("UPSTREAM_MISSING", "The current artifact or upstream configuration is missing.")
    if data.state == WorkflowStatus.BLOCKED or data.current_asset_was_blocked:
        block("QA_BLOCKED", "QA blocked this exact content revision; a score cannot override it.")
    if data.state == WorkflowStatus.FAILED:
        not_ready = True
    if data.state not in {WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.APPROVED}:
        not_ready = True
    if not data.policy_findings_checked:
        not_ready = True
    findings = list(data.current_findings)
    qa_current = bool(
        data.qa
        and ids.qa_report_id
        and ids.asset_version_id
        and ids.research_version_id
        and data.qa_asset_version_id == ids.asset_version_id
        and data.qa_research_version_id == ids.research_version_id
    )
    if data.qa and not qa_current:
        block("STALE_QA", "Saved QA belongs to an older artifact; run fresh QA.")
    elif not data.qa:
        not_ready = True
    if data.qa:
        findings.extend(data.qa.findings)
        if data.qa.status == "BLOCKED":
            block(
                "QA_BLOCKED", "QA blocked this exact content revision; a score cannot override it."
            )
        revision_needed |= data.qa.status == "REVISION_REQUIRED"
    for finding in findings:
        if finding.severity == "BLOCKED":
            block(finding.code, finding.message)
        else:
            revision_needed = True

    hook_words = len(draft.slides[0].headline.text.split())
    hook_score = (
        15
        if 1 <= hook_words <= 12
        else 9
        if hook_words <= 20 and hook_words
        else 3
        if hook_words
        else 0
    )
    add(
        "hook",
        hook_score,
        f"Opening headline: {hook_words} words; the app target is 1–12.",
        None if hook_score == 15 else "Use a concise approved headline with one clear idea.",
    )
    density = max(len(f"{slide.headline.text} {slide.body.text}".split()) for slide in draft.slides)
    density_score = 15 if density <= 55 else 8 if density <= 85 else 2
    if any(
        not slide.headline.text.strip() or not slide.body.text.strip() for slide in draft.slides
    ):
        density_score = 0
    add(
        "readability",
        density_score,
        f"Densest slide: {density} words; the app target is at most 55, before visual overflow checks.",
        None
        if density_score == 15
        else "Split dense material into shorter slides; preserve evidence.",
    )
    ordered = [slide.index for slide in draft.slides] == list(range(1, len(draft.slides) + 1))
    complete = all(
        slide.headline.text.strip() and slide.body.text.strip() for slide in draft.slides
    )
    flow_score = (5 if ordered and complete else 0) + (
        5 if ordered and complete and len(draft.slides) >= 3 else 0
    )
    add(
        "flow",
        flow_score,
        f"{len(draft.slides)} slides; checks sequence and filled headline/body pairs.",
        None
        if flow_score == 10
        else "Use an opening, useful detail and closing in consecutive slides.",
    )

    cta_matches = bool(data.config and draft.cta.text == data.config.cta)
    cta_words = len(draft.cta.text.split())
    cta_score = (
        10
        if cta_matches
        and (
            1 <= cta_words <= 16 or data.config and data.config.cta_type == "NONE" and not cta_words
        )
        else 5
        if cta_matches
        else 0
    )
    if not cta_matches:
        block("CTA_POLICY", "The call to action does not match the configured policy.")
    add(
        "cta",
        cta_score,
        "Checks the configured CTA and an app target of at most 16 words.",
        None
        if cta_score == 10
        else "Use the configured CTA; change policy through a reviewed version.",
        blocked=not cta_matches,
    )

    render = data.render
    render_current = bool(
        render
        and ids.render_run_id
        and data.render_config_current
        and data.render_asset_version_id == ids.asset_version_id
        and data.render_research_version_id == ids.research_version_id
        and data.render_qa_report_id == ids.qa_report_id
        and render.draft_sha256 == canonical_hash(draft.model_dump(mode="json"))
    )
    if render and not render_current:
        block(
            "STALE_RENDER", "Render lineage, content hash or visual configuration is out of date."
        )
    if render_current and render:
        format_matches = len(render.slides) == len(draft.slides) and all(
            slide.width == 1080 and slide.height == 1350 for slide in render.slides
        )
        add(
            "format",
            10 if format_matches else 0,
            "Checks this app's Instagram portrait output: 1080 × 1350 (4:5), with every slide.",
            None if format_matches else "Render all slides with the portrait configuration.",
        )
    else:
        add(
            "format",
            None,
            "No current render to inspect; portrait format has not been checked.",
            "Render the current draft to check its output dimensions.",
        )
        not_ready = True

    blocks = [draft.caption, draft.cta] + [
        item for slide in draft.slides for item in (slide.headline, slide.body)
    ]
    facts = {fact.id: fact for fact in data.research.facts} if data.research else {}
    allowed = set(data.brief.allowed_fact_ids) if data.brief else set()
    claim_blocks = [item for item in blocks if item.kind == "FACT"]
    supported = bool(
        data.research and data.research.verification_status == "VERIFIED" and claim_blocks
    )
    for item in blocks:
        if item.kind == "CREATIVE":
            if item.fact_ids or not data.config or item.text not in data.config.creative_allowlist:
                supported = False
                block(
                    "UNSUPPORTED_CREATIVE", "Creative text must use approved non-factual templates."
                )
            continue
        selected = [facts[fid] for fid in item.fact_ids if fid in facts and fid in allowed]
        if (
            not selected
            or len(selected) != len(item.fact_ids)
            or any(
                fact.verification_status != "VERIFIED" or fact.end <= fact.start
                for fact in selected
            )
            or item.text != " ".join(fact.statement for fact in selected)
        ):
            supported = False
            block("UNSUPPORTED_CLAIM", "A factual block lacks exact allowed, verified evidence.")
    if not supported:
        block(
            "EVIDENCE_INCOMPLETE", "Verified research and supported factual content are required."
        )
    evidence_blocked = not supported or any(
        f.category == "EVIDENCE" and f.severity == "BLOCKED" for f in findings
    )
    evidence_pass = (
        supported
        and not evidence_blocked
        and qa_current
        and data.qa is not None
        and data.qa.status == "PASS"
        and data.policy_findings_checked
        and not data.current_asset_was_blocked
    )
    add(
        "evidence",
        20 if evidence_pass else 0,
        f"{len(claim_blocks)} factual blocks; checks allowed facts, exact excerpts and current QA.",
        None if evidence_pass else "Resolve evidence findings and run QA on these exact revisions.",
        blocked=evidence_blocked or bool(data.qa and data.qa.status == "BLOCKED"),
    )
    disclosure_matches = bool(
        data.config
        and data.config.disclosure.strip()
        and draft.disclosure == data.config.disclosure
    )
    if not disclosure_matches:
        block("DISCLOSURE", "The mandatory AI disclosure is missing or differs from policy.")
    add(
        "disclosure",
        10 if disclosure_matches else 0,
        "Checks the exact required AI disclosure from the current character configuration.",
        None if disclosure_matches else "Restore the required disclosure and run fresh QA.",
        blocked=not disclosure_matches,
    )

    if render_current and render:
        render_pass = (
            render.status == "PASS"
            and not render.findings
            and all(not slide.overflow for slide in render.slides)
        )
        if render.status == "BLOCKED" or any(f.severity == "BLOCKED" for f in render.findings):
            block("VISUAL_BLOCKED", "Visual QA blocked the current render.")
        elif not render_pass:
            revision_needed = True
        add(
            "render",
            10 if render_pass else 0,
            "Uses saved visual QA for exact content; human appearance review is still required.",
            None if render_pass else "Resolve visual findings and render the revised content.",
            blocked=render.status == "BLOCKED",
        )
    else:
        add(
            "render",
            None,
            "Current visual QA is unavailable.",
            "Render this revision, then inspect every slide.",
        )
    gate: Gate = (
        "BLOCKED"
        if blockers
        else "REVISION_REQUIRED"
        if revision_needed
        else "NOT_READY"
        if not_ready
        else "REQUIRES_APPROVAL_CHECKS"
    )
    summary = {
        "BLOCKED": "Resolve publication blockers; checklist points never override policy.",
        "REVISION_REQUIRED": "A revision is required before approval can be requested.",
        "NOT_READY": "Some checks are pending; unscored components earn no points.",
        "REQUIRES_APPROVAL_CHECKS": "Checklist evaluated; guarded human and publishing approvals still apply.",
    }[gate]
    return ContentReadiness(
        workflow_run_id=data.workflow_run_id,
        current_ids=ids,
        evaluated_at=data.evaluated_at,
        score=sum(item.score or 0 for item in components),
        scored_max_points=sum(item.max_points for item in components if item.score is not None),
        gate=gate,
        summary=summary,
        components=components,
        blockers=list(blockers.values()),
    )
