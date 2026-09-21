"""Connection controls are checked before credentials or any provider request."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.services.workflows import ConflictError
from app.social import service


@pytest.fixture(scope="session", autouse=True)
def database():
    # These service-boundary tests must never initialize the PostgreSQL fixture.
    yield None


def forbidden(*args, **kwargs):
    raise AssertionError("Disabled or unauthorized refresh touched an external dependency")


def test_disabled_refresh_does_not_read_credentials_or_contact_provider(monkeypatch):
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(social_connect_enabled=False)
    )
    for dependency in (
        "transaction",
        "connected_provider",
        "provider",
        "service_token",
        "vault_key",
    ):
        monkeypatch.setattr(service, dependency, forbidden)
    with pytest.raises(ConflictError, match="Instagram connection is disabled"):
        service.refresh_connection(uuid4(), "synthetic-caller", uuid4())


def test_enabled_refresh_still_requires_admin_before_credentials(monkeypatch):
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(social_connect_enabled=True)
    )
    for dependency in ("connected_provider", "provider", "service_token", "vault_key"):
        monkeypatch.setattr(service, dependency, forbidden)

    def require(role):
        assert role == "ADMIN"
        raise PermissionError("Administrator required")

    @contextmanager
    def transaction(tenant, token):
        yield SimpleNamespace(require=require, one=forbidden)

    monkeypatch.setattr(service, "transaction", transaction)
    with pytest.raises(PermissionError, match="Administrator required"):
        service.refresh_connection(uuid4(), "synthetic-operator", uuid4())


def test_enabled_authorized_refresh_calls_provider_once_and_persists_new_expiry(monkeypatch):
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(social_connect_enabled=True)
    )
    tenant, cid = uuid4(), uuid4()
    events = []
    saved = []

    def require(role):
        events.append(("require", role))

    def one(table, **filters):
        assert table == "social_connections" and filters == {"id": cid}
        events.append(("connection", cid))
        return {"id": cid}

    def execute(statement, parameters):
        assert "social_rotate_token" in str(statement)
        saved.append(parameters)

    @contextmanager
    def transaction(tid, token):
        assert tid == tenant
        events.append(("transaction", token))
        yield SimpleNamespace(require=require, one=one, connection=SimpleNamespace(execute=execute))

    current_token = SecretStr("synthetic-old-provider-token")

    def refresh(token):
        assert token is current_token
        events.append(("refresh", None))
        return SimpleNamespace(
            access_token=SecretStr("synthetic-new-provider-token"), expires_in=3600
        )

    def connected(tid, connection_id):
        assert tid == tenant and connection_id == cid
        assert events == [
            ("transaction", "synthetic-admin"),
            ("require", "ADMIN"),
            ("connection", cid),
        ]
        return SimpleNamespace(token=current_token, refresh_token=refresh), {"id": cid}

    monkeypatch.setattr(service, "transaction", transaction)
    monkeypatch.setattr(service, "connected_provider", connected)
    monkeypatch.setattr(service, "service_token", lambda tid: "synthetic-connector")
    monkeypatch.setattr(service, "vault_key", lambda: "synthetic-vault-key")
    monkeypatch.setattr(service, "provider", forbidden)
    before = datetime.now(UTC)
    assert service.refresh_connection(tenant, "synthetic-admin", cid) == {"status": "REFRESHED"}
    assert events.count(("refresh", None)) == 1
    assert len(saved) == 1 and saved[0]["id"] == cid
    assert saved[0]["token"] == "synthetic-new-provider-token"
    assert (
        before + timedelta(hours=1) <= saved[0]["expiry"] <= datetime.now(UTC) + timedelta(hours=1)
    )
