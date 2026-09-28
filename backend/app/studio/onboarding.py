"""Bounded, deterministic onboarding previews. No model, network, or artifact writes."""

from dataclasses import dataclass

from app.config import get_settings
from app.db.repository import Repository
from app.services.workflows import ConflictError
from app.studio.schemas import OnboardingDraftRequest, OnboardingDrafts, OnboardingSuggestion
from app.studio.service import categories


@dataclass(frozen=True)
class CategoryDirection:
    names: tuple[str, str, str]
    audience: tuple[str, str]
    topics: tuple[str, str, str]


# These are editorial intentions, not factual claims or publishable content.
_DIRECTIONS: dict[str, dict[str, CategoryDirection]] = {
    "en": {
        "business": CategoryDirection(
            ("Alex", "Morgan", "Jamie"),
            ("Small business owners", "Independent professionals"),
            (
                "practical business decisions through useful checklists",
                "business concepts through plain-language explainers",
                "everyday business questions through source-backed conversations",
            ),
        ),
        "beauty": CategoryDirection(
            ("Mia", "Riley", "Avery"),
            ("Beauty enthusiasts", "Salon professionals"),
            (
                "beauty routines and product research through thoughtful checklists",
                "beauty terminology and ingredient labels through careful explainers",
                "beauty questions through inclusive, evidence-led conversations",
            ),
        ),
        "food": CategoryDirection(
            ("Noah", "Sam", "Robin"),
            ("Home cooks", "Curious food lovers"),
            (
                "everyday cooking through practical preparation guides",
                "ingredients and cooking techniques through clear explainers",
                "food traditions and kitchen questions through thoughtful conversations",
            ),
        ),
        "fitness": CategoryDirection(
            ("Jordan", "Casey", "Taylor"),
            ("Fitness beginners", "Everyday movement enthusiasts"),
            (
                "general movement habits through approachable planning prompts",
                "fitness terminology through evidence-led explainers",
                "everyday movement questions through inclusive conversations",
            ),
        ),
        "technology": CategoryDirection(
            ("Alex", "Quinn", "Drew"),
            ("Curious technology users", "Independent creators"),
            (
                "everyday digital tools through practical checklists",
                "technology concepts through approachable explainers",
                "responsible technology choices through thoughtful conversations",
            ),
        ),
        "travel": CategoryDirection(
            ("River", "Ellis", "Rory"),
            ("Independent travellers", "Curious local explorers"),
            (
                "travel preparation through useful planning checklists",
                "destination research through source-backed explainers",
                "respectful local exploration through thoughtful conversations",
            ),
        ),
        "education": CategoryDirection(
            ("Noa", "Alex", "Sage"),
            ("Curious learners", "Independent educators"),
            (
                "everyday learning through practical study prompts",
                "complex ideas through clear, source-backed explainers",
                "learning questions through thoughtful conversations",
            ),
        ),
        "lifestyle": CategoryDirection(
            ("Avery", "Rowan", "Sky"),
            ("Curious everyday readers", "People exploring intentional routines"),
            (
                "everyday routines through thoughtful planning prompts",
                "lifestyle choices through source-backed explainers",
                "daily-life questions through inclusive conversations",
            ),
        ),
    },
    "es": {
        "business": CategoryDirection(
            ("Álex", "Cris", "Dani"),
            ("Personas con pequeños negocios", "Profesionales independientes"),
            (
                "decisiones de negocio con listas prácticas",
                "conceptos empresariales con explicaciones claras",
                "preguntas del día a día de un negocio con conversaciones documentadas",
            ),
        ),
        "beauty": CategoryDirection(
            ("Luz", "Alba", "Vega"),
            ("Personas interesadas en belleza", "Profesionales de salón"),
            (
                "rutinas de belleza e investigación de productos con guías prácticas",
                "términos de belleza y etiquetas de ingredientes con explicaciones cuidadosas",
                "preguntas de belleza con conversaciones inclusivas y documentadas",
            ),
        ),
        "food": CategoryDirection(
            ("Leo", "Nico", "Sol"),
            ("Personas que cocinan en casa", "Amantes de la gastronomía"),
            (
                "la cocina cotidiana con guías prácticas de preparación",
                "ingredientes y técnicas de cocina con explicaciones claras",
                "tradiciones culinarias y dudas de cocina con conversaciones documentadas",
            ),
        ),
        "fitness": CategoryDirection(
            ("Dani", "Álex", "Cris"),
            ("Personas que empiezan a entrenar", "Personas interesadas en moverse más"),
            (
                "hábitos generales de movimiento con propuestas accesibles",
                "términos de actividad física con explicaciones documentadas",
                "preguntas sobre movimiento cotidiano con conversaciones inclusivas",
            ),
        ),
        "technology": CategoryDirection(
            ("Álex", "Noa", "Ariel"),
            ("Personas curiosas por la tecnología", "Creadores independientes"),
            (
                "herramientas digitales cotidianas con listas prácticas",
                "conceptos tecnológicos con explicaciones accesibles",
                "decisiones tecnológicas responsables con conversaciones documentadas",
            ),
        ),
        "travel": CategoryDirection(
            ("Vega", "Mar", "Leo"),
            ("Viajeros independientes", "Personas que exploran su entorno"),
            (
                "la preparación de viajes con listas útiles",
                "la investigación de destinos con explicaciones documentadas",
                "la exploración local respetuosa con conversaciones cuidadosas",
            ),
        ),
        "education": CategoryDirection(
            ("Noa", "Luz", "Álex"),
            ("Personas con ganas de aprender", "Educadores independientes"),
            (
                "el aprendizaje cotidiano con propuestas de estudio prácticas",
                "ideas complejas con explicaciones claras y documentadas",
                "preguntas de aprendizaje con conversaciones cuidadosas",
            ),
        ),
        "lifestyle": CategoryDirection(
            ("Alba", "Sol", "Mar"),
            ("Personas curiosas por la vida cotidiana", "Personas que exploran nuevas rutinas"),
            (
                "rutinas cotidianas con propuestas de planificación",
                "decisiones del día a día con explicaciones documentadas",
                "preguntas sobre la vida cotidiana con conversaciones inclusivas",
            ),
        ),
    },
}


def build_onboarding_drafts(request: OnboardingDraftRequest) -> OnboardingDrafts:
    """Pure preview: repeat input gives repeat output and never creates an influencer."""
    direction = _DIRECTIONS[request.language][request.category_id]
    audience = request.audience or list(direction.audience)
    # Keep all audience labels in the editable suggestion, but bound the objective
    # by referring only to the first. Never truncate user-provided text mid-label.
    reader = audience[0]
    spanish = request.language == "es"
    labels = (
        ("Guía práctica", "Ideas claras", "Conversaciones útiles")
        if spanish
        else ("Practical guide", "Clear explanations", "Useful conversations")
    )
    tone = (
        {"CLEAR": "claro y directo", "WARM": "cercano y alentador", "BOLD": "enérgico y reflexivo"}
        if spanish
        else {
            "CLEAR": "clear and direct",
            "WARM": "warm and encouraging",
            "BOLD": "bold and thoughtful",
        }
    )[request.tone]
    suggestions = []
    for index, identifier in enumerate(("practical", "explainer", "community")):
        objective = (
            f"Ayudar a {reader} a explorar {direction.topics[index]} con un tono {tone}. "
            "Citar fuentes en las afirmaciones, señalar la incertidumbre y exigir revisión humana antes de publicar."
            if spanish
            else f"Help {reader} explore {direction.topics[index]} in a {tone} voice. "
            "Source factual claims, acknowledge uncertainty, and require human review before publication."
        )
        suggestions.append(
            OnboardingSuggestion.model_validate(
                {
                    "id": identifier,
                    "label": labels[index],
                    "name": request.name or direction.names[index],
                    "audience": audience.copy(),
                    "objective": objective,
                },
                strict=True,
            )
        )
    return OnboardingDrafts(
        notice=(
            "Modo de prueba: borradores de ejemplo basados en plantillas. No se llamó a ningún modelo de IA ni se guardó nada. Revisa y edita antes de crear."
            if spanish
            else "Mock mode: template-based starter drafts. No AI model was called and nothing was saved. Review and edit before creating."
        ),
        suggestions=suggestions,
    )


def onboarding_drafts(repo: Repository, request: OnboardingDraftRequest) -> OnboardingDrafts:
    repo.require("OPERATOR")
    settings = get_settings()
    if not settings.enable_external_creators:
        raise ConflictError("Creator studio creation is disabled by the server")
    if not settings.ai_mock_mode:
        raise ConflictError("Onboarding suggestions currently support mock mode only")
    if request.category_id not in {item.id for item in categories(repo).categories}:
        raise ConflictError("The selected content category is not enabled")
    return build_onboarding_drafts(request)
