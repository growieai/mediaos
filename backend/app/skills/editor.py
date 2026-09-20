from app.models.schemas import CharacterConfig, ContentBrief, ResearchPack


def build_brief(
    research: ResearchPack,
    config: CharacterConfig,
    mission_objective: str,
    audience: list[str] | None = None,
) -> ContentBrief:
    return ContentBrief(
        allowed_fact_ids=[f.id for f in research.facts],
        audience=audience if audience is not None else config.audience,
        franchise=config.franchise,
        objective=mission_objective,
        tone=config.tone,
        cta_type=config.cta_type,
        brand_association_level=config.brand_association_level,
        constraints={"min_slides": config.min_slides, "max_slides": config.max_slides},
    )
