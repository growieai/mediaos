"""Synthetic SQL/provider observations in mediaos_test; never send a real reply."""

# Imported fixtures are intentionally injected by pytest.
# ruff: noqa: F811
import hashlib
import hmac
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from conftest import create, headers, revise
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_community_integration import capture, community_identities, decide, review  # noqa: F401
from test_visual_workflow import approve_content, approve_visual, render

from app.config import get_settings
from app.db.repository import canonical_hash, transaction
from app.rendering.seed import seed_visual_config
from app.social import replies, service
from app.social.schemas import PublishInput
from app.social.seed import seed_social
from app.social_provider import SocialProviderError
from app.social_provider.schemas import CommentEvent


def sql(identity, query, parameters=None, role="OPERATOR"):
    with transaction(UUID(identity["tenant_id"]), identity["tokens"][role]) as repo:
        return repo.connection.execute(text(query), parameters or {}).scalar_one_or_none()


def raw(identity, table, id):
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        return repo.one(table, id=UUID(str(id)))


@pytest.fixture
def reply_parent(client, community_identities, database, monkeypatch, tmp_path):
    identity = community_identities[0]
    connector = secrets.token_urlsafe(32)
    identity["tokens"]["SOCIAL"] = connector
    seed_social(database, identity["tenant_id"], connector)
    monkeypatch.setenv("SOCIAL_REPLY_ENABLED", "true")
    monkeypatch.setenv("SOCIAL_SERVICE_TOKENS", json.dumps({identity["tenant_id"]: connector}))
    monkeypatch.setenv("SOCIAL_STORAGE_PATH", str(tmp_path / "social"))
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "asset_storage_path", tmp_path / "renders")

    def deny(*args, **kwargs):
        raise AssertionError("Integration tests cannot use real HTTP")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny)
    state_hash = hashlib.sha256(str(uuid4()).encode()).hexdigest()
    sid = sql(
        identity,
        "SELECT social_begin_oauth(:id,:hash)",
        {"id": identity["influencer_id"], "hash": state_hash},
        "ADMIN",
    )
    sql(identity, "SELECT social_consume_oauth(:hash)", {"hash": state_hash}, "SOCIAL")
    cid = sql(
        identity,
        "SELECT social_finish_oauth(:sid,:account,'test_reply_account','v25.0',CAST(:scopes AS jsonb),:token,:expiry,:vault)",
        {
            "sid": sid,
            "account": str(uuid4().int)[:16],
            "scopes": json.dumps(
                [
                    "instagram_business_basic",
                    "instagram_business_content_publish",
                    "instagram_business_manage_comments",
                    "instagram_business_manage_insights",
                ]
            ),
            "token": "synthetic-token-no-account",
            "expiry": datetime.now(UTC) + timedelta(days=1),
            "vault": "k" * 32,
        },
        "SOCIAL",
    )
    cfg = seed_visual_config(
        database,
        identity["tenant_id"],
        identity["influencer_id"],
        display_name="Virtual assistant",
        disclosure="This is an AI creator.",
    )
    run = create(client, identity)
    approve_content(client, identity, run)
    rendered = render(client, identity, run, cfg)
    approve_visual(client, identity, rendered)
    published = service.create(
        UUID(identity["tenant_id"]),
        identity["tokens"]["OPERATOR"],
        UUID(rendered["id"]),
        PublishInput(connection_id=cid, idempotency_key=str(uuid4())),
    )
    # Trusted test setup models a historical successful publication. Dispatch of
    # publications is covered in its own suite; no external request occurs here.
    with database.begin() as connection:
        connection.execute(
            text(
                "UPDATE social_publish_runs SET status='PUBLISHED',post_id='90123',published_at=clock_timestamp() WHERE id=:id"
            ),
            {"id": published["id"]},
        )
    yield identity, run, raw(identity, "social_publish_runs", published["id"])
    get_settings.cache_clear()


def observed(identity, parent, *, comment="Thanks", key=None, comment_id="4567", media_id=None):
    conn = raw(identity, "social_connections", parent["connection_id"])
    payload = CommentEvent(
        account_id=conn["account_id"],
        comment_id=comment_id,
        media_id=media_id or parent["post_id"],
        text=comment,
        sender_id="7890",
        username=None,
        occurred_at=datetime.now(UTC),
        event_hash="a" * 64,
    ).model_dump(mode="json")
    hid = sql(
        identity,
        "SELECT social_record_webhook(:cid,:key,CAST(:p AS jsonb))",
        {
            "cid": parent["connection_id"],
            "key": key or str(uuid4()),
            "p": json.dumps(payload),
        },
        "SOCIAL",
    )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["SOCIAL"]) as repo:
        return replies.ingest_webhook(repo, hid), hid


def reviewed(client, identity, parent):
    event, hid = observed(identity, parent)
    draft = review(client, identity, event)
    assert draft["status"] == "AWAITING_REVIEW"
    response = decide(client, identity, draft)
    assert response.status_code == 200, response.text
    return response.json(), event, hid


@pytest.fixture
def reply_run(client, reply_parent):
    identity, workflow, published = reply_parent
    draft, event, hid = reviewed(client, identity, published)
    result = replies.create(
        UUID(identity["tenant_id"]),
        identity["tokens"]["OPERATOR"],
        UUID(draft["id"]),
        replies.ReplyInput(idempotency_key=str(uuid4())),
    )
    return identity, workflow, published, draft, event, hid, result


def authorize(identity, run):
    return replies.decide(
        UUID(identity["tenant_id"]),
        identity["tokens"]["APPROVER"],
        run["id"],
        replies.ReplyDecision(decision="AUTHORIZE_REPLY", text_hash=run["text_hash"]),
    )


class MockReply:
    def __init__(self, identity, outcome=None):
        self.identity, self.outcome, self.calls = identity, outcome, []

    def reply(self, comment_id, exact_text):
        with transaction(
            UUID(self.identity["tenant_id"]), self.identity["tokens"]["SOCIAL"]
        ) as repo:
            jobs = repo.all("social_reply_jobs")
            running = next(j for j in jobs if j["status"] == "RUNNING")
            assert repo.one("skill_runs", id=running["skill_run_id"])["status"] == "RUNNING"
            assert len(repo.all("cost_events", skill_run_id=running["skill_run_id"])) == 1
        self.calls.append((comment_id, exact_text))
        if self.outcome:
            raise self.outcome
        raw = {"id": "888999"}
        return SimpleNamespace(
            payload=SimpleNamespace(id="888999"),
            raw=raw,
            raw_hash=canonical_hash(raw),
            response_hash="b" * 64,
            captured_at=datetime.now(UTC),
        )


def executor(monkeypatch, identity, outcome=None):
    mock = MockReply(identity, outcome)
    monkeypatch.setattr(service, "connected_provider", lambda *args: (mock, {}))
    return mock


def test_platform_bridge_is_idempotent_and_unknown_posts_stay_observations(reply_parent):
    identity, _, parent = reply_parent
    event, hid = observed(identity, parent)
    assert event["mode"] == "PLATFORM" and event["comment_text"] == "Thanks"
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["SOCIAL"]) as repo:
        assert replies.ingest_webhook(repo, hid)["id"] == event["id"]
        assert len(repo.all("social_comment_links", webhook_event_id=hid)) == 1
    assert observed(identity, parent, media_id="999999")[0] is None
    assert observed(identity, parent, comment="x" * 4001)[0] is None


def test_manual_input_cannot_forge_platform_provenance(client, reply_parent):
    identity, workflow, _ = reply_parent
    from test_community_unit import event_payload

    response = client.post(
        f"/v1/workflow-runs/{workflow['id']}/community-events",
        headers=headers(identity),
        json=event_payload(mode="PLATFORM"),
    )
    assert response.status_code == 422
    with pytest.raises(DBAPIError):
        sql(identity, "SELECT social_link_comment(:id)", {"id": uuid4()})


def test_review_does_not_send_and_dispatch_needs_separate_exact_authorization(
    reply_run, monkeypatch
):
    identity, workflow, _, draft, _, _, run = reply_run
    mock = executor(monkeypatch, identity)
    assert run["status"] == "AWAITING_REPLY_APPROVAL"
    assert (
        replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])[
            "jobs"
        ]
        == []
    )
    assert mock.calls == []
    with pytest.raises(PermissionError):
        replies.decide(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            run["id"],
            replies.ReplyDecision(decision="AUTHORIZE_REPLY", text_hash=run["text_hash"]),
        )
    authorize(identity, run)
    result = replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert result["status"] == "SENT" and result["reply_id"] == "888999"
    assert mock.calls == [(run["comment_id"], run["exact_text"])]
    assert run["exact_text"] == "\n".join(
        [b["text"] for b in draft["draft"]["blocks"]] + [draft["draft"]["disclosure"]]
    )
    assert result["jobs"][0]["result"]["raw"] == {"id": "888999"}
    assert raw(identity, "workflow_runs", workflow["id"])["state"] == "APPROVED"
    replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert len(mock.calls) == 1


def test_unknown_reply_cannot_replay_or_create_new_key(reply_run, monkeypatch):
    identity, _, _, draft, _, _, run = reply_run
    mock = executor(monkeypatch, identity, SocialProviderError("UNKNOWN_OUTCOME"))
    authorize(identity, run)
    result = replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert result["status"] == "UNKNOWN_OUTCOME"
    replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert len(mock.calls) == 1
    with pytest.raises(DBAPIError):
        replies.create(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(draft["id"]),
            replies.ReplyInput(idempotency_key=str(uuid4())),
        )


def test_runtime_cannot_mutate_or_forge_receipt(reply_run):
    identity, _, _, _, _, _, run = reply_run
    for statement in (
        "UPDATE social_reply_runs SET status='SENT' WHERE id=:id",
        "DELETE FROM social_reply_runs WHERE id=:id",
        "SELECT social_reserve_reply(:id)",
        "SELECT social_finish_reply(:id,'{}'::jsonb)",
    ):
        with pytest.raises(DBAPIError) as error:
            sql(identity, statement, {"id": run["id"]})
        assert error.value.orig.sqlstate == "42501"


def test_cross_tenant_reads_and_decisions_rejected(client, reply_run, community_identities):
    _, _, _, _, _, _, run = reply_run
    other = community_identities[1]
    assert (
        client.get(f"/v1/social-reply-runs/{run['id']}", headers=headers(other)).status_code == 404
    )
    assert (
        client.post(
            f"/v1/social-reply-runs/{run['id']}/review",
            headers=headers(other, "APPROVER"),
            json={"decision": "AUTHORIZE_REPLY", "text_hash": run["text_hash"], "comment": None},
        ).status_code
        == 404
    )


def test_new_comment_observation_blocks_old_authorized_reply(reply_run, monkeypatch):
    identity, _, parent, _, _, _, run = reply_run
    authorize(identity, run)
    observed(identity, parent, comment="Changed comment", comment_id=run["comment_id"])
    mock = executor(monkeypatch, identity)
    with pytest.raises(DBAPIError):
        replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert mock.calls == []


def test_latest_review_and_parent_revision_required(client, reply_run):
    identity, _, _, _, event, _, run = reply_run
    review(client, identity, event)
    with pytest.raises(DBAPIError):
        authorize(identity, run)


def test_changed_parent_blocks_authorized_reply(client, reply_run, monkeypatch):
    identity, workflow, _, _, _, _, run = reply_run
    authorize(identity, run)
    payload = raw(identity, "content_asset_versions", workflow["asset_version_id"])["payload"]
    revise(client, identity, workflow, payload)
    mock = executor(monkeypatch, identity)
    with pytest.raises(DBAPIError):
        replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert mock.calls == []


def test_concurrent_idempotency_returns_one_reply_intent(client, reply_parent):
    identity, _, parent = reply_parent
    draft, _, _ = reviewed(client, identity, parent)
    key = str(uuid4())

    def invoke(_):
        return replies.create(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(draft["id"]),
            replies.ReplyInput(idempotency_key=key),
        )["id"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(invoke, range(3)))
    assert len(set(results)) == 1


def test_interrupted_post_without_receipt_holds_unknown(reply_run, monkeypatch):
    identity, _, _, _, _, _, run = reply_run
    authorize(identity, run)
    sql(identity, "SELECT social_reserve_reply(:id)", {"id": run["id"]}, "SOCIAL")
    mock = executor(monkeypatch, identity)
    result = replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert result["status"] == "UNKNOWN_OUTCOME" and mock.calls == []


def test_bounded_known_refusal_retry_preserves_attempts(reply_run, monkeypatch, database):
    identity, _, _, _, _, _, run = reply_run
    authorize(identity, run)
    mock = executor(
        monkeypatch,
        identity,
        SocialProviderError("RATE_LIMITED", retryable=True, retry_after_seconds=120),
    )
    for attempt in range(1, 4):
        result = replies.execute(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"]
        )
        assert len(result["jobs"]) == attempt
        if attempt < 3:
            assert result["status"] == "AUTHORIZED"
            assert (
                result["jobs"][-1]["retry_at"] - result["jobs"][-1]["ended_at"]
            ).total_seconds() > 119
            replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
            assert len(mock.calls) == attempt
            with database.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE social_reply_jobs SET retry_at=clock_timestamp()-interval '1 second' WHERE reply_run_id=:id"
                    ),
                    {"id": run["id"]},
                )
                conn.execute(
                    text(
                        "UPDATE private.social_account_cooldowns SET retry_after=clock_timestamp()-interval '1 second' "
                        "WHERE tenant_id=:tenant AND account_id=(SELECT account_id FROM social_connections WHERE id=:connection)"
                    ),
                    {"tenant": identity["tenant_id"], "connection": run["connection_id"]},
                )
    assert result["status"] == "FAILED" and len(mock.calls) == 3


def test_reviewed_manual_comment_still_cannot_be_sent(client, reply_parent):
    identity, workflow, _ = reply_parent
    event = capture(client, identity, workflow, comment_text="Thanks")
    draft = review(client, identity, event)
    assert decide(client, identity, draft).status_code == 200
    with pytest.raises(DBAPIError):
        replies.create(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(draft["id"]),
            replies.ReplyInput(idempotency_key=str(uuid4())),
        )


def test_wrong_text_hash_and_rejection_cannot_authorize_send(reply_run, monkeypatch):
    identity, _, _, _, _, _, run = reply_run
    with pytest.raises(DBAPIError):
        replies.decide(
            UUID(identity["tenant_id"]),
            identity["tokens"]["APPROVER"],
            run["id"],
            replies.ReplyDecision(decision="AUTHORIZE_REPLY", text_hash="0" * 64),
        )
    rejected = replies.decide(
        UUID(identity["tenant_id"]),
        identity["tokens"]["APPROVER"],
        run["id"],
        replies.ReplyDecision(decision="REJECT", text_hash=run["text_hash"]),
    )
    assert rejected["status"] == "REJECTED"
    mock = executor(monkeypatch, identity)
    assert (
        replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])[
            "status"
        ]
        == "REJECTED"
    )
    assert mock.calls == []


def test_connection_revocation_blocks_authorized_reply(reply_run, monkeypatch):
    identity, _, _, _, _, _, run = reply_run
    authorize(identity, run)
    sql(identity, "SELECT social_revoke(:id)", {"id": run["connection_id"]}, "ADMIN")
    mock = executor(monkeypatch, identity)
    with pytest.raises(DBAPIError):
        replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert mock.calls == []


def test_same_idempotency_key_different_review_conflicts(client, reply_run):
    identity, _, _, _, event, _, run = reply_run
    newer = review(client, identity, event)
    assert decide(client, identity, newer).status_code == 200
    with pytest.raises(DBAPIError) as error:
        replies.create(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(newer["id"]),
            replies.ReplyInput(idempotency_key=run["idempotency_key"]),
        )
    assert error.value.orig.sqlstate == "23505"


def test_concurrent_reservation_commits_one_attempt(reply_run):
    identity, _, _, _, _, _, run = reply_run
    authorize(identity, run)

    def reserve(_):
        try:
            return sql(identity, "SELECT social_reserve_reply(:id)", {"id": run["id"]}, "SOCIAL")
        except DBAPIError as error:
            assert error.orig.sqlstate == "23514"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert len([result for result in results if result]) == 1
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert len(repo.all("social_reply_jobs", reply_run_id=run["id"])) == 1


def test_checkpoint_outage_recovers_saved_receipt_without_post_replay(reply_run, monkeypatch):
    identity, _, _, _, _, _, run = reply_run
    authorize(identity, run)
    mock = executor(monkeypatch, identity)
    original = replies._complete
    attempts = 0

    def fail_once(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise DBAPIError("checkpoint", {}, RuntimeError("synthetic database outage"))
        return original(*args)

    monkeypatch.setattr(replies, "_complete", fail_once)
    with pytest.raises(DBAPIError):
        replies.execute(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"])
    assert raw(identity, "social_reply_runs", run["id"])["status"] == "SENDING"
    recovered = replies.execute(
        UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], run["id"]
    )
    assert recovered["status"] == "SENT" and len(mock.calls) == 1


def test_arbitrary_retry_category_rejected_at_database_boundary(reply_run):
    identity, _, _, _, _, _, run = reply_run
    authorize(identity, run)
    job = sql(identity, "SELECT social_reserve_reply(:id)", {"id": run["id"]}, "SOCIAL")
    with pytest.raises(DBAPIError):
        sql(
            identity,
            "SELECT social_fail_reply(:id,'UNKNOWN_OUTCOME',true,false,0)",
            {"id": job},
            "SOCIAL",
        )
    assert raw(identity, "social_reply_jobs", job)["status"] == "RUNNING"


def reconnect(identity, published, *, account=None):
    previous = raw(identity, "social_connections", published["connection_id"])
    state_hash = hashlib.sha256(str(uuid4()).encode()).hexdigest()
    sid = sql(
        identity,
        "SELECT social_begin_oauth(:id,:hash)",
        {"id": identity["influencer_id"], "hash": state_hash},
        "ADMIN",
    )
    sql(identity, "SELECT social_consume_oauth(:hash)", {"hash": state_hash}, "SOCIAL")
    return sql(
        identity,
        "SELECT social_finish_oauth(:sid,:account,'test_reconnected','v25.0',CAST(:scopes AS jsonb),:token,:expiry,:vault)",
        {
            "sid": sid,
            "account": account or previous["account_id"],
            "scopes": json.dumps(previous["scopes"]),
            "token": "synthetic-reconnected-token",
            "expiry": datetime.now(UTC) + timedelta(days=1),
            "vault": "k" * 32,
        },
        "SOCIAL",
    )


def signed_comment(monkeypatch, identity, published, *, account=None, comment_id="4567"):
    settings = get_settings()
    monkeypatch.setattr(settings, "social_connect_enabled", True)
    monkeypatch.setattr(settings, "social_app_secret", SecretStr("offline-webhook-secret"))
    previous = raw(identity, "social_connections", published["connection_id"])
    body = json.dumps(
        {
            "object": "instagram",
            "entry": [
                {
                    "id": account or previous["account_id"],
                    "time": int(datetime.now(UTC).timestamp()),
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": comment_id,
                                "text": "Thanks",
                                "media": {"id": published["post_id"]},
                                "from": {"id": "7890"},
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"offline-webhook-secret", body, hashlib.sha256).hexdigest()
    return body, signature


def test_signed_webhook_commits_intake_before_linking(reply_parent, monkeypatch):
    identity, _, published = reply_parent
    body, signature = signed_comment(monkeypatch, identity, published)
    original, inspected = replies.ingest_webhook, []

    def verify_committed(repo, hid):
        # This separate connection must see the saved event and acquire the
        # account lock before linkage starts taking workflow/review locks.
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["SOCIAL"]) as other:
            assert other.connection is not repo.connection
            event = other.one("social_webhook_events", id=hid)
            assert other.connection.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": "social-account:" + event["payload"]["account_id"]},
            ).scalar_one()
        inspected.append(hid)
        return original(repo, hid)

    monkeypatch.setattr(replies, "ingest_webhook", verify_committed)
    assert service.receive_webhook(body, signature) == {"accepted": 1}
    assert service.receive_webhook(body, signature) == {"accepted": 1}
    assert len(inspected) == 2 and inspected[0] == inspected[1]
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert len(repo.all("social_comment_links", webhook_event_id=inspected[0])) == 1
        assert not repo.all("social_reply_runs")


def test_webhook_link_failure_survives_commit_and_replay_after_reconnect(reply_parent, monkeypatch):
    identity, _, published = reply_parent
    body, signature = signed_comment(monkeypatch, identity, published)
    original = replies.ingest_webhook

    def fail_link(repo, hid):
        raise RuntimeError("synthetic-link-interruption")

    monkeypatch.setattr(replies, "ingest_webhook", fail_link)
    with pytest.raises(RuntimeError, match="synthetic-link-interruption"):
        service.receive_webhook(body, signature)
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        saved = repo.all("social_webhook_events")
        assert len(saved) == 1 and saved[0]["connection_id"] == published["connection_id"]
        assert not repo.all("social_comment_links")
    current = reconnect(identity, published)
    monkeypatch.setattr(replies, "ingest_webhook", original)
    assert service.receive_webhook(body, signature) == {"accepted": 1}
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert len(repo.all("social_webhook_events")) == 1
        link = repo.one("social_comment_links", webhook_event_id=saved[0]["id"])
        assert link["connection_id"] == current
        assert link["publish_run_id"] == published["id"]


def test_reconnected_account_links_historical_post_and_requires_new_reply_approval(
    client, reply_run, monkeypatch
):
    identity, workflow, published, _, _, _, old_reply = reply_run
    authorize(identity, old_reply)
    current = reconnect(identity, published)
    assert current != published["connection_id"]
    body, signature = signed_comment(monkeypatch, identity, published)
    assert service.receive_webhook(body, signature) == {"accepted": 1}
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        link = repo.one("social_comment_links", connection_id=current)
        event = repo.one("community_events", id=link["event_id"])
        assert link["publish_run_id"] == published["id"]
        assert link["account_id"] == published["account_id"]
        assert event["workflow_run_id"] == UUID(workflow["id"])
    with pytest.raises(DBAPIError) as raised:
        sql(identity, "SELECT social_reserve_reply(:id)", {"id": old_reply["id"]}, "SOCIAL")
    assert raised.value.orig.sqlstate == "23514"
    reviewed_draft = review(client, identity, event)
    assert decide(client, identity, reviewed_draft).status_code == 200
    new_reply = replies.create(
        UUID(identity["tenant_id"]),
        identity["tokens"]["OPERATOR"],
        UUID(reviewed_draft["id"]),
        replies.ReplyInput(idempotency_key=str(uuid4())),
    )
    assert new_reply["connection_id"] == current and not new_reply["decisions"]
    mock = executor(monkeypatch, identity)
    assert (
        replies.execute(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], new_reply["id"]
        )["status"]
        == "AWAITING_REPLY_APPROVAL"
    )
    assert not mock.calls
    authorize(identity, new_reply)
    assert (
        replies.execute(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], new_reply["id"]
        )["status"]
        == "SENT"
    )
    assert len(mock.calls) == 1
    assert (
        raw(identity, "social_publish_runs", published["id"])["connection_id"]
        == published["connection_id"]
    )


@pytest.mark.parametrize("old_status", ["UNKNOWN_OUTCOME", "SENT"])
def test_reconnect_cannot_bypass_prior_uncertain_or_sent_reply(
    client, reply_run, monkeypatch, old_status
):
    identity, _, published, _, _, _, old_reply = reply_run
    authorize(identity, old_reply)
    if old_status == "UNKNOWN_OUTCOME":
        job = sql(identity, "SELECT social_reserve_reply(:id)", {"id": old_reply["id"]}, "SOCIAL")
        sql(
            identity,
            "SELECT social_fail_reply(:id,'UNKNOWN_OUTCOME',false,true,0)",
            {"id": job},
            "SOCIAL",
        )
    else:
        executor(monkeypatch, identity)
        assert (
            replies.execute(
                UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], old_reply["id"]
            )["status"]
            == "SENT"
        )
    current = reconnect(identity, published)
    assert service.receive_webhook(*signed_comment(monkeypatch, identity, published)) == {
        "accepted": 1
    }
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        link = repo.one("social_comment_links", connection_id=current)
        event = repo.one("community_events", id=link["event_id"])
    new_review = review(client, identity, event)
    assert decide(client, identity, new_review).status_code == 200
    with pytest.raises(DBAPIError) as raised:
        replies.create(
            UUID(identity["tenant_id"]),
            identity["tokens"]["OPERATOR"],
            UUID(new_review["id"]),
            replies.ReplyInput(idempotency_key=str(uuid4())),
        )
    assert raised.value.orig.sqlstate == "23514"


def test_same_media_id_on_different_account_does_not_link(reply_parent, monkeypatch):
    identity, _, published = reply_parent
    other_account = str(uuid4().int)[:16]
    reconnect(identity, published, account=other_account)
    assert service.receive_webhook(
        *signed_comment(monkeypatch, identity, published, account=other_account)
    ) == {"accepted": 1}
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert len(repo.all("social_webhook_events")) == 1
        assert not repo.all("social_comment_links")


def test_existing_review_can_create_fresh_authorization_after_reconnect(reply_run, monkeypatch):
    identity, _, published, reviewed_draft, _, _, old_reply = reply_run
    authorize(identity, old_reply)
    current = reconnect(identity, published)
    replacement = replies.create(
        UUID(identity["tenant_id"]),
        identity["tokens"]["OPERATOR"],
        UUID(reviewed_draft["id"]),
        replies.ReplyInput(idempotency_key=str(uuid4())),
    )
    assert replacement["review_id"] == old_reply["review_id"]
    assert replacement["comment_link_id"] == old_reply["comment_link_id"]
    assert replacement["connection_id"] == current != old_reply["connection_id"]
    assert replacement["text_hash"] == old_reply["text_hash"]
    assert not replacement["decisions"]
    with pytest.raises(DBAPIError) as raised:
        sql(identity, "SELECT social_reserve_reply(:id)", {"id": old_reply["id"]}, "SOCIAL")
    assert raised.value.orig.sqlstate == "23514"
    mock = executor(monkeypatch, identity)
    assert (
        replies.execute(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], replacement["id"]
        )["status"]
        == "AWAITING_REPLY_APPROVAL"
    )
    assert not mock.calls
    authorize(identity, replacement)
    assert (
        replies.execute(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], replacement["id"]
        )["status"]
        == "SENT"
    )
    assert len(mock.calls) == 1


def test_confirmed_platform_post_cannot_back_two_local_publications(client, reply_parent, database):
    identity, _, published = reply_parent
    original_render = raw(identity, "render_runs", published["render_run_id"])
    other_workflow = create(client, identity)
    approve_content(client, identity, other_workflow)
    other_render = render(
        client, identity, other_workflow, original_render["visual_config_version_id"]
    )
    approve_visual(client, identity, other_render)
    pending = service.create(
        UUID(identity["tenant_id"]),
        identity["tokens"]["OPERATOR"],
        UUID(other_render["id"]),
        PublishInput(connection_id=published["connection_id"], idempotency_key=str(uuid4())),
    )
    with pytest.raises(DBAPIError) as raised:
        # The invariant holds even below the guarded reconciliation service.
        with database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE social_publish_runs SET status='PUBLISHED',post_id=:post,published_at=clock_timestamp() WHERE id=:id"
                ),
                {"id": pending["id"], "post": published["post_id"]},
            )
    assert raised.value.orig.sqlstate == "23505"
    assert (
        raw(identity, "social_publish_runs", pending["id"])["status"] == "AWAITING_PUBLISH_APPROVAL"
    )
