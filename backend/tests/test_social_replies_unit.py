import json
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.community.schemas import CommunityEventInput, CommunityEventPayload
from app.db.repository import canonical_hash
from app.social import replies


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def event():
    return {
        "schema_version": 1,
        "mode": "PLATFORM",
        "origin": "instagram:media:1:comment:2",
        "participant_reference": "instagram:3",
        "comment_text": "Thanks",
        "captured_at": datetime.now(UTC).isoformat(),
    }


def receipt(**changes):
    raw = {"id": "1234"}
    return {
        "schema_version": 1,
        "id": "1234",
        "response_hash": "a" * 64,
        "captured_at": datetime.now(UTC),
        "raw": raw,
        "raw_hash": canonical_hash(raw),
        "text_hash": canonical_hash("Exact reviewed reply"),
        **changes,
    }


def test_platform_observation_readable_but_manual_request_cannot_claim_it():
    assert CommunityEventPayload.model_validate_json(json.dumps(event())).mode == "PLATFORM"
    with pytest.raises(ValidationError):
        CommunityEventInput.model_validate_json(json.dumps({**event(), "idempotency_key": "key"}))


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "bad/id"},
        {"raw_hash": "0" * 64},
        {"raw": {"id": "other"}},
        {"captured_at": datetime.now()},
        {"captured_at": datetime.now(UTC).astimezone(timezone(timedelta(hours=1)))},
        {"response_hash": "not-a-digest"},
        {"text_hash": "unknown"},
        {"raw": {"id": "1234", "unbounded": "x" * 100001}},
    ],
)
def test_receipt_shape_identity_and_provenance_fail_closed(changes):
    with pytest.raises(ValidationError):
        replies.ReplyReceipt.model_validate(receipt(**changes))


def test_reply_authorization_is_exact_and_cannot_accept_replacement_text():
    with pytest.raises(ValidationError):
        replies.ReplyDecision.model_validate({"decision": "APPROVE", "text_hash": "a" * 64})
    with pytest.raises(ValidationError):
        replies.ReplyDecision.model_validate(
            {"decision": "AUTHORIZE_REPLY", "text_hash": "a" * 64, "text": "changed"}
        )
    with pytest.raises(ValidationError):
        replies.ReplyInput.model_validate({"idempotency_key": "x", "comment_id": "arbitrary"})


def test_disabled_reply_execution_does_not_resolve_identity_or_provider(monkeypatch):
    monkeypatch.setattr(
        replies, "get_settings", lambda: SimpleNamespace(social_reply_enabled=False)
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled dispatch must not touch database or provider")

    monkeypatch.setattr(replies, "transaction", forbidden)
    monkeypatch.setattr(replies.service, "connected_provider", forbidden)
    with pytest.raises(replies.ConflictError, match="disabled"):
        replies.execute(uuid4(), "unused", uuid4())
