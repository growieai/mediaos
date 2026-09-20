from hashlib import sha256

from app.models.schemas import ContentBrief, ResearchPack


def build_brief_mock(research: ResearchPack, influencer_id: str = "sofia_es") -> ContentBrief:
    digest = sha256(research.research_pack_id.encode()).hexdigest()[:10]
    return ContentBrief(
        brief_id=f"cb_{digest}",
        research_pack_id=research.research_pack_id,
        influencer_id=influencer_id,
        franchise="DINERO GRATIS",
        audience=research.audience,
        objective="Explain one verified business opportunity clearly enough to earn saves and shares.",
        hook="If you run a small business in Spain, do not ignore this update.",
        angle="Useful, practical, anti-bureaucratic, no hype.",
        growie_association_level=0,
        format="carousel",
        cta="Save this and check the official source before applying.",
    )
