"""A no-network writing starter; generated text is never factual source evidence."""

import hashlib

from app.config import get_settings
from app.db.repository import Repository
from app.services.workflows import ConflictError
from app.studio.schemas import SourceDraft, SourceDraftRequest, StudioInfluencer
from app.studio.service import influencer


def build_source_draft(request: SourceDraftRequest, creator: StudioInfluencer) -> SourceDraft:
    if request.influencer_id != creator.id:
        raise ConflictError("Source draft creator does not match the selected identity")
    if creator.language not in ("en", "es"):
        raise ConflictError("Source draft previews currently support English and Spanish only")
    audience = "; ".join(creator.audience)
    if creator.language == "es":
        raw = (
            "BORRADOR CREATIVO — NO ES EVIDENCIA VERIFICADA\n\n"
            f"Título de trabajo: {request.title}\n"
            f"Creador: {creator.name}. Audiencia prevista: {audience}.\n"
            f"Misión editorial (contexto de planificación): {creator.objective}\n\n"
            f"Apertura propuesta: Exploremos «{request.title}» paso a paso. "
            "¿Qué pregunta te gustaría resolver antes de tomar una decisión?\n\n"
            "Desarrollo propuesto: Elige una pregunta concreta de tu audiencia. "
            "Organiza la historia en una idea, un ejemplo pendiente de comprobar y una acción para reflexionar. "
            "Localiza una fuente original para cada afirmación; comprueba su contexto y fecha. "
            "Si falta evidencia, deja la pregunta abierta en lugar de inventar una respuesta.\n\n"
            "Cierre propuesto: ¿Qué te gustaría que investigáramos después? "
            "Conserva este texto como una propuesta de trabajo hasta disponer de evidencia real.\n\n"
            "Este borrador se basa en plantillas locales. No se consultó ninguna fuente ni se llamó a un modelo de IA. "
            "Guardar este texto crea material de prueba no publicable. Para publicar, inicia una historia nueva con una fuente real y revisión humana."
        )
        notice = (
            "Modo de prueba: propuesta creativa basada en plantillas, no evidencia factual. "
            "No se llamó a ningún modelo ni se guardó nada. Si usas este texto, quedará marcado como material de prueba no publicable."
        )
    else:
        raw = (
            "CREATIVE DRAFT — NOT VERIFIED SOURCE EVIDENCE\n\n"
            f"Working title: {request.title}\n"
            f"Creator: {creator.name}. Intended audience: {audience}.\n"
            f"Editorial mission (planning context): {creator.objective}\n\n"
            f"Opening draft: Let’s explore “{request.title}” one step at a time. "
            "What question would you want answered before making a decision?\n\n"
            "Story direction: Choose one specific question from your audience. "
            "Structure the story around an idea, an example still to be checked, and a reflection prompt. "
            "Find an original source for each factual claim and check its context and date. "
            "Where evidence is missing, leave the question open instead of inventing an answer.\n\n"
            "Closing draft: What would you like us to investigate next? "
            "Keep this as a working proposal until real evidence is available.\n\n"
            "This draft uses local templates. No source was researched and no AI model was called. "
            "Saving this text creates non-publishable test material. To publish, start a new story with a real source and human review."
        )
        notice = (
            "Mock mode: a template-based creative starter, not factual evidence. "
            "No model was called and nothing was saved. Using this text marks the story as non-publishable test material."
        )
    return SourceDraft.model_validate(
        {
            "notice": notice,
            "influencer_id": creator.id,
            "mission_id": creator.mission_id,
            "language": creator.language,
            "title": request.title,
            "raw_content": raw,
            "origin": "generated:studio-source-draft-v1:"
            + hashlib.sha256(raw.encode()).hexdigest(),
        },
        strict=True,
    )


def source_draft(repo: Repository, request: SourceDraftRequest) -> SourceDraft:
    repo.require("OPERATOR")
    if not get_settings().ai_mock_mode:
        raise ConflictError("Source draft suggestions currently support mock mode only")
    return build_source_draft(request, influencer(repo, request.influencer_id))
