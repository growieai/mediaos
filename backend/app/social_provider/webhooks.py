"""Verify exact raw bytes before parsing. Signatures never authorize outbound replies."""

import hashlib
import hmac
import re
from datetime import UTC, datetime

from pydantic import SecretStr, ValidationError

from .http import SocialProviderError, canonical_hash, credential, strict_json
from .schemas import CommentEvent, CommentWebhook

MAX_WEBHOOK_BYTES = 1_000_000


def verify_webhook_signature(body: bytes, signature: str, app_secret: SecretStr) -> bool:
    if (
        not isinstance(body, bytes)
        or len(body) > MAX_WEBHOOK_BYTES
        or not isinstance(signature, str)
        or not re.fullmatch(r"sha256=[0-9a-f]{64}", signature)
    ):
        return False
    expected = (
        "sha256=" + hmac.new(credential(app_secret).encode(), body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def webhook_challenge(mode: str, token: str, challenge: str, expected_token: SecretStr) -> str:
    if (
        mode != "subscribe"
        or not isinstance(token, str)
        or not token.isascii()
        or len(token) > 8192
        or not isinstance(challenge, str)
        or not re.fullmatch(r"[0-9]{1,128}", challenge)
        or not hmac.compare_digest(token, credential(expected_token))
    ):
        raise SocialProviderError("AUTHENTICATION")
    return challenge


def normalize_comment_webhook(body: bytes) -> CommentWebhook:
    """Pure schema normalization; caller must first authenticate HMAC, then bind tenant/account."""
    if not isinstance(body, bytes) or not 1 <= len(body) <= MAX_WEBHOOK_BYTES:
        raise SocialProviderError("INVALID_OUTPUT")
    try:
        payload = strict_json(body)
        if payload.get("object") != "instagram":
            raise ValueError("Instagram object required")
        entries = payload.get("entry")
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError("Bounded entries required")
        events, ignored = [], 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Entry object required")
            instant = entry.get("time")
            if type(instant) is not int or not 0 <= instant <= 4102444800:
                raise ValueError("Unix seconds required")
            changes = entry.get("changes", [])
            if not isinstance(changes, list) or len(changes) > 100:
                raise ValueError("Bounded changes required")
            for change in changes:
                if not isinstance(change, dict):
                    raise ValueError("Change object required")
                if change.get("field") != "comments":
                    ignored += 1
                    continue
                value = change.get("value")
                if not isinstance(value, dict) or not isinstance(value.get("media"), dict):
                    raise ValueError("Comment media required")
                sender = value.get("from", {})
                if not isinstance(sender, dict):
                    raise ValueError("Comment sender object required")
                normalized = {
                    "account_id": entry.get("id"),
                    "comment_id": value.get("id"),
                    "media_id": value["media"].get("id"),
                    "text": value.get("text"),
                    "sender_id": sender.get("id"),
                    "username": sender.get("username"),
                    "occurred_at": datetime.fromtimestamp(instant, UTC),
                }
                # Hash stable identity and content; batch boundaries do not change dedup identity.
                event_hash = canonical_hash(
                    {
                        **normalized,
                        "occurred_at": normalized["occurred_at"].isoformat(),
                    }
                )
                events.append(CommentEvent(**normalized, event_hash=event_hash))
                if len(events) > 500:
                    raise ValueError("Too many comment events")
        return CommentWebhook(
            raw_hash=hashlib.sha256(body).hexdigest(),
            events=events,
            ignored_changes=ignored,
        )
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, ValidationError):
        raise SocialProviderError("INVALID_OUTPUT") from None
