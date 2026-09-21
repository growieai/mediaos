"""Recover already saved signed comments locally after publication confirmation."""

# ruff: noqa: F811 -- imported pytest fixtures are intentionally injected.
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from conftest import headers
from sqlalchemy import text
from test_social_integration import (  # noqa: F401
    FakeInstagram,
    authorize,
    execute,
    ready,
    social,
    visual_context,
)
from test_social_replies_integration import signed_comment

from app.config import get_settings
from app.db.repository import transaction
from app.social import replies, service
from app.social_provider import normalize_comment_webhook


@pytest.fixture
def unknown_publication(client, social, monkeypatch):
    row, _, _, _ = ready(client, social)
    fake = FakeInstagram(social, "unknown")
    monkeypatch.setattr(service, "provider", lambda token=None: fake)
    authorize(client, social, row)
    response = execute(client, social, row)
    assert response.status_code == 200 and response.json()["status"] == "UNKNOWN_OUTCOME"

    def read_media(mid):
        fake.calls.append("read")
        return SimpleNamespace(
            payload=SimpleNamespace(
                id=mid,
                owner=SimpleNamespace(id=row["account_id"]),
                caption=row["plan"]["caption"],
                media_type="IMAGE",
                timestamp=datetime.now(UTC).isoformat(),
            ),
            response_hash="a" * 64,
            captured_at=datetime.now(UTC),
        )

    monkeypatch.setattr(fake, "read_media", read_media, raising=False)
    return {**social, "publication": row}, row, fake


def save_comment(monkeypatch, data, row, comment_id="4567"):
    body, signature = signed_comment(
        monkeypatch, data["a"], {**row, "post_id": "10003"}, comment_id=comment_id
    )
    assert service.receive_webhook(body, signature) == {"accepted": 1}
    return normalize_comment_webhook(body).events[0].model_dump(mode="json")


def rows(data, table, **filters):
    identity = data["a"]
    publication = data["publication"]
    # Session-scoped tenant identities retain earlier tests' immutable history.
    # Assert the exact publication's rows, not the accumulated tenant totals.
    if table == "social_webhook_events":
        filters["connection_id"] = UUID(publication["connection_id"])
    else:
        filters["workflow_run_id"] = UUID(publication["workflow_run_id"])
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        return repo.all(table, **filters)


def reconcile(client, data, row):
    return client.post(
        f"/v1/social-publishes/{row['id']}/reconcile",
        headers=headers(data["a"], "APPROVER"),
        json={
            "candidate_media_id": "10003",
            "confirm_external_match": True,
            "comment": "Synthetic provider read confirms the exact approved post.",
        },
    )


def retry(client, data, row, identity=None, role="OPERATOR"):
    return client.post(
        f"/v1/social-publishes/{row['id']}/comments/relink",
        headers=headers(identity or data["a"], role),
    )


def test_unknown_publish_comment_links_after_reconciliation_exactly_once(
    client, unknown_publication, monkeypatch
):
    data, row, fake = unknown_publication
    save_comment(monkeypatch, data, row)
    assert len(rows(data, "social_webhook_events")) == 1
    assert not rows(data, "social_comment_links")
    original = replies.ingest_webhook

    def committed_before_link(repo, hid):
        with transaction(UUID(data["a"]["tenant_id"]), data["a"]["tokens"]["OPERATOR"]) as other:
            assert other.one("social_publish_runs", id=UUID(row["id"]))["status"] == "PUBLISHED"
            for key in ("social:" + row["id"], "social-account:" + row["account_id"]):
                assert other.connection.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": key},
                ).scalar_one()
        return original(repo, hid)

    monkeypatch.setattr(replies, "ingest_webhook", committed_before_link)
    response = reconcile(client, data, row)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PUBLISHED"
    assert response.json()["comment_relink"] == {
        "publish_run_id": row["id"],
        "processed": 1,
        "linked": 1,
        "has_more": False,
        "network_performed": False,
    }
    assert len(rows(data, "social_comment_links")) == 1
    calls = fake.calls.copy()
    assert retry(client, data, row).json()["processed"] == 0
    assert reconcile(client, data, row).json()["comment_relink"]["linked"] == 0
    assert fake.calls == calls and fake.calls.count("publish") == 1
    assert len(rows(data, "audit_events", event_type="PLATFORM_COMMENT_LINKED")) == 1
    assert not rows(data, "social_reply_runs")


def test_interrupted_relink_retains_publication_and_prior_links_for_explicit_retry(
    client, unknown_publication, monkeypatch
):
    data, row, fake = unknown_publication
    save_comment(monkeypatch, data, row, "4567")
    save_comment(monkeypatch, data, row, "4568")
    original, invocations = replies.ingest_webhook, []

    def fail_second(repo, hid):
        invocations.append(hid)
        if len(invocations) == 2:
            raise RuntimeError("synthetic-link-interruption")
        return original(repo, hid)

    monkeypatch.setattr(replies, "ingest_webhook", fail_second)
    response = reconcile(client, data, row)
    assert response.status_code == 200 and response.json()["status"] == "PUBLISHED"
    assert response.json()["comment_relink"]["status"] == "RETRY_REQUIRED"
    assert len(rows(data, "social_comment_links")) == 1
    calls = fake.calls.copy()
    monkeypatch.setattr(replies, "ingest_webhook", original)
    # Repair remains local when every outbound/intake feature flag is disabled.
    monkeypatch.setattr(get_settings(), "social_connect_enabled", False)
    monkeypatch.setattr(get_settings(), "social_publish_enabled", False)
    monkeypatch.setattr(get_settings(), "social_reply_enabled", False)
    retried = retry(client, data, row)
    assert retried.status_code == 200, retried.text
    assert retried.json()["linked"] == 1 and not retried.json()["has_more"]
    assert len(rows(data, "social_comment_links")) == 2
    assert fake.calls == calls


def test_saved_comment_relink_is_bounded_and_repeated_batches_make_progress(
    client, unknown_publication, monkeypatch
):
    data, row, fake = unknown_publication
    monkeypatch.setattr(service, "COMMENT_RELINK_BATCH", 2)
    for cid in range(4560, 4565):
        save_comment(monkeypatch, data, row, str(cid))
    first = reconcile(client, data, row).json()["comment_relink"]
    assert first["processed"] == first["linked"] == 2 and first["has_more"]
    calls = fake.calls.copy()
    second = retry(client, data, row).json()
    last = retry(client, data, row).json()
    assert second["processed"] == second["linked"] == 2 and second["has_more"]
    assert last["processed"] == last["linked"] == 1 and not last["has_more"]
    assert retry(client, data, row).json()["processed"] == 0
    assert len(rows(data, "social_comment_links")) == 5 and fake.calls == calls


def test_invalid_saved_comments_cannot_starve_later_valid_evidence(
    client, unknown_publication, monkeypatch
):
    data, row, _ = unknown_publication
    payload = save_comment(monkeypatch, data, row)
    actor = data["tokens"][data["a"]["tenant_id"]]
    variants = [
        {"text": "\u00a0\u3000"},
        {"text": "x" * 4001},
        {"occurred_at": "2026-09-21T24:00:00+00:00"},
        {"occurred_at": "2026-02-30T00:00:00+00:00"},
        {"username": 7},
        {"sender_id": "made-up"},
        {"unexpected_field": True},
    ]
    # Explicit trusted test setup of malformed connector records. Production
    # signature intake produces typed comments; none of this data is publishable.
    with transaction(UUID(data["a"]["tenant_id"]), actor) as repo:
        for index in range(30):
            bad = payload | {"comment_id": str(9000 + index)} | variants[index % len(variants)]
            repo.connection.execute(
                text("SELECT social_record_webhook(:id,:key,CAST(:p AS jsonb))"),
                {"id": row["connection_id"], "key": str(uuid4()), "p": json.dumps(bad)},
            )
    save_comment(monkeypatch, data, row, "999999")
    monkeypatch.setattr(service, "COMMENT_RELINK_BATCH", 1)
    assert reconcile(client, data, row).json()["comment_relink"]["linked"] == 1
    result = retry(client, data, row)
    assert result.status_code == 200, result.text
    assert result.json()["linked"] == 1 and not result.json()["has_more"]
    assert len(rows(data, "social_webhook_events")) == 32
    assert len(rows(data, "social_comment_links")) == 2


def test_relink_requires_identity_operator_owned_confirmed_publication(
    client, unknown_publication, monkeypatch
):
    data, row, _ = unknown_publication
    route = f"/v1/social-publishes/{row['id']}/comments/relink"
    assert client.post(route).status_code == 401
    assert retry(client, data, row, role="APPROVER").status_code == 403
    assert retry(client, data, row, identity=data["b"]).status_code == 404
    assert retry(client, data, row).status_code == 409
    save_comment(monkeypatch, data, row)
    assert reconcile(client, data, row).status_code == 200
    assert retry(client, data, row, identity=data["b"]).status_code == 404
