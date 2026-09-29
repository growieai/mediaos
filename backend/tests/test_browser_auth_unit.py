import json

import pytest
from pydantic import ValidationError

from app.auth.service import LoginInput, SetupInput, normalize_email
from app.config import Settings


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


@pytest.mark.parametrize("password", ["short", "x" * 73, "x" * 14, "😀" * 19, "x" * 16 + "\x00"])
def test_password_boundaries(password):
    with pytest.raises(ValidationError):
        LoginInput.model_validate_json(
            json.dumps({"email": "user@example.com", "password": password}), strict=True
        )


def test_credentials_are_secret_repr_and_payload_is_strict():
    obj = SetupInput.model_validate_json(
        json.dumps({"token": "a" * 64, "password": "a suitable passphrase"}), strict=True
    )
    assert "a suitable passphrase" not in repr(obj) and "a" * 64 not in repr(obj)
    for payload in (
        {"token": "a" * 64, "password": 123},
        {"token": "a" * 64, "password": "a suitable passphrase", "roles": ["ADMIN"]},
    ):
        with pytest.raises(ValidationError):
            SetupInput.model_validate_json(json.dumps(payload), strict=True)


@pytest.mark.parametrize(
    "email",
    [
        "notanemail",
        "evil\n@example.com",
        "user@localhost",
        "üser@example.com",
        "x" * 255 + "@example.com",
    ],
)
def test_email_is_bounded(email):
    with pytest.raises(ValueError):
        normalize_email(email)


def test_production_requires_explicit_https_origin(monkeypatch):
    monkeypatch.delenv("AUTH_PUBLIC_ORIGIN", raising=False)
    values = {
        "app_env": "production",
        "database_url": "postgresql+psycopg://mediaos_runtime:ignored@localhost/mediaos",
    }
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
    assert (
        Settings(
            _env_file=None, **values, auth_public_origin="https://mediaos.example.com"
        ).auth_public_origin
        == "https://mediaos.example.com"
    )
    for origin in (
        "http://mediaos.example.com",
        "https://mediaos.example.com/",
        "https://user:password@example.com",
        "https://example.com?query=1",
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **values, auth_public_origin=origin)
