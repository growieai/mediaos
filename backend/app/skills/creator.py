from app.models.schemas import (
    CarouselDraft,
    CarouselSlide,
    CharacterConfig,
    ResearchPack,
    TextBlock,
)


def create_carousel(
    research: ResearchPack, config: CharacterConfig, influencer_version_id
) -> CarouselDraft:
    def creative(text):
        return TextBlock(kind="CREATIVE", text=text)

    return CarouselDraft(
        influencer_version_id=influencer_version_id,
        language=config.language,
        brand_association_level=config.brand_association_level,
        slides=[
            CarouselSlide(
                index=i + 1,
                headline=creative(config.headline),
                body=TextBlock(kind="FACT", text=f.statement, fact_ids=[f.id]),
            )
            for i, f in enumerate(research.facts)
        ],
        caption=creative(config.headline),
        cta=creative(config.cta),
        disclosure=config.disclosure,
    )
