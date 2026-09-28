"""Offline heuristics regressions; no database, credentials or provider calls."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.repository import canonical_hash
from app.models.schemas import (
    CarouselDraft,
    CarouselSlide,
    CharacterConfig,
    ContentBrief,
    Fact,
    QAFinding,
    QAReport,
    ResearchPack,
    TextBlock,
    WorkflowStatus,
)
from app.rendering.schemas import RenderedSlide, RenderManifest, TextCoverage
from app.studio.readiness import ReadinessInput, RevisionIds, score_readiness


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def example():
    fact = Fact(
        id=uuid4(),
        source_snapshot_id=uuid4(),
        start=0,
        end=30,
        statement="Applications open in October.",
        verification_status="VERIFIED",
        confidence=1.0,
        fact_type="GENERAL",
    )
    config = CharacterConfig(
        persona="A virtual educator",
        voice="Direct",
        visual_policy="Readable",
        brand_policy="Helpful",
        language="en",
        audience=["Local businesses"],
        franchise="Practical updates",
        objective="Explain",
        tone="Clear",
        disclosure="AI creator.",
        headline="Check the opportunity",
        cta="Read the source.",
        brand_association_level=0,
        max_brand_association_level=0,
        min_slides=1,
        max_slides=20,
        creative_allowlist=[
            "Check the opportunity",
            "Details to know",
            "Before you apply",
            "Read the source.",
        ],
    )
    draft = CarouselDraft(
        influencer_version_id=uuid4(),
        language="en",
        brand_association_level=0,
        slides=[
            CarouselSlide(
                index=n,
                headline=TextBlock(kind="CREATIVE", text=heading),
                body=TextBlock(kind="FACT", text=fact.statement, fact_ids=[fact.id]),
            )
            for n, heading in enumerate(config.creative_allowlist[:3], 1)
        ],
        caption=TextBlock(kind="FACT", text=fact.statement, fact_ids=[fact.id]),
        cta=TextBlock(kind="CREATIVE", text=config.cta),
        disclosure=config.disclosure,
    )
    ids = RevisionIds(
        asset_version_id=uuid4(),
        research_version_id=uuid4(),
        qa_report_id=uuid4(),
        render_run_id=uuid4(),
    )
    coverage = TextCoverage(
        field_path="caption", text=draft.caption.text, text_sha256="0" * 64, exact_coverage=True
    )
    manifest = RenderManifest(
        pillow_version="12.3.0",
        status="PASS",
        draft_sha256=canonical_hash(draft.model_dump(mode="json")),
        visual_config_sha256="0" * 64,
        font_sha256={},
        influencer_version_id=str(draft.influencer_version_id),
        language="en",
        caption=draft.caption,
        caption_coverage=coverage,
        slides=[
            RenderedSlide(
                index=n,
                filename=f"slide-{n:02d}.png",
                sha256="0" * 64,
                text_coverage=[],
                overflow=False,
            )
            for n in range(1, 4)
        ],
        findings=[],
    )
    return ReadinessInput(
        workflow_run_id=uuid4(),
        state=WorkflowStatus.AWAITING_APPROVAL,
        evaluated_at=datetime(2026, 9, 28, tzinfo=UTC),
        current_ids=ids,
        draft=draft,
        brief=ContentBrief(
            allowed_fact_ids=[fact.id],
            audience=config.audience,
            franchise=config.franchise,
            objective=config.objective,
            tone=config.tone,
            cta_type=config.cta_type,
            brand_association_level=0,
            constraints={},
        ),
        research=ResearchPack(facts=[fact], verification_status="VERIFIED"),
        config=config,
        qa=QAReport(status="PASS", findings=[]),
        qa_asset_version_id=ids.asset_version_id,
        qa_research_version_id=ids.research_version_id,
        policy_findings_checked=True,
        render=manifest,
        render_asset_version_id=ids.asset_version_id,
        render_research_version_id=ids.research_version_id,
        render_qa_report_id=ids.qa_report_id,
        render_config_current=True,
    )


def component(result, name):
    return next(item for item in result.components if item.id == name)


def test_no_saved_content_is_not_a_fabricated_zero_or_perfect_score():
    value = ReadinessInput(
        workflow_run_id=uuid4(), state=WorkflowStatus.CREATED, evaluated_at=datetime.now(UTC)
    )
    result = score_readiness(value)
    assert result.score is None
    assert result.scored_max_points == 0
    assert result.gate == "NOT_READY"
    assert all(item.score is None and item.status == "NOT_SCORED" for item in result.components)


def test_complete_checklist_is_not_publication_permission_or_predicted_engagement():
    result = score_readiness(example())
    assert result.score == 100
    assert result.scored_max_points == 100
    assert result.gate == "REQUIRES_APPROVAL_CHECKS"
    assert result.grants_publication_permission is False
    assert "not a probability" in result.disclaimer
    assert sum(item.max_points for item in result.components) == 100
    assert "reach" not in result.model_dump()


@pytest.mark.parametrize("mode", ["current", "history", "workflow"])
def test_block_cannot_be_outscored(mode):
    value = example()
    if mode == "current":
        value.qa = QAReport(status="BLOCKED", findings=[])
    elif mode == "history":
        value.current_asset_was_blocked = True
    else:
        value.state = WorkflowStatus.BLOCKED
    result = score_readiness(value)
    assert result.gate == "BLOCKED"
    assert result.score >= 70
    assert "QA_BLOCKED" in {item.code for item in result.blockers}
    assert not result.grants_publication_permission


def test_revision_required_is_distinct_from_blocked():
    value = example()
    value.qa = QAReport(
        status="REVISION_REQUIRED",
        findings=[
            QAFinding(
                code="SLIDE_ORDER",
                category="CONTENT",
                severity="REVISION_REQUIRED",
                message="Reorder slides.",
            )
        ],
    )
    result = score_readiness(value)
    assert result.gate == "REVISION_REQUIRED"
    assert not result.blockers


@pytest.mark.parametrize("field", ["qa_asset_version_id", "qa_research_version_id"])
def test_old_qa_never_qualifies_current_content(field):
    value = example()
    setattr(value, field, uuid4())
    result = score_readiness(value)
    assert result.gate == "BLOCKED"
    assert component(result, "evidence").score == 0
    assert "STALE_QA" in {item.code for item in result.blockers}


@pytest.mark.parametrize(
    "code", ["STALE_RESEARCH", "GRANT_EXPIRED", "OFFICIAL_SOURCE_CONFLICT", "INELIGIBLE_EVIDENCE"]
)
def test_fresh_database_evidence_findings_veto_saved_qa_pass(code):
    value = example()
    value.current_findings = [
        QAFinding(code=code, category="EVIDENCE", severity="BLOCKED", message=code)
    ]
    result = score_readiness(value)
    assert result.gate == "BLOCKED"
    assert component(result, "evidence").score == 0


def test_missing_live_policy_check_remains_not_ready_even_with_saved_pass():
    value = example()
    value.policy_findings_checked = False
    result = score_readiness(value)
    assert result.gate == "NOT_READY"
    assert component(result, "evidence").score == 0


def test_missing_render_is_not_scored_and_does_not_rescale_other_points():
    value = example()
    value.render = None
    value.current_ids.render_run_id = None
    result = score_readiness(value)
    assert result.score == result.scored_max_points == 80
    assert result.gate == "NOT_READY"
    assert component(result, "render").score is None
    assert component(result, "format").score is None


@pytest.mark.parametrize(
    "field", ["render_asset_version_id", "render_research_version_id", "render_qa_report_id"]
)
def test_old_render_is_not_scored_as_current(field):
    value = example()
    setattr(value, field, uuid4())
    result = score_readiness(value)
    assert result.gate == "BLOCKED"
    assert component(result, "render").score is None


def test_modified_content_hash_requires_a_new_render():
    value = example()
    value.draft.slides[0].headline.text = "Details to know"
    assert "STALE_RENDER" in {item.code for item in score_readiness(value).blockers}


def test_new_visual_configuration_invalidates_old_render_check():
    value = example()
    value.render_config_current = False
    assert score_readiness(value).gate == "BLOCKED"


@pytest.mark.parametrize(
    "status,gate", [("BLOCKED", "BLOCKED"), ("REVISION_REQUIRED", "REVISION_REQUIRED")]
)
def test_visual_qa_is_separate_and_fail_closed(status, gate):
    value = example()
    value.render.status = status
    result = score_readiness(value)
    assert result.gate == gate
    assert component(result, "render").score == 0


@pytest.mark.parametrize(
    "damage", ["missing_id", "fabricated_id", "wrong_pack", "wrong_excerpt", "unverified"]
)
def test_unsupported_claim_is_not_a_good_evidence_score(damage):
    value = example()
    fact = value.research.facts[0]
    if damage == "missing_id":
        value.draft.slides[0].body.fact_ids = []
    elif damage == "fabricated_id":
        value.draft.slides[0].body.fact_ids = [uuid4()]
    elif damage == "wrong_pack":
        value.brief.allowed_fact_ids = []
    elif damage == "wrong_excerpt":
        value.draft.slides[0].body.text = "Everyone qualifies for free money."
    else:
        fact.verification_status = "UNVERIFIED"
    result = score_readiness(value)
    assert result.gate == "BLOCKED"
    assert component(result, "evidence").score == 0


def test_unapproved_creative_text_cannot_hide_an_unsupported_claim():
    value = example()
    value.draft.caption = TextBlock(kind="CREATIVE", text="All companies receive 10000 euros.")
    result = score_readiness(value)
    assert result.gate == "BLOCKED"
    assert "UNSUPPORTED_CREATIVE" in {item.code for item in result.blockers}


def test_missing_disclosure_is_a_veto():
    value = example()
    value.draft.disclosure = ""
    result = score_readiness(value)
    assert result.gate == "BLOCKED"
    assert component(result, "disclosure").score == 0


def test_adding_words_to_dense_slides_cannot_improve_density_score():
    values = []
    for count in (20, 60, 90):
        value = example()
        value.draft.slides[0].body.text = "word " * count
        values.append(component(score_readiness(value), "readability").score)
    assert values == [15, 8, 2]


def test_shortening_long_hook_improves_only_an_editorial_heuristic():
    scores = []
    for count in (30, 18, 8):
        value = example()
        value.draft.slides[0].headline.text = "word " * count
        result = score_readiness(value)
        scores.append(component(result, "hook").score)
        assert result.gate == "BLOCKED"  # Unsupported edited text still needs real QA.
    assert scores == [3, 9, 15]


def test_schema_rejects_untyped_or_extra_predicted_reach():
    result = score_readiness(example()).model_dump(mode="json")
    result["predicted_reach"] = 100000
    from app.studio.readiness import ContentReadiness

    with pytest.raises(ValidationError):
        ContentReadiness.model_validate_json(__import__("json").dumps(result), strict=True)


def test_evaluation_is_deterministic_and_preserves_exact_ids():
    value = example()
    first, second = score_readiness(value), score_readiness(value)
    assert first == second
    assert first.current_ids == value.current_ids
    assert first.evaluated_at == value.evaluated_at


def test_naive_evaluation_time_is_rejected():
    with pytest.raises(ValidationError):
        ReadinessInput(
            workflow_run_id=uuid4(),
            state=WorkflowStatus.CREATED,
            evaluated_at=datetime(2026, 9, 28),
        )
