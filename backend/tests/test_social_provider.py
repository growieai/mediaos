"""Offline transport contracts. No account, provider network or database is touched."""

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.social_provider import (
    InstagramProvider,
    InstagramSettings,
    SocialProviderError,
    normalize_comment_webhook,
    verify_webhook_signature,
    webhook_challenge,
)
from app.social_provider.http import Transport, canonical_hash

APP_SECRET = "offline-app-secret"
ACCESS_TOKEN = "offline-access-token"


@pytest.fixture(scope="session", autouse=True)
def database():
    """Override integration suite's destructive database fixture for this pure module."""


def settings(**overrides):
    return InstagramSettings(
        **{
            "api_version": "v99.0",
            "app_id": "12345",
            "app_secret": SecretStr(APP_SECRET),
            "redirect_uri": "https://console.example.org/oauth/callback",
            "media_host_allowlist": ("assets.example.org",),
            **overrides,
        }
    )


def provider(handler):
    return InstagramProvider(settings(), SecretStr(ACCESS_TOKEN), httpx.MockTransport(handler))


def response(payload, status=200, **headers):
    return httpx.Response(status, json=payload, headers=headers)


def no_network(_):
    pytest.fail("Validation should reject before HTTP")


def media(**overrides):
    return {
        "id": "123",
        "media_type": "CAROUSEL_ALBUM",
        "caption": "Exact approved text",
        "timestamp": "2026-09-21T01:02:03+0000",
        "owner": {"id": "456"},
        **overrides,
    }


def test_auth_url_signed_state_and_scope_safety():
    state = "A" * 240 + "." + "b" * 64
    url = provider(no_network).authorization_url(
        state,
        ("instagram_business_basic", "instagram_business_content_publish"),
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.instagram.com"
    assert parsed.path == "/oauth/authorize"
    assert query["state"] == [state]
    assert query["response_type"] == ["code"]
    assert query["enable_fb_login"] == ["0"]
    assert APP_SECRET not in url and ACCESS_TOKEN not in url


@pytest.mark.parametrize(
    "state,scopes",
    [
        ("short", ("instagram_business_basic",)),
        ("x" * 1025, ("instagram_business_basic",)),
        ("x" * 40 + "&next=https://bad.example", ("instagram_business_basic",)),
        ("x" * 40, ("pages_show_list",)),
        ("x" * 40, ("instagram_business_manage_messages",)),
        ("x" * 40, ("instagram_business_basic", "instagram_business_basic")),
    ],
)
def test_auth_rejects_wrong_flow_or_unbounded_state(state, scopes):
    with pytest.raises(SocialProviderError, match="INVALID_REQUEST"):
        provider(no_network).authorization_url(state, scopes)


@pytest.mark.parametrize(
    "override",
    [
        {"api_version": "latest"},
        {"api_version": "v99.0/evil"},
        {"redirect_uri": "http://127.0.0.1/callback"},
        {"redirect_uri": "https://user:pass@example.org/callback"},
        {"media_host_allowlist": ("assets.example.org.evil/path",)},
        {"media_host_allowlist": ("127.0.0.1",)},
        {"media_host_allowlist": ("*.example.org",)},
    ],
)
def test_configuration_is_explicit_and_bounded(override):
    with pytest.raises(ValidationError):
        settings(**override)


@pytest.mark.parametrize("envelope", [False, True])
def test_code_exchange_uses_fixed_host_and_returns_secret_grant(envelope):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.host == "api.instagram.com"
        assert request.url.path == "/oauth/access_token"
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["authorization_code"]
        assert body["code"] == ["offline-authorization-code"]
        assert body["client_secret"] == [APP_SECRET]
        grant = {
            "access_token": "issued-token",
            "user_id": 456,
            "permissions": ["instagram_business_basic", "instagram_business_content_publish"],
        }
        return response({"data": [grant]} if envelope else grant)

    result = provider(handle).exchange_code(SecretStr("offline-authorization-code"))
    assert len(calls) == 1 and result.user_id == "456"
    assert result.access_token.get_secret_value() == "issued-token"
    assert "issued-token" not in result.model_dump_json()
    assert result.permissions == ["instagram_business_basic", "instagram_business_content_publish"]
    assert result.expires_in is None


def test_absent_permissions_are_never_invented():
    result = provider(
        lambda _: response({"access_token": "new-token", "user_id": "456"})
    ).exchange_code(
        SecretStr("code"),
    )
    assert result.permissions == []


@pytest.mark.parametrize(
    "method,expected_path,grant_type",
    [
        ("exchange_long_lived", "/access_token", "ig_exchange_token"),
        ("refresh_token", "/refresh_access_token", "ig_refresh_token"),
    ],
)
def test_token_lifecycle_never_leaks_into_http_logs(
    method, expected_path, grant_type, caplog, monkeypatch
):
    # Capture directly even after application logging disables propagation.
    # This intentionally tests the transport filter before the safe formatter.
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", [caplog.handler])
        monkeypatch.setattr(logger, "propagate", False)
        monkeypatch.setattr(logger, "level", logging.DEBUG)

    def handle(request):
        assert request.url.host == "graph.instagram.com"
        assert request.url.path == expected_path
        assert request.url.params["grant_type"] == grant_type
        assert request.url.params["access_token"] == ACCESS_TOKEN
        logging.getLogger("httpcore.http11").debug("sensitive headers %s", ACCESS_TOKEN)
        return response(
            {"access_token": "replacement-token", "expires_in": 1000, "token_type": "bearer"}
        )

    with caplog.at_level(logging.DEBUG):
        result = getattr(provider(handle), method)(SecretStr(ACCESS_TOKEN))
    assert result.expires_in == 1000
    assert result.permissions == [] and result.user_id is None
    assert ACCESS_TOKEN not in caplog.text and APP_SECRET not in caplog.text
    assert "replacement-token" not in caplog.text
    # Suppression is scoped; unrelated caller logging still works.
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info("unrelated-safe-log")
    assert "unrelated-safe-log" in caplog.text


def test_profile_requires_professional_and_preserves_provenance():
    def handle(request):
        assert request.url.host == "graph.instagram.com"
        assert request.url.path == "/v99.0/me"
        assert request.headers["authorization"] == "Bearer " + ACCESS_TOKEN
        assert "access_token" not in request.url.params
        return response(
            {
                "id": "9",
                "user_id": "456",
                "username": "test_creator",
                "account_type": "MEDIA_CREATOR",
                "new_provider_field": "retained",
            }
        )

    result = provider(handle).profile()
    assert result.payload.id == "456" and result.payload.account_type == "MEDIA_CREATOR"
    assert result.raw["new_provider_field"] == "retained"
    assert result.raw_hash == canonical_hash(result.raw)
    assert result.captured_at.utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "456", "username": "test", "account_type": "PERSONAL"},
        {"id": "456", "username": "test"},
        {"id": "../456", "username": "test", "account_type": "BUSINESS"},
    ],
)
def test_profile_fails_closed(payload):
    with pytest.raises(SocialProviderError, match="INVALID_OUTPUT"):
        provider(lambda _: response(payload)).profile()


def test_image_carousel_publish_and_exact_reply_requests():
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path, parse_qs(request.content.decode())))
        return response({"id": str(100 + len(calls))})

    client = provider(handle)
    assert (
        client.create_image(
            "456", "https://assets.example.org/approved.jpg", "AI disclosure"
        ).payload.id
        == "101"
    )
    client.create_image_child("456", "https://assets.example.org/child.jpg")
    client.create_carousel("456", ["101", "102"], "Exact caption")
    client.publish("456", "103")
    client.reply("987", "  Exact approved reply.\nAI disclosure  ")
    assert calls[0] == (
        "POST",
        "/v99.0/456/media",
        {
            "image_url": ["https://assets.example.org/approved.jpg"],
            "caption": ["AI disclosure"],
            "is_ai_generated": ["true"],
        },
    )
    assert calls[1][2]["is_carousel_item"] == ["true"]
    assert "is_ai_generated" not in calls[1][2]
    assert calls[2][2] == {
        "media_type": ["CAROUSEL"],
        "is_ai_generated": ["true"],
        "children": ["101,102"],
        "caption": ["Exact caption"],
    }
    assert calls[3] == ("POST", "/v99.0/456/media_publish", {"creation_id": ["103"]})
    assert calls[4] == (
        "POST",
        "/v99.0/987/replies",
        {"message": ["  Exact approved reply.\nAI disclosure  "]},
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://assets.example.org.evil.org/x.jpg",
        "http://assets.example.org/x.jpg",
        "https://127.0.0.1/x.jpg",
        "https://assets.example.org@evil.org/x.jpg",
        "https://assets.example.org/x.jpg#secret",
        "https://assets.example.org:444/x.jpg",
        "https://other.example.org/x.jpg",
        "file:///C:/secret.jpg",
    ],
)
def test_asset_url_is_exact_host_allowlisted(url):
    with pytest.raises(SocialProviderError, match="INVALID_REQUEST"):
        provider(no_network).create_image("456", url)


@pytest.mark.parametrize(
    "call",
    [
        lambda client: client.publish("456/evil", "123"),
        lambda client: client.read_media("%2e%2e"),
        lambda client: client.create_carousel("456", ["123", "123"], "caption"),
        lambda client: client.create_carousel("456", ["123"], "caption"),
        lambda client: client.create_image(
            "456", "https://assets.example.org/x.jpg", "child-caption", True
        ),
        lambda client: client.reply("456", "x" * 2201),
        lambda client: client.list_comments("456", "evil?access_token=x"),
    ],
)
def test_invalid_mutation_and_cursor_are_rejected_before_network(call):
    with pytest.raises(SocialProviderError, match="INVALID_REQUEST"):
        call(provider(no_network))


def test_pagination_only_reuses_cursor_never_provider_next_url():
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return response(
                {
                    "data": [media()],
                    "paging": {
                        "cursors": {"after": "cursor_1=="},
                        "next": "https://evil.example.org/?access_token=" + ACCESS_TOKEN,
                    },
                }
            )
        assert request.url.host == "graph.instagram.com"
        assert request.url.params["after"] == "cursor_1=="
        return response({"data": []})

    client = provider(handle)
    first = client.list_media("456")
    assert first.payload.has_more and first.payload.after == "cursor_1=="
    assert first.payload.data[0].id == "123"
    assert first.raw["paging"]["next"] == "[REDACTED]"
    assert first.redacted and ACCESS_TOKEN not in first.model_dump_json()
    assert not client.list_media("456", first.payload.after).payload.has_more


def test_provider_secrets_are_removed_from_payload_keys_and_values():
    original = media(caption=f"{ACCESS_TOKEN} {APP_SECRET}", **{ACCESS_TOKEN: "should redact"})
    result = provider(lambda _: response(original, **{"x-fb-trace-id": APP_SECRET})).read_media(
        "123"
    )
    serialized = result.model_dump_json()
    assert ACCESS_TOKEN not in serialized and APP_SECRET not in serialized
    assert result.payload.caption == "[REDACTED] [REDACTED]"
    assert result.redacted and result.request_id is None
    assert result.response_hash == hashlib.sha256(response(original).content).hexdigest()


def test_read_and_reconcile_are_read_only_and_validate_exact_ids():
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/99"):
            return response({"id": "99", "status_code": "PUBLISHED"})
        return response(media())

    client = provider(handle)
    assert client.container_status("99").payload.status_code == "PUBLISHED"
    assert client.read_media("123").payload.owner.id == "456"
    assert all(request.method == "GET" for request in calls)
    with pytest.raises(SocialProviderError, match="INVALID_OUTPUT"):
        client.read_media("999")


def test_comment_read_and_reply_listing():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        item = {"id": "987", "text": "Pregunta", "media": {"id": "123"}}
        return response(
            {"data": [item]} if request.url.path.endswith(("comments", "replies")) else item
        )

    client = provider(handle)
    assert client.read_comment("987").payload.text == "Pregunta"
    assert client.list_comments("123").payload.data[0].id == "987"
    assert client.list_replies("987").payload.data[0].id == "987"
    assert calls == ["/v99.0/987", "/v99.0/123/comments", "/v99.0/987/replies"]


def test_insights_missing_counts_remain_null_and_raw_values_survive():
    body = {
        "data": [
            {
                "id": "123/insights/reach/lifetime",
                "name": "reach",
                "period": "lifetime",
                "values": [{"value": 27}],
            },
            {"name": "saved", "period": "lifetime", "total_value": {"value": 0}},
            {"name": "shares", "period": "day", "values": [{"value": 3}]},
        ]
    }
    result = provider(lambda _: response(body)).insights("123")
    assert result.payload.metrics["reach"] == 27 and result.payload.metrics["saved"] == 0
    assert result.payload.metrics["shares"] is None
    assert result.payload.metrics["comments"] is None and result.payload.metrics["follows"] is None
    assert result.raw == body
    assert result.payload.definitions["shares"] == "Meta media shares; lifetime scalar; API v99.0"


def test_metric_definitions_do_not_change_when_a_missing_counter_becomes_available():
    missing = provider(lambda _: response({"data": []})).insights("123")
    observed = provider(
        lambda _: response(
            {"data": [{"name": "reach", "period": "lifetime", "values": [{"value": 0}]}]}
        )
    ).insights("123")
    assert missing.payload.metrics["reach"] is None and observed.payload.metrics["reach"] == 0
    assert missing.payload.definitions == observed.payload.definitions


@pytest.mark.parametrize(
    "data",
    [
        [{"name": "reach", "period": "lifetime", "values": [{"value": "20"}]}],
        [{"name": "reach", "period": "lifetime", "values": [{"value": True}]}],
        [{"name": "reach", "period": "lifetime", "values": [{"value": -1}]}],
        [
            {
                "name": "reach",
                "period": "lifetime",
                "values": [{"value": 2}],
                "total_value": {"value": 3},
            }
        ],
        [{"name": "reach", "id": "999/insights/reach/lifetime", "period": "lifetime"}],
        [{"name": "reach", "period": "lifetime"}, {"name": "reach", "period": "lifetime"}],
    ],
)
def test_metric_counter_shapes_fail_closed(data):
    with pytest.raises(SocialProviderError, match="INVALID_OUTPUT"):
        provider(lambda _: response({"data": data})).insights("123")


@pytest.mark.parametrize("method", ["publish", "reply", "create_image"])
@pytest.mark.parametrize(
    "failure", ["timeout", "server", "invalid_json", "invalid_schema", "transient"]
)
def test_ambiguous_post_outcomes_never_retry(method, failure):
    calls = []

    def handle(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(ACCESS_TOKEN, request=request)
        if failure == "server":
            return response({"error": {"message": ACCESS_TOKEN}}, 503)
        if failure == "invalid_json":
            return httpx.Response(200, content=b"{", headers={"content-type": "application/json"})
        if failure == "transient":
            return response({"error": {"code": 2, "is_transient": True}})
        return response({"id": "not-numeric"})

    client = provider(handle)
    with pytest.raises(SocialProviderError) as raised:
        if method == "publish":
            client.publish("456", "123")
        elif method == "reply":
            client.reply("987", "Approved text")
        else:
            client.create_image("456", "https://assets.example.org/x.jpg")
    error = raised.value
    assert error.category == "UNKNOWN_OUTCOME"
    assert not error.retryable and error.retry_delay(1) is None
    assert len(calls) == 1 and ACCESS_TOKEN not in str(error)


def test_get_backoff_metadata_is_bounded_without_hidden_replays():
    calls = []

    def handle(request):
        calls.append(request)
        return response(
            {"error": {"code": 4, "message": ACCESS_TOKEN}}, 429, **{"retry-after": "31"}
        )

    with pytest.raises(SocialProviderError) as raised:
        provider(handle).read_media("123")
    error = raised.value
    assert error.category == "RATE_LIMITED" and error.retryable
    assert error.retry_delay(1) == 31 and error.retry_delay(2) == 31
    assert error.retry_delay(3) is None and len(calls) == 1
    assert ACCESS_TOKEN not in str(error)


def test_explicit_post_rate_refusal_is_retryable_but_never_replayed_in_transport():
    calls = []

    def refuse(request):
        calls.append(request)
        return response({"error": {"code": 4}}, 429, **{"retry-after": "90"})

    with pytest.raises(SocialProviderError) as raised:
        provider(refuse).publish("456", "123")
    assert raised.value.category == "RATE_LIMITED"
    assert raised.value.retryable and raised.value.retry_after_seconds == 90
    assert len(calls) == 1


def test_slow_small_chunks_cannot_bypass_total_dispatch_deadline(monkeypatch):
    from app.social_provider import http

    elapsed = [0]

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(1000):
                elapsed[0] += 1
                yield b" "

    monkeypatch.setattr(http.time, "monotonic", lambda: elapsed[0])
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "application/json"}, stream=SlowStream()
        )
    )
    with pytest.raises(SocialProviderError) as raised:
        Transport(transport).request("POST", "graph.instagram.com", "/v25.0/123/media_publish")
    assert raised.value.category == "UNKNOWN_OUTCOME"
    assert elapsed[0] == 46


@pytest.mark.parametrize(
    "status,category",
    [(400, "INVALID_REQUEST"), (401, "AUTHENTICATION"), (403, "PERMISSION_DENIED")],
)
def test_definite_provider_failures_are_sanitized_and_not_retried(status, category):
    with pytest.raises(SocialProviderError) as raised:
        provider(lambda _: response({"error": {"message": APP_SECRET}}, status)).publish(
            "456", "123"
        )
    assert raised.value.category == category and not raised.value.retryable
    assert APP_SECRET not in str(raised.value)


def test_redirect_is_not_followed_and_proxy_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.example.org")
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example.org"})

    with pytest.raises(SocialProviderError, match="REDIRECT_REJECTED"):
        provider(handle).read_media("123")
    assert len(calls) == 1 and calls[0].url.host == "graph.instagram.com"


@pytest.mark.parametrize("raw", [b'{"id":"123","id":"999"}', b'{"count":NaN}', b"[]"])
def test_invalid_json_shapes_are_rejected(raw):
    with pytest.raises(SocialProviderError, match="INVALID_OUTPUT"):
        provider(
            lambda _: httpx.Response(200, content=raw, headers={"content-type": "application/json"})
        ).read_media("123")


def test_response_limit_without_content_length():
    class LargeStream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(32):
                yield b"x" * 65536

    with pytest.raises(SocialProviderError, match="RESPONSE_TOO_LARGE"):
        provider(
            lambda _: httpx.Response(
                200, stream=LargeStream(), headers={"content-type": "application/json"}
            )
        ).read_media("123")


@pytest.mark.parametrize(
    "host,path",
    [
        ("graph.facebook.com", "/v99.0/123"),
        ("evil.example.org", "/123"),
        ("graph.instagram.com", "//evil.example.org"),
        ("graph.instagram.com", "/../123"),
    ],
)
def test_transport_cannot_fetch_arbitrary_hosts_or_paths(host, path):
    with pytest.raises(SocialProviderError, match="INVALID_REQUEST"):
        Transport(httpx.MockTransport(no_network)).request("GET", host, path, safe_read=True)


def webhook_body(**value_overrides):
    return json.dumps(
        {
            "object": "instagram",
            "entry": [
                {
                    "id": "456",
                    "time": 1789952523,
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": "987",
                                "text": "Información",
                                "media": {"id": "123"},
                                "from": {"id": "654", "username": "reader"},
                                **value_overrides,
                            },
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    ).encode()


def test_webhook_hmac_exact_bytes_then_typed_comment_normalization():
    body = webhook_body()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, signature, SecretStr(APP_SECRET))
    assert not verify_webhook_signature(body + b" ", signature, SecretStr(APP_SECRET))
    assert not verify_webhook_signature(body, signature, SecretStr("wrong-secret"))
    normalized = normalize_comment_webhook(body)
    assert normalized.raw_hash == hashlib.sha256(body).hexdigest()
    event = normalized.events[0]
    assert (event.account_id, event.media_id, event.comment_id) == ("456", "123", "987")
    assert event.text == "Información" and event.sender_id == "654"
    assert event.occurred_at.utcoffset().total_seconds() == 0
    assert event.event_hash == normalize_comment_webhook(body).events[0].event_hash


@pytest.mark.parametrize(
    "signature", ["", "sha1=" + "a" * 40, "sha256=" + "a" * 63, "SHA256=" + "a" * 64]
)
def test_webhook_invalid_signature_format(signature):
    assert not verify_webhook_signature(webhook_body(), signature, SecretStr(APP_SECRET))


@pytest.mark.parametrize(
    "overrides", [{"id": "../987"}, {"media": {}}, {"text": None}, {"from": "bad"}]
)
def test_webhook_invalid_comment_cannot_enter_logic(overrides):
    with pytest.raises(SocialProviderError, match="INVALID_OUTPUT"):
        normalize_comment_webhook(webhook_body(**overrides))


def test_webhook_ignores_unrelated_fields_without_reclassifying_them_as_comments():
    body = json.dumps(
        {
            "object": "instagram",
            "entry": [
                {
                    "id": "456",
                    "time": 1789952523,
                    "changes": [{"field": "mentions", "value": {"text": "not a comment"}}],
                }
            ],
        }
    ).encode()
    result = normalize_comment_webhook(body)
    assert result.events == [] and result.ignored_changes == 1
    assert (
        webhook_challenge(
            "subscribe", "verification-token", "1234", SecretStr("verification-token")
        )
        == "1234"
    )
    with pytest.raises(SocialProviderError, match="AUTHENTICATION"):
        webhook_challenge("subscribe", "wrong-token", "1234", SecretStr("verification-token"))
