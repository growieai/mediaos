"""Provider deadlines use synthetic clocks/streams, without sleeping or network I/O."""

from types import SimpleNamespace

import httpx
import pytest
import test_structured_model_unit as structured_tests
from pydantic import SecretStr

from app.ai import structured
from app.media import files
from app.media_providers import http

creator_inputs = structured_tests.inputs


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


class TrickleStream(httpx.SyncByteStream):
    def __init__(self):
        self.elapsed = 0
        self.closed = False

    def __iter__(self):
        # Every read stays below socket read timeouts. The full response stays
        # below the old buffering threshold, which used to defer the check.
        for _ in range(1000):
            self.elapsed += 1
            yield b" "

    def close(self):
        self.closed = True


def trickle(monkeypatch, module, encoding="identity"):
    stream = TrickleStream()
    calls = []
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: stream.elapsed))

    def send(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "req_deadline", "content-encoding": encoding},
            stream=stream,
        )

    return stream, calls, httpx.MockTransport(send)


@pytest.mark.parametrize(
    "method,ambiguous,category,retryable",
    [("GET", False, "NETWORK", True), ("POST", True, "UNKNOWN_OUTCOME", False)],
)
def test_media_provider_deadline_checks_small_chunks(
    monkeypatch, method, ambiguous, category, retryable
):
    stream, calls, transport = trickle(monkeypatch, http)
    with pytest.raises(http.ProviderError) as raised:
        http.Transport("api.heygen.com", transport).request(
            method, "/video", {}, ambiguous=ambiguous
        )
    assert raised.value.category == category
    assert raised.value.retryable is retryable
    assert stream.elapsed == 121 and stream.closed
    assert len(calls) == 1


def test_text_model_deadline_checks_small_chunks_without_paid_replay(monkeypatch, creator_inputs):
    stream, calls, transport = trickle(monkeypatch, structured)
    adapter = structured.OpenAISelectionAdapter(
        structured.SelectionSettings(api_key=SecretStr("offline-fake-key"), model="test-model"),
        transport,
    )
    with pytest.raises(structured.ModelSelectionError) as raised:
        adapter.select(*creator_inputs)
    assert raised.value.category == "UNKNOWN_OUTCOME" and not raised.value.retryable
    assert raised.value.execution.request_id == "req_deadline"
    assert raised.value.execution.cost is None
    assert stream.elapsed == 121 and stream.closed
    assert len(calls) == 1


def test_media_download_deadline_checks_small_chunks_and_remains_retryable(monkeypatch):
    stream, calls, transport = trickle(monkeypatch, files)
    with pytest.raises(files.MediaRetrievalError):
        files._download("https://files.heygen.ai/offline-fixture.mp4", transport)
    assert stream.elapsed == 181 and stream.closed
    assert len(calls) == 1


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("method,ambiguous", [("GET", False), ("POST", True)])
def test_media_provider_rejects_compressed_success_before_decoding(
    monkeypatch, encoding, method, ambiguous
):
    stream, calls, transport = trickle(monkeypatch, http, encoding)
    with pytest.raises(http.ProviderError) as raised:
        http.Transport("api.heygen.com", transport).request(
            method, "/video", {}, ambiguous=ambiguous
        )
    assert raised.value.category == ("UNKNOWN_OUTCOME" if ambiguous else "INVALID_OUTPUT")
    assert not raised.value.retryable
    assert stream.elapsed == 0 and stream.closed
    assert len(calls) == 1 and calls[0].headers["accept-encoding"] == "identity"


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_text_model_rejects_compressed_success_without_paid_replay(
    monkeypatch, creator_inputs, encoding
):
    stream, calls, transport = trickle(monkeypatch, structured, encoding)
    adapter = structured.OpenAISelectionAdapter(
        structured.SelectionSettings(api_key=SecretStr("offline-fake-key"), model="test-model"),
        transport,
    )
    with pytest.raises(structured.ModelSelectionError) as raised:
        adapter.select(*creator_inputs)
    assert raised.value.category == "UNKNOWN_OUTCOME" and not raised.value.retryable
    assert raised.value.execution.cost is None
    assert stream.elapsed == 0 and stream.closed
    assert len(calls) == 1 and calls[0].headers["accept-encoding"] == "identity"


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_media_download_rejects_compressed_success_before_decoding(monkeypatch, encoding):
    stream, calls, transport = trickle(monkeypatch, files, encoding)
    with pytest.raises(files.MediaFileError) as raised:
        files._download("https://files.heygen.ai/offline-fixture.mp4", transport)
    assert type(raised.value) is files.MediaFileError
    assert stream.elapsed == 0 and stream.closed
    assert len(calls) == 1 and calls[0].headers["accept-encoding"] == "identity"
