"""Request metadata validation needs no database, credentials or provider calls."""

import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.media import service


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


@pytest.mark.parametrize("value", [None, "", "x" * 256, "id\nheader", "https://provider/id", 42])
def test_invalid_failure_request_ids_are_omitted(value):
    assert service._safe_failure_request_id(value) is None


@pytest.mark.parametrize("value", ["speech-job_12:34.56", "x" * 255])
def test_bounded_failure_request_ids_are_retained(value, monkeypatch):
    settings = get_settings().model_copy(
        update={
            "elevenlabs_api_key": None,
            "heygen_api_key": None,
            "hf_api_key_id": None,
            "hf_api_key_secret": None,
        }
    )
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    assert service._safe_failure_request_id(value) == value


@pytest.mark.parametrize("field", ["elevenlabs_api_key", "heygen_api_key", "hf_api_key_secret"])
@pytest.mark.parametrize(
    "echo", ["fixture-secret-value", "prefix-fixture-secret-value-suffix", "secret-value"]
)
def test_failure_request_ids_cannot_echo_configured_credentials(field, echo, monkeypatch):
    settings = get_settings().model_copy(update={field: SecretStr("fixture-secret-value")})
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    assert service._safe_failure_request_id(echo) is None
