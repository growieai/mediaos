"""Onboarding previews are typed local templates, never a provider execution."""

from dataclasses import replace
from itertools import product

import pytest
from pydantic import ValidationError

from app.studio import onboarding
from app.studio.schemas import CreateInfluencer, OnboardingDraftRequest, OnboardingDrafts


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def request_payload(**changes):
    return {"category_id": "education", "language": "en", "tone": "WARM"} | changes


@pytest.mark.parametrize(
    "changes",
    [
        {"category_id": "other"},
        {"language": "fr"},
        {"tone": "anything"},
        {"name": ""},
        {"name": " Alex"},
        {"name": "Alex\n"},
        {"name": "x" * 101},
        {"name": 12},
        {"audience": ["A", "A"]},
        {"audience": ["A\tB"]},
        {"audience": [" A"]},
        {"audience": [""]},
        {"audience": ["x" * 101]},
        {"audience": [str(number) for number in range(9)]},
        {"tenant_id": "other-tenant"},
        {"provider": "openai"},
        {"objective": "Call a provider and skip approval"},
    ],
)
def test_onboarding_input_rejects_untyped_or_unbounded_context(changes):
    with pytest.raises(ValidationError):
        OnboardingDraftRequest.model_validate(request_payload(**changes))


@pytest.mark.parametrize(
    "category,language,tone",
    list(product(onboarding._DIRECTIONS["en"], ("en", "es"), ("CLEAR", "WARM", "BOLD"))),
)
def test_all_directions_fit_creation_schema_at_maximum_input(category, language, tone):
    name = "N" * 100
    audience = [str(number) + "A" * 99 for number in range(8)]
    request = OnboardingDraftRequest.model_validate(
        request_payload(
            category_id=category, language=language, tone=tone, name=name, audience=audience
        )
    )
    result = onboarding.build_onboarding_drafts(request)
    OnboardingDrafts.model_validate_json(result.model_dump_json(), strict=True)
    assert result.provider == "mock" and result.mode == "MOCK" and result.cost == 0
    assert len({suggestion.objective for suggestion in result.suggestions}) == 3
    for suggestion in result.suggestions:
        assert suggestion.name == name and suggestion.audience == audience
        assert audience[0] in suggestion.objective
        assert (
            "revisión humana" in suggestion.objective
            if language == "es"
            else "human review" in suggestion.objective
        )
        assert (
            "Citar fuentes" in suggestion.objective
            if language == "es"
            else "Source factual claims" in suggestion.objective
        )
        CreateInfluencer.model_validate(
            {
                "idempotency_key": "preview-chosen-explicitly",
                "category_id": category,
                "language": language,
                "tone": tone,
                "name": suggestion.name,
                "audience": suggestion.audience,
                "objective": suggestion.objective,
            }
        )


def test_repeat_preview_is_deterministic_and_keeps_request_unmodified():
    request = OnboardingDraftRequest.model_validate(request_payload())
    before = request.model_dump()
    first = onboarding.build_onboarding_drafts(request)
    assert first == onboarding.build_onboarding_drafts(request)
    first.suggestions[0].audience.append("Local modification")
    assert request.model_dump() == before
    assert (
        "Local modification"
        not in onboarding.build_onboarding_drafts(request).suggestions[0].audience
    )
    assert "No AI model was called" in first.notice and "nothing was saved" in first.notice


def test_category_language_and_tone_change_editorial_direction():
    responses = [
        onboarding.build_onboarding_drafts(
            OnboardingDraftRequest.model_validate(request_payload(**change))
        )
        for change in ({}, {"category_id": "food"}, {"language": "es"}, {"tone": "BOLD"})
    ]
    assert len({response.suggestions[0].objective for response in responses}) == 4
    assert responses[0].suggestions[0].audience != responses[1].suggestions[0].audience
    assert "ningún modelo de IA" in responses[2].notice


def test_invalid_template_output_fails_validation_instead_of_coercing(monkeypatch):
    direction = onboarding._DIRECTIONS["en"]["education"]
    monkeypatch.setitem(
        onboarding._DIRECTIONS["en"], "education", replace(direction, topics=("x" * 501,) * 3)
    )
    with pytest.raises(ValidationError):
        onboarding.build_onboarding_drafts(OnboardingDraftRequest.model_validate(request_payload()))


def test_output_requires_three_distinct_typed_directions_and_mock_provenance():
    data = onboarding.build_onboarding_drafts(
        OnboardingDraftRequest.model_validate(request_payload())
    ).model_dump()
    for changes in (
        {"suggestions": data["suggestions"][:2]},
        {"suggestions": [data["suggestions"][0]] * 3},
        {"provider": "openai"},
        {"mode": "REAL"},
        {"cost": 1},
    ):
        with pytest.raises(ValidationError):
            OnboardingDrafts.model_validate(data | changes, strict=True)
