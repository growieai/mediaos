"""Media contracts use HTTP fixtures only: no provider account, spend or PostgreSQL."""

import base64
import json
from copy import deepcopy
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.media_providers import ElevenLabs, HeyGen, Higgsfield, ProviderError
from app.media_providers.higgsfield import KLING, WAN
from app.media_providers.schemas import JobStatus, SpeechResult


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


SECRET = "do-not-print-this-key"


def response_transport(body=None, status=200, headers=None):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=body, headers=headers)

    return httpx.MockTransport(handle), requests


def speech_body(text="Hola"):
    return {
        "audio_base64": base64.b64encode(b"ID3synthetic-test-audio").decode(),
        "alignment": {
            "characters": list(text),
            "character_start_times_seconds": [i / 10 for i in range(len(text))],
            "character_end_times_seconds": [(i + 1) / 10 for i in range(len(text))],
        },
        "ignored_provider_metadata": "not application input",
    }


def test_speech_contract_and_secret_safe_representation():
    transport, requests = response_transport(speech_body(), headers={"request-id": "speech-123"})
    adapter = ElevenLabs(SecretStr(SECRET), transport=transport)
    result = adapter.speech("Hola", "voice-id")
    assert result.audio == b"ID3synthetic-test-audio"
    assert result.request_id == "speech-123"
    assert result.cost_usd is result.tokens is result.usage_characters is None
    assert "audio=" not in repr(result)
    assert SECRET not in repr(adapter) + repr(adapter.api_key)
    assert requests[0].url == "https://api.elevenlabs.io/v1/text-to-speech/voice-id/with-timestamps"
    assert requests[0].headers["xi-api-key"] == SECRET
    assert json.loads(requests[0].content) == {"text": "Hola", "model_id": "eleven_multilingual_v2"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b.pop("alignment"),
        lambda b: b.update(alignment=None),
        lambda b: b["alignment"].update(characters=list("Otro")),
        lambda b: b["alignment"].update(characters=["Hola"]),
        lambda b: b["alignment"].update(character_end_times_seconds=[0.1]),
        lambda b: b["alignment"].update(character_start_times_seconds=[-1, 0.1, 0.2, 0.3]),
        lambda b: b["alignment"].update(character_end_times_seconds=[0.4, 0.2, 0.3, 0.4]),
        lambda b: b["alignment"].update(character_end_times_seconds=[0.1, 0.2, 0.3, 301]),
        lambda b: b["alignment"].update(character_end_times_seconds=[0.1, 0.2, "0.3", 0.4]),
        lambda b: b.update(audio_base64="not-valid-base64"),
        lambda b: b.update(audio_base64=base64.b64encode(b"not audio").decode()),
    ],
)
def test_speech_rejects_unusable_audio_or_alignment(mutation):
    body = speech_body()
    mutation(body)
    transport, requests = response_transport(body)
    with pytest.raises(ProviderError) as error:
        ElevenLabs(SecretStr(SECRET), transport=transport).speech("Hola", "voice")
    assert error.value.category == "UNKNOWN_OUTCOME"
    assert error.value.retryable is False
    assert len(requests) == 1


@pytest.mark.parametrize(
    "voice,text,model",
    [
        ("../secrets", "Hola", "eleven_v3"),
        ("voice?key=secret", "Hola", "eleven_v3"),
        ("voice", "", "eleven_v3"),
        ("voice", "x" * 5001, "eleven_v3"),
        ("voice", "Hola", "invented-model"),
    ],
)
def test_speech_input_rejected_before_network(voice, text, model):
    transport, requests = response_transport({})
    with pytest.raises(ProviderError, match="INVALID_REQUEST"):
        ElevenLabs(SecretStr(SECRET), transport=transport).speech(text, voice, model)
    assert requests == []


def test_heygen_upload_and_create_exact_contract():
    transport, requests = response_transport(
        {
            "data": {
                "asset_id": "image-1",
                "mime_type": "image/png",
                "size_bytes": 4,
                "url": "https://files.heygen.ai/input.png",
            }
        }
    )
    result = HeyGen(SecretStr(SECRET), transport=transport).upload(
        b"test", "image/png", "upload-key"
    )
    assert result.asset_id == "image-1"
    assert requests[0].headers["Idempotency-Key"] == "upload-key"
    transport, requests = response_transport({"data": {"video_id": "video-1"}})
    job = HeyGen(SecretStr(SECRET), transport=transport).create("image-1", "audio-1")
    assert job.status == "QUEUED"
    payload = json.loads(requests[0].content)
    assert payload == {
        "type": "image",
        "image": {"type": "asset_id", "asset_id": "image-1"},
        "audio_asset_id": "audio-1",
        "resolution": "1080p",
        "aspect_ratio": "9:16",
        "engine": {"type": "avatar_iv"},
    }
    assert "Idempotency-Key" not in requests[0].headers


@pytest.mark.parametrize(
    "body",
    [
        {"asset_id": "a", "mime_type": "image/png", "size_bytes": 3},
        {"asset_id": "a", "mime_type": "image/jpeg", "size_bytes": 4},
        {"asset_id": "a", "mime_type": "image/png", "size_bytes": "4"},
    ],
)
def test_upload_rejects_mismatched_output(body):
    transport, _ = response_transport({"data": body})
    with pytest.raises(ProviderError, match="UNKNOWN_OUTCOME"):
        HeyGen(SecretStr(SECRET), transport=transport).upload(b"test", "image/png", "key")


@pytest.mark.parametrize(
    "state,expected",
    [
        ("pending", "QUEUED"),
        ("processing", "RUNNING"),
        ("completed", "COMPLETED"),
        ("failed", "FAILED"),
    ],
)
def test_heygen_poll_normalizes_states(state, expected):
    body = {"id": "job-1", "status": state, "failure_message": SECRET}
    if state == "completed":
        body.update(video_url="https://files.heygen.ai/output.mp4", duration=10.5)
    transport, _ = response_transport({"data": body})
    result = HeyGen(SecretStr(SECRET), transport=transport).poll("job-1")
    assert result.status == expected
    assert SECRET not in str(result)


@pytest.mark.parametrize(
    "url",
    [
        "http://files.heygen.ai/a",
        "https://u:p@files.heygen.ai/a",
        "https://127.0.0.1/a",
        "https://localhost/a",
        "https://files.heygen.ai:80/a",
        "https://files.heygen.ai/a\n",
        "https://files.heygen.ai/a#x",
    ],
)
def test_output_url_rejected(url):
    transport, _ = response_transport(
        {"data": {"id": "job-1", "status": "completed", "video_url": url}}
    )
    with pytest.raises(ProviderError, match="INVALID_OUTPUT"):
        HeyGen(SecretStr(SECRET), transport=transport).poll("job-1")


@pytest.mark.parametrize(
    "body",
    [
        {"id": "other", "status": "pending"},
        {"id": "job-1", "status": "mystery"},
        {"id": "job-1", "status": "completed"},
        {
            "id": "job-1",
            "status": "completed",
            "video_url": "https://files.heygen.ai/a",
            "duration": 301,
        },
        {"id": "job-1", "status": "pending", "video_url": "https://files.heygen.ai/a"},
    ],
)
def test_status_fail_closed(body):
    transport, _ = response_transport({"data": body})
    with pytest.raises(ProviderError, match="INVALID_OUTPUT"):
        HeyGen(SecretStr(SECRET), transport=transport).poll("job-1")


def test_higgsfield_estimate_submit_and_poll():
    parameters = {
        "image_url": "https://images.example.com/portrait.png",
        "audio_url": "https://audio.example.com/speech.wav",
        "duration": 10,
    }
    transport, requests = response_transport({"credits": "3.2", "usd": "0.75"})
    estimate = Higgsfield(SecretStr("id"), SecretStr(SECRET), transport=transport).estimate(
        WAN, parameters
    )
    assert estimate.usd == Decimal("0.75")
    assert requests[0].url.path == f"/estimate/{WAN}"
    transport, requests = response_transport(
        {
            "request_id": "job-1",
            "status": "queued",
            "status_url": "https://malicious.invalid/ignored",
        }
    )
    result = Higgsfield(SecretStr("id"), SecretStr(SECRET), transport=transport).submit(
        WAN, parameters
    )
    assert result.request_id == "job-1"
    assert json.loads(requests[0].content)["audio_url"] == parameters["audio_url"]
    transport, requests = response_transport(
        {
            "request_id": "job-1",
            "status": "completed",
            "video": {"url": "https://cdn.example.com/final.mp4"},
        }
    )
    result = Higgsfield(SecretStr("id"), SecretStr(SECRET), transport=transport).poll("job-1")
    assert result.status == "COMPLETED"
    assert requests[0].url == "https://api.higgsfield.ai/requests/job-1/status"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("queued", "QUEUED"),
        ("in_progress", "RUNNING"),
        ("failed", "FAILED"),
        ("nsfw", "BLOCKED"),
        ("canceled", "CANCELED"),
    ],
)
def test_higgsfield_terminal_states(state, expected):
    transport, _ = response_transport({"request_id": "job", "status": state, "error": SECRET})
    result = Higgsfield(SecretStr("id"), SecretStr(SECRET), transport=transport).poll("job")
    assert result.status == expected
    assert SECRET not in str(result)


@pytest.mark.parametrize(
    "model,parameters",
    [
        ("unknown", {}),
        (WAN, {"image_url": "http://bad.example.com/image"}),
        (WAN, {"image_url": "https://cdn.example.com/a", "duration": 16}),
        (WAN, {"image_url": "https://cdn.example.com/a", "unsupported": True}),
        (
            KLING,
            {"image_url": "https://cdn.example.com/a", "audio_url": "https://cdn.example.com/b"},
        ),
    ],
)
def test_higgsfield_rejects_wrong_model_input(model, parameters):
    transport, requests = response_transport({})
    with pytest.raises(ProviderError, match="INVALID_REQUEST"):
        Higgsfield(SecretStr("id"), SecretStr(SECRET), transport=transport).submit(
            model, parameters
        )
    assert not requests


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "oops"])
def test_bad_estimate_rejected(value):
    transport, _ = response_transport({"credits": "1", "usd": value})
    with pytest.raises(ProviderError, match="INVALID_OUTPUT"):
        Higgsfield(SecretStr("id"), SecretStr(SECRET), transport=transport).estimate(
            KLING, {"image_url": "https://cdn.example.com/a"}
        )


@pytest.mark.parametrize(
    "status,category,retry",
    [
        (302, "PROVIDER_UNAVAILABLE", False),
        (400, "INVALID_REQUEST", False),
        (401, "AUTHENTICATION", False),
        (402, "PAYMENT_REQUIRED", False),
        (429, "RATE_LIMITED", True),
        (500, "UNKNOWN_OUTCOME", False),
        (503, "UNKNOWN_OUTCOME", False),
    ],
)
def test_generation_never_retries_internally(status, category, retry):
    transport, requests = response_transport(
        {"detail": SECRET},
        status,
        {"Location": "https://attacker.invalid", "x-correlation-id": "corr-1"},
    )
    with pytest.raises(ProviderError) as error:
        HeyGen(SecretStr(SECRET), transport=transport).create("image", "audio")
    assert (error.value.category, error.value.retryable) == (category, retry)
    assert error.value.request_id == "corr-1"
    assert SECRET not in repr(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize("poll", [False, True])
def test_network_ambiguity_is_not_replayed(poll):
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout(SECRET, request=request)

    adapter = HeyGen(SecretStr(SECRET), transport=httpx.MockTransport(handle))
    with pytest.raises(ProviderError) as error:
        adapter.poll("video") if poll else adapter.create("image", "audio")
    assert error.value.category == ("NETWORK" if poll else "UNKNOWN_OUTCOME")
    assert error.value.retryable == poll
    assert SECRET not in str(error.value)
    assert len(calls) == 1


def test_poll_500_is_retryable_but_adapter_does_not_loop():
    transport, requests = response_transport({}, 500)
    with pytest.raises(ProviderError) as error:
        HeyGen(SecretStr(SECRET), transport=transport).poll("id")
    assert error.value.category == "PROVIDER_UNAVAILABLE"
    assert error.value.retryable
    assert len(requests) == 1


def test_retry_after_metadata_and_secret_echo_are_sanitized():
    transport, requests = response_transport({}, 429, {"Retry-After": "20", "request-id": SECRET})
    with pytest.raises(ProviderError) as error:
        HeyGen(SecretStr(SECRET), transport=transport).create("image", "audio")
    assert error.value.retry_after_seconds == 20
    assert error.value.request_id is None
    assert len(requests) == 1


@pytest.mark.parametrize(
    "data,mime,key",
    [
        (b"", "image/png", "key"),
        (b"x", "text/html", "key"),
        (b"x", "image/png", "secret\nheader"),
        (b"x" * 32_000_001, "image/png", "key"),
    ],
    ids=["empty", "wrong-mime", "bad-key", "oversized"],
)
def test_upload_input_limits(data, mime, key):
    transport, requests = response_transport({})
    with pytest.raises(ProviderError, match="INVALID_REQUEST"):
        HeyGen(SecretStr(SECRET), transport=transport).upload(data, mime, key)
    assert not requests


@pytest.mark.parametrize(
    "content,category",
    [
        (b"not-json", "INVALID_OUTPUT"),
        (b"[]", "INVALID_OUTPUT"),
        (b"x" * 1_000_001, "RESPONSE_TOO_LARGE"),
    ],
    ids=["not-json", "wrong-envelope", "oversized"],
)
@pytest.mark.parametrize("poll", [False, True])
def test_bounded_invalid_responses(content, category, poll):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=content))
    adapter = HeyGen(SecretStr(SECRET), transport=transport)
    with pytest.raises(ProviderError, match=category if poll else "UNKNOWN_OUTCOME") as error:
        adapter.poll("job") if poll else adapter.create("image", "audio")
    assert not error.value.retryable


@pytest.mark.parametrize("provider", ["elevenlabs", "heygen", "higgsfield"])
@pytest.mark.parametrize(
    "content", [b'{"incomplete":', b"null", b"{}", b'{"data":{"video_id":17}}']
)
@pytest.mark.parametrize("status", [200, 201, 202, 204])
def test_accepted_generation_without_valid_result_is_held(provider, content, status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, content=content, headers={"request-id": "accepted-1"})

    transport = httpx.MockTransport(handle)
    with pytest.raises(ProviderError, match="UNKNOWN_OUTCOME") as error:
        if provider == "elevenlabs":
            ElevenLabs(SecretStr(SECRET), transport=transport).speech("Hola", "voice")
        elif provider == "heygen":
            HeyGen(SecretStr(SECRET), transport=transport).create("image", "audio")
        else:
            Higgsfield(SecretStr("id"), SecretStr(SECRET), transport=transport).submit(
                WAN, {"image_url": "https://images.example.com/portrait.png"}
            )
    assert not error.value.retryable
    assert error.value.request_id == "accepted-1"
    assert len(requests) == 1
    assert SECRET not in str(error.value)


def test_accepted_generation_stream_interruption_is_held():
    class Truncated(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"data":'
            raise httpx.ReadError(SECRET)

    transport = httpx.MockTransport(lambda r: httpx.Response(202, stream=Truncated()))
    with pytest.raises(ProviderError, match="UNKNOWN_OUTCOME") as error:
        HeyGen(SecretStr(SECRET), transport=transport).create("image", "audio")
    assert not error.value.retryable
    assert SECRET not in str(error.value)


def test_invalid_estimate_json_does_not_become_unknown_generation():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=b'{"partial":'))
    with pytest.raises(ProviderError, match="INVALID_OUTPUT"):
        Higgsfield(SecretStr("id"), SecretStr(SECRET), transport=transport).estimate(
            WAN, {"image_url": "https://images.example.com/portrait.png"}
        )


def test_normalized_internal_results_forbid_extra_authority():
    with pytest.raises(ValidationError):
        JobStatus(request_id="job", status="QUEUED", approved=True)
    body = deepcopy(speech_body()["alignment"])
    with pytest.raises(ValidationError):
        SpeechResult(audio=b"audio", alignment=body, cost_usd="0")
