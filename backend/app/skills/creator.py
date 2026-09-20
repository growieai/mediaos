from hashlib import sha256

from app.models.schemas import CarouselDraft, CarouselSlide, ContentBrief, ResearchPack


def create_carousel_mock(brief: ContentBrief, research: ResearchPack) -> CarouselDraft:
    digest = sha256(brief.brief_id.encode()).hexdigest()[:10]
    source_fact = research.verified_facts[0]
    return CarouselDraft(
        content_id=f"content_{digest}",
        brief_id=brief.brief_id,
        caption=(
            "Nueva alerta para pequeños negocios en España. "
            "He resumido lo importante sin convertirlo en jerga administrativa. "
            "Verifica siempre los requisitos finales en la fuente oficial."
        ),
        slides=[
            CarouselSlide(index=1, headline=brief.hook, body="Sofía found something worth checking."),
            CarouselSlide(index=2, headline="Qué cambió", body=source_fact.claim),
            CarouselSlide(index=3, headline="Por qué importa", body=research.implications[0]),
            CarouselSlide(index=4, headline="Antes de actuar", body="Confirma elegibilidad, fechas y documentación en la fuente oficial."),
            CarouselSlide(index=5, headline="Hazlo útil", body=brief.cta),
        ],
        disclosure="Sofía es una creadora virtual generada con IA.",
    )
