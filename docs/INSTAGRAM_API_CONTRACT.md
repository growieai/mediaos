# Instagram transport contract

The transport boundary implements Instagram Login for a professional account. It does not grant permission, approve content, or authorize a reply. Live acceptance remains unverified until the owner connects an account and explicitly approves controlled testing.

## Source review

Reviewed 2026-09-21. [Meta's official Instagram Postman collection](https://www.postman.com/meta/instagram/folder/6raa77c/instagram-api-with-instagram-login) identifies the professional-account flow, its `instagram_business_*` permission family, and separation from Facebook Login. Facebook Login and Page tokens are intentionally not implemented.

The official generated SDK corroborates media container/publish parameters, including `is_ai_generated`: [IGUser](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/iguser.py). Its [IGMedia](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/igmedia.py), [IGComment](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/igcomment.py), and [InstagramInsightsResult](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/instagraminsightsresult.py) describe read, insights and reply edges and counter names. These definitions support the wire contract; they do not demonstrate successful calls for a particular account/API version.

Direct Meta developer pages returned HTTP 429 or were inaccessible during implementation. The exact OAuth/profile response shapes, insight permissions and webhook subscription payload therefore still require verification against the app dashboard and the following primary references before live execution:

- [Business Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login/)
- [Publishing](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing/)
- [Insights](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/insights/)
- [Comment moderation](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/comment-moderation/)
- [Webhooks](https://developers.facebook.com/docs/instagram-platform/webhooks/)

Tests use synthetic wire responses, not live recordings. Their explicit `v99.0` exercises version handling; it is not a recommended or verified Meta version.

## Interface

`InstagramSettings` requires an explicit API version, Instagram app ID/secret, exact registered public HTTPS redirect URI, and administrator-configured exact media hostname allowlist. `InstagramProvider(settings, token=SecretStr(...))` has no database or tenant access. Tests inject `httpx.MockTransport`.

| Method | Operation | Result |
| --- | --- | --- |
| `authorization_url(state, scopes)` | Builds Instagram authorization URL; no request | URL |
| `exchange_code(SecretStr(code))` | POST `api.instagram.com/oauth/access_token` | `TokenGrant` |
| `exchange_long_lived(token)` | GET `graph.instagram.com/access_token` | `TokenGrant` |
| `refresh_token(token)` | GET `graph.instagram.com/refresh_access_token` | `TokenGrant` |
| `profile()` | Versioned `/me` | `Observation[Profile]` |
| `create_image(user_id, url, caption, is_carousel_item=False)` | Creates image container | `Observation[CreatedObject]` |
| `create_image_child(user_id, url)` | Creates carousel child | `Observation[CreatedObject]` |
| `create_carousel(user_id, children, caption)` | Creates parent with 2–10 distinct children | `Observation[CreatedObject]` |
| `container_status(container_id)` | Reads status | `Observation[ContainerStatus]` |
| `publish(user_id, container_id)` | Publishes exact container | `Observation[CreatedObject]` |
| `read_media(id)` / `list_media(user_id, after=None)` | Reads media | Typed media/page observation |
| `insights(media_id, metrics=(...))` | Reads media counters | `Observation[Insights]` |
| `read_comment(id)` / `list_comments(media_id, after=None)` | Reads comments | Typed comment/page observation |
| `list_replies(comment_id, after=None)` | Reads replies | Typed page observation |
| `reply(comment_id, exact_approved_text)` | Sends exact approved reply | `Observation[CreatedObject]` |

Graph operations use only versioned paths on `graph.instagram.com`. Single-image and parent carousel submissions set `is_ai_generated=true`; child containers do not separately set that label. The platform label supplements approved caption/graphic disclosure and does not replace QA.

`TokenGrant.access_token` is a `SecretStr`; no raw token body is exposed in an observation. Missing expiry, account ID or permissions stays missing. Bind the short grant, long-lived grant and observed professional profile in the service. Never invent expiry or infer granted permissions from requested OAuth scopes. An absent permissions list must leave permission-dependent actions blocked. No unverified permissions endpoint is guessed as a fallback. Consumer or missing profile account types fail closed.

Graph `Observation[T]` has typed `payload`, `captured_at`, `endpoint`, `api_version`, safe `request_id`, `raw`, `raw_hash`, `response_hash`, and `redacted`. `raw` is observed JSON with credential fields/echoes and pagination URLs redacted. `raw_hash` hashes its canonical UTF-8 JSON; `response_hash` hashes exact HTTP JSON bytes before redaction. Do not describe redacted JSON as original bytes. Persist observations with tenant ownership and exact external-subject mapping.

Pages expose `payload.data`, `payload.after`, and `payload.has_more`. Only bounded cursors can be supplied to the same endpoint. Provider `paging.next` URLs are never followed. Page limits/checkpoints and poll cadence belong to the persisted caller.

Insights normalize only nonnegative scalar counters with `period=lifetime`. Keys are `reach`, `saved`, `shares`, `comments`, `follows`, `views`, `likes`; platform observations and learning preserve the provider key `saved`. Missing, unavailable, daily or ambiguous multi-value data stays null. No absent metric becomes zero. Unsupported metric requests fail rather than invent counts. This layer makes no attribution or causal-learning claims.

## Failure and security semantics

All calls are single attempts. Sanitized `SocialProviderError` exposes category, optional safe status/code/request ID, and retry metadata. Safe reads allow at most three persisted attempts; `retry_delay(completed_attempts)` uses exponential delay and bounded numeric `Retry-After`. This transport neither sleeps nor retries internally.

POST timeouts, uncertain server errors, malformed successes and unusable IDs become `UNKNOWN_OUTCOME`, with automatic replay prohibited. OAuth exchange/refresh are also non-replayable despite their GET endpoints. A crash after submission remains held for reconciliation. Container `PUBLISHED` is not itself a published media ID. Read/list results are evidence candidates: caption/time similarity or an empty list never automatically proves delivery or non-delivery.

Transport fixes the two API hostnames, HTTPS, no redirects/environment proxies, timeouts, request/response limits and numeric IDs. Dependency HTTP logs are suppressed during these requests because token-lifecycle query strings contain credentials. Image URLs must match exact configured public HTTPS hostnames; the adapter never fetches them. The caller must serve approved immutable JPEGs, bind their hashes to the publication plan and ensure Meta can fetch them without operator credentials.

Webhook ingress must bound the raw body and call `verify_webhook_signature` before parsing. It compares HMAC-SHA256 over exact bytes in constant time. `webhook_challenge` separately verifies the handshake token. `normalize_comment_webhook` is a pure parser and **does not authenticate a webhook**. It supports bounded Instagram comment changes with `value.media.id`, exact text and Unix-seconds entry timestamps. Unknown event categories are ignored; malformed comments are rejected. Bind account IDs to active tenant connections, verify media ownership, deduplicate event hashes and persist events. An inbound signature is never consent or outbound-reply approval.

## Live acceptance dependencies

The owner must connect a professional Instagram account through an Instagram app, configure secrets privately, verify a supported API version and registered public HTTPS callbacks, and obtain required permissions/access levels. Verify publishing, insights and comments independently for that account. Approved media hosting, webhook subscriptions, encrypted credentials and persisted tenant/approval/idempotency/rate-limit guards are integration-layer responsibilities. No DM transport or permission is implemented.

The live gate is an explicitly approved controlled post, exact published-media reconciliation, real insight observations, a signed real comment event and a separately approved reply. None was performed by the offline tests.

From `backend`:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_social_provider.py -q
.venv/Scripts/python.exe -m ruff check app/social_provider tests/test_social_provider.py
.venv/Scripts/python.exe -m mypy app/social_provider
```

The test module overrides the suite's database fixture; it performs no PostgreSQL reset. Verification: 89 offline tests, Ruff and mypy passed. Service/database acceptance is reported separately.
