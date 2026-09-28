"""Studio schema, feature gate and portrait integrity without database or network."""

import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from pydantic import ValidationError

from app.config import Settings
from app.services.workflows import ConflictError
from app.studio import service
from app.studio.schemas import CreateInfluencer


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def payload():
    return {
        "idempotency_key": "creator-unit",
        "name": "Alex",
        "category_id": "education",
        "language": "en",
        "tone": "WARM",
        "audience": ["Curious learners"],
        "objective": "Explain sourced ideas clearly.",
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"category_id": "fabricated"},
        {"tone": "anything"},
        {"language": "xx"},
        {"audience": []},
        {"audience": ["A", "A"]},
        {"audience": ["A\nB"]},
        {"name": "   "},
        {"name": " Alex"},
        {"name": "Alex\t"},
        {"objective": "A" * 501},
        {"creative_allowlist": ["Unsupported factual claim"]},
        {"disclosure": ""},
        {"tenant_id": str(uuid4())},
    ],
)
def test_schema_rejects_invalid_or_unowned_configuration(changes):
    with pytest.raises(ValidationError):
        CreateInfluencer.model_validate(payload() | changes)


def test_disabled_creation_stops_before_database(monkeypatch):
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(enable_external_creators=False)
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Feature gate touched persistence")

    monkeypatch.setattr(service, "transaction", forbidden)
    with pytest.raises(ConflictError, match="disabled"):
        service.create_influencer(uuid4(), "not-a-token", CreateInfluencer(**payload()), uuid4())


def test_creator_flag_is_supported_without_enabling_automatic_dispatch():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://mediaos_runtime@localhost/mediaos_test",
        enable_external_creators=True,
    )
    assert settings.enable_external_creators
    assert not settings.enable_auto_publish
    for key in ("enable_auto_publish", "enable_auto_replies", "enable_video"):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                database_url="postgresql+psycopg://mediaos_runtime@localhost/mediaos_test",
                **{key: True},
            )


def test_portrait_checks_stored_hash_and_strips_image_metadata(tmp_path, monkeypatch):
    path = tmp_path / "portrait.png"
    Image.new("RGB", (20, 20), "blue").save(path)
    row = {
        "version": 1,
        "reference_path": "characters/example/portrait.png",
        "reference_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    repo = SimpleNamespace(one=lambda *a, **kw: {}, all=lambda *a, **kw: [row])
    monkeypatch.setattr(service, "catalog_path", lambda value: path)
    assert service.portrait(repo, uuid4()).startswith(b"\x89PNG")
    path.write_bytes(b"modified")
    with pytest.raises(ConflictError, match="checksum"):
        service.portrait(repo, uuid4())
