import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.schemas import SourceInput
from app.services.workflows import ConflictError
from app.studio.schemas import SourceDraft, SourceDraftRequest, StudioInfluencer
from app.studio.source_drafts import build_source_draft


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def creator(language="en"):
    return StudioInfluencer(
        id=uuid4(),
        name="Alex",
        category_id="education",
        language=language,
        tone="Warm",
        audience=["Curious learners", "Independent teachers"],
        objective="Explain ideas with care.",
        mission_id=uuid4(),
        influencer_version_id=uuid4(),
        character_config_version_id=uuid4(),
        visual_config_version_id=None,
        opportunity_discovery=False,
        portrait_available=False,
        created_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("title", ["", " ", " Draft", "Draft ", "Draft\nidea", "x" * 201, 42])
def test_source_draft_requires_bounded_plain_title(title):
    with pytest.raises(ValidationError):
        SourceDraftRequest(influencer_id=uuid4(), title=title)


@pytest.mark.parametrize(
    "field", ["tenant_id", "provider", "raw_content", "language", "is_fixture"]
)
def test_source_draft_rejects_client_override_of_persisted_context(field):
    with pytest.raises(ValidationError):
        SourceDraftRequest.model_validate(
            {"influencer_id": uuid4(), "title": "An idea", field: "override"}
        )


@pytest.mark.parametrize("language", ["en", "es"])
def test_source_draft_uses_saved_context_with_explicit_nonfactual_provenance(language):
    item = creator(language)
    request = SourceDraftRequest(influencer_id=item.id, title="Una idea / An idea")
    result = build_source_draft(request, item)
    assert result == build_source_draft(request, item)
    assert result.influencer_id == item.id and result.mission_id == item.mission_id
    assert result.language == language and result.title == request.title
    for value in (request.title, item.name, item.objective, *item.audience):
        assert value in result.raw_content
    assert result.source_type == "GENERATED" and result.classification == "INTERNAL"
    assert result.is_fixture is True and result.provider == "mock" and result.cost == 0
    assert result.origin.endswith(hashlib.sha256(result.raw_content.encode()).hexdigest())
    assert result.metadata.source_draft_policy == "studio-source-draft-v1"
    assert (
        "NO ES EVIDENCIA VERIFICADA" in result.raw_content
        if language == "es"
        else "NOT VERIFIED SOURCE EVIDENCE" in result.raw_content
    )
    SourceDraft.model_validate_json(result.model_dump_json(), strict=True)


def test_source_draft_rejects_wrong_creator_and_unsupported_language():
    item = creator()
    with pytest.raises(ConflictError, match="identity"):
        build_source_draft(SourceDraftRequest(influencer_id=uuid4(), title="Idea"), item)
    item.language = "fr"
    with pytest.raises(ConflictError, match="English and Spanish"):
        build_source_draft(SourceDraftRequest(influencer_id=item.id, title="Idea"), item)


def source_payload(**changes):
    return {
        "source_type": "MANUAL",
        "origin": "internal:notes",
        "title": "Notes",
        "publisher": "Editor",
        "raw_content": "A planning prompt.",
        "captured_at": datetime.now(UTC),
        "classification": "INTERNAL",
        "is_fixture": True,
        "evidence": [{"start": 0, "end": 18, "statement": "A planning prompt."}],
    } | changes


@pytest.mark.parametrize(
    "marker",
    [
        {"source_type": "GENERATED"},
        {"origin": "generated:studio-source-draft-v1:any"},
        {"origin": " GENERATED:reserved "},
        {"metadata": {"source_draft_policy": "studio-source-draft-v1"}},
        {"metadata": {"source_draft_policy": "tampered"}},
    ],
)
@pytest.mark.parametrize(
    "unsafe",
    [{"is_fixture": False}, {"classification": "PRIMARY"}, {"classification": "SECONDARY"}],
)
def test_generated_markers_cannot_be_promoted_to_source_evidence(marker, unsafe):
    SourceInput.model_validate(source_payload(**marker))
    with pytest.raises(ValidationError, match="non-publishable"):
        SourceInput.model_validate(source_payload(**(marker | unsafe)))


def test_normal_manual_source_retains_existing_nonfixture_path():
    result = SourceInput.model_validate(source_payload(is_fixture=False, classification="PRIMARY"))
    assert result.source_type == "MANUAL" and not result.is_fixture


@pytest.mark.parametrize(
    "change",
    [
        {"is_fixture": False},
        {"source_type": "MANUAL"},
        {"classification": "PRIMARY"},
        {"provider": "openai"},
        {"origin": "https://example.invalid"},
        {"cost": 1},
    ],
)
def test_source_draft_response_rejects_untrusted_output(change):
    item = creator()
    result = build_source_draft(SourceDraftRequest(influencer_id=item.id, title="Idea"), item)
    with pytest.raises(ValidationError):
        SourceDraft.model_validate(result.model_dump() | change)
