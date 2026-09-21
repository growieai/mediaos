import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from conftest import create, headers, request_data
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_visual_workflow import (  # noqa: F401
    approve_content,
    approve_visual,
    render,
    visual_context,
)

from app.config import get_settings
from app.db.repository import canonical_hash, transaction
from app.social import service
from app.social.seed import seed_social
from app.social_provider import SocialProviderError


@pytest.fixture
def social(visual_context, database, monkeypatch, tmp_path):  # noqa: F811
    visual = visual_context
    tokens = {}
    for identity in (visual["a"], visual["b"]):
        token = secrets.token_urlsafe(32)
        tokens[identity["tenant_id"]] = token
        seed_social(database, identity["tenant_id"], token)
    for name, value in {
        "ASSET_STORAGE_PATH": str(visual["storage"]),
        "SOCIAL_CONNECT_ENABLED": "true",
        "SOCIAL_PUBLISH_ENABLED": "true",
        "SOCIAL_SERVICE_TOKENS": json.dumps(tokens),
        "SOCIAL_VAULT_KEY": "k" * 32,
        "SOCIAL_PUBLIC_BASE_URL": "https://media.example.com",
        "SOCIAL_STORAGE_PATH": str(tmp_path / "social"),
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield {**visual, "tokens": tokens}
    get_settings.cache_clear()


def connection(data, identity=None, account=None):
    identity = identity or data["a"]
    tenant = UUID(identity["tenant_id"])
    digest = hashlib.sha256(str(uuid4()).encode()).hexdigest()
    with transaction(tenant, identity["tokens"]["ADMIN"]) as repo:
        state = repo.connection.execute(
            text("SELECT social_begin_oauth(:id,:hash)"),
            {"id": identity["influencer_id"], "hash": digest},
        ).scalar_one()
    with transaction(tenant, data["tokens"][str(tenant)]) as repo:
        repo.connection.execute(text("SELECT social_consume_oauth(:hash)"), {"hash": digest})
        return repo.connection.execute(
            text(
                "SELECT social_finish_oauth(:state,:account,'test_account','v25.0',CAST(:permissions AS jsonb),:token,:expiry,:key)"
            ),
            {
                "state": state,
                "account": account or str(uuid4().int)[:16],
                "permissions": json.dumps(
                    [
                        "instagram_business_basic",
                        "instagram_business_content_publish",
                        "instagram_business_manage_insights",
                        "instagram_business_manage_comments",
                    ]
                ),
                "token": "test-token-not-a-real-credential",
                "expiry": datetime.now(UTC) + timedelta(days=30),
                "key": "k" * 32,
            },
        ).scalar_one()


def ready(client, data, payload=None):
    identity = data["a"]
    run = create(client, identity, payload=payload)
    approve_content(client, identity, run)
    rendered = render(client, identity, run, data["configs"][0])
    approve_visual(client, identity, rendered)
    cid = connection(data)
    payload = {"connection_id": str(cid), "idempotency_key": str(uuid4())}
    response = client.post(
        f"/v1/renders/{rendered['id']}/social-publishes", headers=headers(identity), json=payload
    )
    assert response.status_code == 200, response.text
    return response.json(), run, rendered, payload


def authorize(client, data, row):
    response = client.post(
        f"/v1/social-publishes/{row['id']}/review",
        headers=headers(data["a"], "APPROVER"),
        json={
            "decision": "AUTHORIZE_PUBLISH",
            "plan_hash": row["plan_hash"],
            "reviewed_images": True,
            "reviewed_caption": True,
            "confirmed_account": True,
            "comment": None,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


class FakeInstagram:
    settings = SimpleNamespace(api_version="v25.0")

    def __init__(self, data, mode="success"):
        self.data, self.mode, self.calls = data, mode, []

    def result(self, kind, payload):
        tenant = UUID(self.data["a"]["tenant_id"])
        with transaction(tenant, self.data["tokens"][str(tenant)]) as repo:
            rows = repo.all("social_publish_jobs")
            assert any(row["status"] == "RUNNING" for row in rows)
        self.calls.append(kind)
        raw = {"data": payload}
        return SimpleNamespace(
            payload=SimpleNamespace(**payload),
            response_hash="d" * 64,
            raw=raw,
            raw_hash=canonical_hash(raw),
            captured_at=datetime.now(UTC),
        )

    def create_image(self, *args, **kwargs):
        return self.result("child", {"id": "10001"})

    def create_carousel(self, *args, **kwargs):
        return self.result("container", {"id": "10002"})

    def container_status(self, *args):
        return self.result("poll", {"status_code": "FINISHED"})

    def publish(self, *args):
        if self.mode == "unknown":
            self.calls.append("publish")
            raise SocialProviderError("UNKNOWN_OUTCOME")
        return self.result("publish", {"id": "10003"})

    def insights(self, media_id):
        result = self.result(
            "insights",
            {
                "media_id": media_id,
                "metrics": {"reach": 0, "saved": None},
                "definitions": {"reach": "lifetime reach", "saved": "lifetime saves"},
            },
        )
        return result


def execute(client, data, row):
    return client.post(
        f"/v1/social-publishes/{row['id']}/execute",
        headers=headers(data["a"]),
        json={"confirm_public_post": True},
    )


def test_persisted_social_publish_exact_approval_and_insights(client, social, monkeypatch):
    row, run, rendered, payload = ready(client, social)
    fake = FakeInstagram(social)
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    assert row["status"] == "AWAITING_PUBLISH_APPROVAL"
    blocked = execute(client, social, row)
    assert blocked.status_code == 200 and blocked.json()["status"] == "AWAITING_PUBLISH_APPROVAL"
    assert fake.calls == []
    authorize(client, social, row)
    response = execute(client, social, row)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "PUBLISHED" and result["post_id"] == "10003"
    assert [j["stage"] for j in result["jobs"]] == ["CHILD", "POLL", "PUBLISH"]
    assert execute(client, social, row).json()["status"] == "PUBLISHED"
    assert fake.calls == ["child", "poll", "publish"]
    duplicate = client.post(
        f"/v1/renders/{rendered['id']}/social-publishes", headers=headers(social["a"]), json=payload
    )
    assert duplicate.json()["id"] == row["id"]
    observation = client.post(
        f"/v1/social-publishes/{row['id']}/insights",
        headers=headers(social["a"]),
        json={"idempotency_key": "observation1"},
    )
    assert observation.status_code == 200, observation.text
    values = observation.json()["insights"][0]["payload"]["metrics"]
    assert values == {"reach": 0, "saved": None}
    repeated = client.post(
        f"/v1/social-publishes/{row['id']}/insights",
        headers=headers(social["a"]),
        json={"idempotency_key": "observation1"},
    )
    assert repeated.status_code == 200 and len(repeated.json()["insights"]) == 1
    assert fake.calls.count("insights") == 1


def test_multiple_carousel_children_are_committed_before_parent_and_publication(
    client, social, monkeypatch
):
    data = request_data(social["a"])
    source = data["source"]
    statement = "Requests are reviewed each Friday."
    start = len(source["raw_content"]) + 1
    source["raw_content"] += "\n" + statement
    source["evidence"].append(
        {"start": start, "end": start + len(statement), "statement": statement}
    )
    row, _, _, _ = ready(client, social, data)
    assert len(row["plan"]["slides"]) == 2

    class CarouselProvider(FakeInstagram):
        def create_image(self, *args, **kwargs):
            assert kwargs["is_carousel_item"] is True
            return self.result("child", {"id": str(11000 + len(self.calls))})

        def create_carousel(self, account, children, caption):
            assert children == ["11000", "11001"]
            assert caption == row["plan"]["caption"]
            return super().create_carousel(account, children, caption)

    fake = CarouselProvider(social)
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    result = execute(client, social, row)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "PUBLISHED"
    assert [job["stage"] for job in result.json()["jobs"]] == [
        "CHILD",
        "CHILD",
        "CONTAINER",
        "POLL",
        "PUBLISH",
    ]
    assert execute(client, social, row).json()["status"] == "PUBLISHED"
    assert fake.calls == ["child", "child", "container", "poll", "publish"]


def test_unknown_publish_cannot_replay_or_start_duplicate(client, social, monkeypatch):
    row, _, rendered, payload = ready(client, social)
    fake = FakeInstagram(social, "unknown")
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    result = execute(client, social, row)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "UNKNOWN_OUTCOME"
    assert execute(client, social, row).json()["status"] == "UNKNOWN_OUTCOME"
    assert fake.calls.count("publish") == 1
    payload["idempotency_key"] = str(uuid4())
    duplicate = client.post(
        f"/v1/renders/{rendered['id']}/social-publishes", headers=headers(social["a"]), json=payload
    )
    assert duplicate.status_code == 409


def test_cross_tenant_social_retrieval_mutation_approval_and_credentials(client, social):
    row, _, _, _ = ready(client, social)
    other = social["b"]
    assert (
        client.get(f"/v1/social-publishes/{row['id']}", headers=headers(other)).status_code == 404
    )
    with transaction(UUID(other["tenant_id"]), other["tokens"]["OPERATOR"]) as repo:
        assert (
            repo.connection.execute(
                text("SELECT count(*) FROM social_publish_runs WHERE id=:id"), {"id": row["id"]}
            ).scalar_one()
            == 0
        )
    for query in (
        "UPDATE social_publish_runs SET status='PUBLISHED' WHERE id=:id",
        "SELECT social_token(:id,'kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk')",
    ):
        with pytest.raises(DBAPIError):
            with transaction(
                UUID(social["a"]["tenant_id"]), social["a"]["tokens"]["OPERATOR"]
            ) as repo:
                repo.connection.execute(text(query), {"id": row["id"]})
    decision = {
        "decision": "AUTHORIZE_PUBLISH",
        "plan_hash": row["plan_hash"],
        "reviewed_images": True,
        "reviewed_caption": True,
        "confirmed_account": True,
        "comment": None,
    }
    assert (
        client.post(
            f"/v1/social-publishes/{row['id']}/review", headers=headers(social["a"]), json=decision
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/v1/social-publishes/{row['id']}/review",
            headers=headers(other, "APPROVER"),
            json=decision,
        ).status_code
        == 404
    )


def test_oauth_state_single_use_roles_and_cross_tenant_account(client, social):
    cid = connection(social, account="9999990000")
    with pytest.raises(DBAPIError):
        connection(social, social["b"], account="9999990000")
    with transaction(
        UUID(social["a"]["tenant_id"]), social["tokens"][social["a"]["tenant_id"]]
    ) as repo:
        assert (
            repo.connection.execute(
                text("SELECT social_token(:id,:key)"), {"id": cid, "key": "k" * 32}
            ).scalar_one()
            == "test-token-not-a-real-credential"
        )
    revoked = client.post(
        f"/v1/social/connections/{cid}/revoke", headers=headers(social["a"], "ADMIN")
    )
    assert revoked.status_code == 200
    with pytest.raises(DBAPIError):
        with transaction(
            UUID(social["a"]["tenant_id"]), social["tokens"][social["a"]["tenant_id"]]
        ) as repo:
            repo.connection.execute(
                text("SELECT social_token(:id,:key)"), {"id": cid, "key": "k" * 32}
            )
