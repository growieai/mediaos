"""Pure adapter tests use recorded-shaped responses, never credentials or live APIs."""

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.ai.structured import (
    ADAPTER_VERSION,
    ENDPOINT,
    CreatorPlan,
    ModelSelectionError,
    OpenAISelectionAdapter,
    SelectionSettings,
    assemble_carousel,
    template_id,
)
from app.models.schemas import CharacterConfig, ContentBrief, Fact, ResearchPack


@pytest.fixture
def inputs():
    facts = [
        Fact(
            id=uuid4(),
            source_snapshot_id=uuid4(),
            start=0,
            end=len(statement),
            statement=statement,
            verification_status="VERIFIED",
            confidence=1.0,
            fact_type="GENERAL",
        )
        for statement in ["Texto oficial: información pública.", "Programa para pequeñas empresas."]
    ]
    config = CharacterConfig(
        persona="A practical virtual business creator",
        voice="Clear and concise",
        visual_policy="Readable type",
        brand_policy="Value first",
        language="es-ES",
        audience=["Small businesses"],
        franchise="Verified information",
        objective="Help readers understand official information",
        tone="Practical",
        disclosure="Creadora virtual generada con IA.",
        headline="Para revisar",
        cta="Consulta la fuente oficial.",
        cta_type="READ_SOURCE",
        brand_association_level=0,
        max_brand_association_level=1,
        min_slides=1,
        max_slides=10,
        creative_allowlist=["Para revisar", "Consulta la fuente oficial.", "Lo esencial"],
    )
    research = ResearchPack(facts=facts, verification_status="VERIFIED")
    brief = ContentBrief(
        allowed_fact_ids=[f.id for f in facts],
        audience=config.audience,
        franchise=config.franchise,
        objective=config.objective,
        tone=config.tone,
        cta_type=config.cta_type,
        brand_association_level=0,
        constraints={"min_slides": 1, "max_slides": 10},
    )
    return research, brief, config, uuid4()


def plan_payload(inputs):
    research, _, config, _ = inputs
    return {
        "schema_version": 1,
        "slides": [
            {"fact_id": str(f.id), "headline_template_id": template_id(config.headline)}
            for f in reversed(research.facts)
        ],
        "caption_template_id": template_id("Lo esencial"),
        "cta_template_id": template_id(config.cta),
    }


def envelope(plan):
    return {
        "id": "resp_test_01",
        "model": "configured-model-snapshot",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(plan)}],
            },
        ],
        "usage": {
            "input_tokens": 123,
            "output_tokens": 45,
            "total_tokens": 168,
            "input_tokens_details": {"cached_tokens": 12},
            "output_tokens_details": {"reasoning_tokens": 5},
        },
    }


def adapter_for(body=None, *, status=200, handler=None, settings=None, headers=None):
    def send(request):
        return httpx.Response(status, json=body, headers=headers or {"x-request-id": "req_test_01"})

    return OpenAISelectionAdapter(
        settings
        or SelectionSettings(
            api_key=SecretStr("unit-test-not-a-real-key"), model="configured-model"
        ),
        transport=httpx.MockTransport(handler or send),
    )


def test_selection_requests_strict_schema_and_preserves_exact_claims(inputs):
    seen = []

    def send(request):
        seen.append(request)
        return httpx.Response(
            200, json=envelope(plan_payload(inputs)), headers={"x-request-id": "req_test"}
        )

    result = adapter_for(handler=send).select(*inputs)
    request = seen[0]
    assert str(request.url) == ENDPOINT
    assert request.method == "POST"
    payload = json.loads(request.content)
    assert payload["model"] == "configured-model"
    assert payload["store"] is False
    assert "tools" not in payload
    schema_format = payload["text"]["format"]
    assert schema_format["type"] == "json_schema"
    assert schema_format["strict"] is True
    assert schema_format["schema"]["additionalProperties"] is False
    assert set(schema_format["schema"]["required"]) == set(schema_format["schema"]["properties"])
    facts_schema = schema_format["schema"]["$defs"]["SlideSelection"]["properties"]["fact_id"]
    assert set(facts_schema["enum"]) == {str(f.id) for f in inputs[0].facts}
    assert [s.body.text for s in result.draft.slides] == [
        f.statement for f in reversed(inputs[0].facts)
    ]
    assert [s.body.fact_ids for s in result.draft.slides] == [
        [f.id] for f in reversed(inputs[0].facts)
    ]
    assert result.draft.disclosure == inputs[2].disclosure
    assert result.draft.language == inputs[2].language
    assert result.draft.influencer_version_id == inputs[3]
    assert result.draft.cta.text == inputs[2].cta
    assert result.execution.provider == "openai"
    assert result.execution.model == "configured-model"
    assert result.execution.response_model == "configured-model-snapshot"
    assert result.execution.adapter == ADAPTER_VERSION
    assert result.execution.request_id == "req_test"
    assert result.execution.input_tokens == 123
    assert result.execution.output_tokens == 45
    assert result.execution.token_details.cached_input_tokens == 12
    assert result.execution.cost is None
    assert not result.execution.is_mock


@pytest.mark.parametrize(
    "mutation", ["fabricated_fact", "duplicate_fact", "foreign_template", "wrong_cta"]
)
def test_invalid_selection_never_produces_a_draft(inputs, mutation):
    plan = plan_payload(inputs)
    if mutation == "fabricated_fact":
        plan["slides"][0]["fact_id"] = str(uuid4())
    elif mutation == "duplicate_fact":
        plan["slides"][1]["fact_id"] = plan["slides"][0]["fact_id"]
    elif mutation == "foreign_template":
        plan["caption_template_id"] = template_id("An invented financial claim")
    elif mutation == "wrong_cta":
        plan["cta_template_id"] = template_id(inputs[2].headline)
    with pytest.raises(ModelSelectionError) as caught:
        adapter_for(envelope(plan)).select(*inputs)
    assert caught.value.category == "INVALID_SELECTION"
    assert not caught.value.retryable
    assert caught.value.execution.output_tokens == 45


@pytest.mark.parametrize(
    "mutation", ["extra_claim", "wrong_type", "missing_field", "string_version"]
)
def test_malformed_model_output_is_rejected_strictly(inputs, mutation):
    plan = plan_payload(inputs)
    if mutation == "extra_claim":
        plan["slides"][0]["body"] = "An invented financial claim"
    elif mutation == "wrong_type":
        plan["slides"][0]["fact_id"] = 1
    elif mutation == "missing_field":
        del plan["caption_template_id"]
    else:
        plan["schema_version"] = "1"
    with pytest.raises(ModelSelectionError, match="INVALID_OUTPUT"):
        adapter_for(envelope(plan)).select(*inputs)


@pytest.mark.parametrize(
    "mutation",
    ["unverified", "outside_brief", "duplicated_fact", "missing_disclosure", "cta", "range"],
)
def test_invalid_upstream_input_makes_no_network_request(inputs, mutation):
    research, brief, config, _ = inputs
    if mutation == "unverified":
        research.facts[0].verification_status = "UNVERIFIED"
    elif mutation == "outside_brief":
        brief.allowed_fact_ids.append(uuid4())
    elif mutation == "duplicated_fact":
        research.facts.append(research.facts[0])
    elif mutation == "missing_disclosure":
        config.disclosure = " "
    elif mutation == "cta":
        config.cta = "Unsupported CTA"
    else:
        brief.constraints["min_slides"] = 3

    def unexpected(request):
        pytest.fail("Invalid input must fail before network access")

    with pytest.raises(ModelSelectionError, match="INVALID_INPUT"):
        adapter_for(handler=unexpected).select(*inputs)


def test_model_cannot_select_research_fact_excluded_by_brief(inputs):
    inputs[1].allowed_fact_ids = [inputs[0].facts[0].id]
    with pytest.raises(ModelSelectionError, match="INVALID_SELECTION"):
        adapter_for(envelope(plan_payload(inputs))).select(*inputs)


@pytest.mark.parametrize("kind", ["refusal", "incomplete", "multiple_texts", "tool_call"])
def test_response_control_states_fail_closed(inputs, kind):
    body = envelope(plan_payload(inputs))
    if kind == "refusal":
        body["output"][1]["content"] = [{"type": "refusal", "refusal": "unsafe provider text"}]
    elif kind == "incomplete":
        body["status"] = "incomplete"
    elif kind == "multiple_texts":
        body["output"][1]["content"].append(body["output"][1]["content"][0])
    else:
        body["output"].append({"type": "function_call", "arguments": "{}"})
    with pytest.raises(ModelSelectionError) as caught:
        adapter_for(body).select(*inputs)
    assert caught.value.category == {"refusal": "REFUSAL", "incomplete": "INCOMPLETE"}.get(
        kind, "INVALID_RESPONSE"
    )
    assert caught.value.execution.input_tokens == 123
    assert not caught.value.retryable
    assert "unsafe provider text" not in str(caught.value)


@pytest.mark.parametrize(
    "status,retryable",
    [
        (429, True),
        (500, True),
        (503, True),
        (408, True),
        (400, False),
        (401, False),
        (403, False),
        (302, False),
    ],
)
def test_http_error_classification_has_no_automatic_retries(inputs, status, retryable):
    requests = []

    def send(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"error": "unit-test-not-a-real-key raw prompt"},
            headers={"retry-after": "60"},
        )

    with pytest.raises(ModelSelectionError) as caught:
        adapter_for(handler=send).select(*inputs)
    assert caught.value.retryable is retryable
    assert caught.value.retry_after_seconds == 60
    assert len(requests) == 1
    assert "unit-test" not in str(caught.value)
    assert "raw prompt" not in str(caught.value)
    assert caught.value.execution.input_tokens is None


def test_network_error_redacts_exception_and_preserves_retryability(inputs):
    def fail(request):
        raise httpx.ReadTimeout("secret unit-test-not-a-real-key", request=request)

    with pytest.raises(ModelSelectionError, match="NETWORK") as caught:
        adapter_for(handler=fail).select(*inputs)
    assert caught.value.retryable
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_missing_usage_is_unknown_not_zero(inputs):
    body = envelope(plan_payload(inputs))
    del body["usage"]
    result = adapter_for(body).select(*inputs)
    assert result.execution.input_tokens is None
    assert result.execution.output_tokens is None
    assert result.execution.token_details is None
    assert result.execution.cost is None


def test_invalid_usage_values_and_untrusted_ids_are_not_logged(inputs):
    body = envelope(plan_payload(inputs))
    body["model"] = "raw provider secret with spaces"
    body["id"] = "bad\nresponse"
    body["usage"] = {"input_tokens": True, "output_tokens": "45", "total_tokens": -1}
    result = adapter_for(body, headers={"x-request-id": "untrusted header value"}).select(*inputs)
    assert result.execution.input_tokens is None
    assert result.execution.output_tokens is None
    assert result.execution.total_tokens is None
    assert result.execution.response_model is None
    assert result.execution.response_id is None
    assert result.execution.request_id is None


def test_input_and_response_sizes_are_bounded(inputs):
    settings = SelectionSettings(
        api_key=SecretStr("unit-test-key"), model="configured-model", max_input_bytes=1024
    )
    with pytest.raises(ModelSelectionError, match="INPUT_TOO_LARGE"):
        adapter_for(settings=settings).select(*inputs)
    settings = SelectionSettings(
        api_key=SecretStr("unit-test-key"), model="configured-model", max_response_bytes=1024
    )
    body = envelope(plan_payload(inputs))
    body["irrelevant_padding"] = "x" * 2000
    with pytest.raises(ModelSelectionError, match="RESPONSE_TOO_LARGE"):
        adapter_for(body, settings=settings).select(*inputs)


@pytest.mark.parametrize("raw", [b"not JSON", b"[]", b'{"status": "completed", "output": null}'])
def test_invalid_provider_response_does_not_leak_body(inputs, raw):
    with pytest.raises(ModelSelectionError, match="INVALID_RESPONSE"):
        adapter_for(handler=lambda request: httpx.Response(200, content=raw)).select(*inputs)


def test_deterministic_assembly_and_template_identity(inputs):
    plan = CreatorPlan.model_validate_json(json.dumps(plan_payload(inputs)))
    first = assemble_carousel(plan, *inputs)
    second = assemble_carousel(plan, *inputs)
    assert first.model_dump_json() == second.model_dump_json()
    assert template_id("información") != template_id("informacion")
    assert template_id("text ") != template_id("text")


def test_secrets_are_not_in_settings_repr():
    settings = SelectionSettings(
        api_key=SecretStr("unit-test-not-a-real-key"), model="configured-model"
    )
    assert "unit-test-not-a-real-key" not in repr(settings)
    assert "unit-test-not-a-real-key" not in settings.model_dump_json()
