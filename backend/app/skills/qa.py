from app.models.schemas import CarouselDraft, QAReport, ResearchPack


def qa_carousel_mock(draft: CarouselDraft, research: ResearchPack) -> QAReport:
    reasons: list[str] = []
    required_changes: list[str] = []

    if not research.publishable:
        reasons.append("Research pack is not publishable.")
        return QAReport(
            status="BLOCK",
            fact_score=0.2,
            brand_score=0.9,
            policy_score=0.9,
            reasons=reasons,
            required_changes=["Obtain an official or approved source."],
        )

    if not draft.disclosure:
        required_changes.append("Add AI identity disclosure.")

    if research.confidence < 0.9:
        required_changes.append("Research confidence below auto-approval threshold.")

    status = "REVISE" if required_changes else "APPROVE"
    reasons.append("Claims are constrained to the supplied ResearchPack.")
    reasons.append("AI identity disclosure is present." if draft.disclosure else "Missing disclosure.")

    return QAReport(
        status=status,
        fact_score=research.confidence,
        brand_score=0.95,
        policy_score=0.98 if draft.disclosure else 0.6,
        reasons=reasons,
        required_changes=required_changes,
    )
