# Controlled Instagram integration

This is a standalone, tenant-scoped Instagram Login integration for a professional account. It provides a connection lifecycle, exact image publication plans, persisted publishing attempts, real post observations and separately authorized comment replies. All external-action flags default to false. There is no automatic publisher, reply scheduler, DM integration or connection to existing Growie production services.

Offline tests demonstrate transport contracts and database protections. They do not demonstrate that an actual account has granted access or that Meta accepted a real post. Keep live verification pending until the owner connects the intended account and explicitly approves the acceptance actions below. Provider research and its limits are recorded in [INSTAGRAM_API_CONTRACT.md](INSTAGRAM_API_CONTRACT.md).

## Dependencies and private configuration

| Capability | Required dependencies | Remaining external verification |
| --- | --- | --- |
| Existing mock research/content/render loop | Standalone PostgreSQL, migrations and seed | No Instagram or AI credentials needed |
| Account connection | Instagram professional account; Instagram app credentials; supported explicit API version; registered public HTTPS callback; credential-vault key; tenant SOCIAL principal | Actual login, professional profile, granted permissions and token expiry |
| Controlled publishing | Active current connection; exact current content/visual approvals; reviewed JPEG plan; public HTTPS media origin; manual publishing flag | Meta image fetch, container readiness and controlled post receipt |
| Post insights | Confirmed platform post; active connection; permitted media metrics for pinned API version | Real observed counters; missing values must remain unknown |
| Platform comment intake | HTTPS webhook route; verification token; app secret; Meta comment subscription; matching published post | Real signed event and exact account/media mapping |
| Controlled reply | Verified PLATFORM event; current evidence-backed reviewed draft; separate exact-text outbound authorization; reply flag | Actual separately approved reply acknowledgement |

Set these values privately in the local `.env` or a production secret manager. Do not paste keys into chat, commit them, put them into the console, or reuse Growie production credentials.

```dotenv
AI_MOCK_MODE=true
SOCIAL_CONNECT_ENABLED=false
SOCIAL_PUBLISH_ENABLED=false
SOCIAL_REPLY_ENABLED=false
```

The app also needs `SOCIAL_APP_ID`, `SOCIAL_APP_SECRET`, `SOCIAL_API_VERSION`, `SOCIAL_REDIRECT_URI`, `SOCIAL_PUBLIC_BASE_URL`, `SOCIAL_VAULT_KEY` and `SOCIAL_WEBHOOK_VERIFY_TOKEN`. Choose a currently supported version from Meta's documentation/dashboard; there is deliberately no guessed default. `SOCIAL_REDIRECT_URI` must exactly match the registered public HTTPS callback, ending `/v1/social/oauth/callback`. `SOCIAL_PUBLIC_BASE_URL` is the public HTTPS origin that exposes only the reviewed media capability route. It has no credentials, query, fragment, nonstandard port or path prefix.

Generate a dedicated unpredictable vault key of at least 32 characters and back it up separately from the database. It encrypts stored social tokens and signs purpose-bound OAuth/media capabilities. Losing or changing it prevents old credentials from decrypting and invalidates existing capabilities. Rotation needs a planned reconnect or separate migration of encrypted credentials; changing `.env` alone is not credential rotation.

Run the existing migrate and seed commands against this project's standalone database. Seed provisions a dedicated tenant `SOCIAL` principal and writes `.local/social-credentials.json`; these credentials belong to the connector service and must not be used as an operator/approver console identity. It also creates `.local/social`, the private JPEG/receipt storage directory. Production supplies `SOCIAL_SERVICE_TOKENS`, JSON mapping tenant UUIDs to dedicated SOCIAL bearer tokens, through its secret manager. Standard human roles remain OPERATOR, APPROVER and ADMIN.

## Local and container setup

From the repository root, using the configured standalone environment:

```bash
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py dev
```

For a new Docker environment, first follow [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md): setup, start PostgreSQL, migrate and seed, then start API/console. Compose mounts only the separate ingestion/social credential files read-only and private render/media/social directories. It refuses to silently create missing social mount paths; seed first. Keep POSIX ownership aligned with `LOCAL_UID`/`LOCAL_GID`; protect inherited Windows ACLs.

Host tools use the configured loopback database address. Containers use `postgres:5432`; the console talks to `api:8000` internally. The public HTTPS callback/media origin is a separate explicit deployment dependency. `127.0.0.1:8000` is not a public callback for Meta. Docker ports remain bound to localhost. Do not expose PostgreSQL or the full private storage directory to obtain a working callback.

Compose passes explicit social flags/configuration to the API and stores social files under `/workspace/.local/social`. Leaving the flags false preserves the local mock workflow. Text model calls remain independently controlled by `AI_MOCK_MODE` and tenant model policy; enabling an Instagram flag does not enable paid AI.

## Internal endpoints

All paths below are under `/v1`. Internal endpoints require a human bearer credential and `X-Tenant-ID`; authorization is rechecked server-side. Public callback/capability/webhook exceptions have their own cryptographic checks and never accept caller-supplied tenant authority.

| Endpoint | Purpose / permission |
| --- | --- |
| GET `/social/dependencies` | Report configured flags and dependencies, no secret values |
| GET `/social/connections` | Tenant-owned connection versions and revocations |
| POST `/social/connect` | ADMIN starts OAuth for `{influencer_id}` |
| GET `/social/oauth/callback` | Public OAuth callback; validates and consumes signed expiring persisted state |
| POST `/social/connections/{id}/refresh` | ADMIN explicitly refreshes token |
| POST `/social/connections/{id}/revoke` | ADMIN appends irreversible local revocation |
| GET `/workflow-runs/{id}/social-publishes` | Saved plans, jobs, decisions and observations |
| POST `/renders/{id}/social-publishes` | OPERATOR saves exact JPEG plan using `{connection_id,idempotency_key}` |
| GET `/social-publishes/{id}` | Saved publication detail |
| GET `/social-publishes/{id}/slides/{index}` | Authenticated exact JPEG preview |
| POST `/social-publishes/{id}/review` | APPROVER authorizes or rejects exact plan hash |
| POST `/social-publishes/{id}/execute` | OPERATOR explicitly dispatches with `{confirm_public_post:true}` |
| POST `/social-publishes/{id}/reconcile` | APPROVER records inspected external match for uncertain publish |
| POST `/social-publishes/{id}/insights` | OPERATOR fetches/retries bounded real observations using `{idempotency_key}` |
| GET/POST `/social-publishes/{id}/learning` | Inspect reports / OPERATOR compares `{baseline_snapshot_id,current_snapshot_id,idempotency_key}` |
| GET `/social/media/{capability}` | Public, expiring, purpose-bound capability for exact saved JPEG |
| GET `/social/webhook` | Meta verification-token challenge |
| POST `/social/webhook` | Bounded raw body with valid app-secret HMAC; no bearer identity |
| GET/POST `/community-reviews/{id}/reply-dispatches` | Inspect requests / OPERATOR prepares exact outbound reply with `{idempotency_key}` |
| GET `/social-reply-runs/{id}` | Exact outbound text, target comment and job history |
| POST `/social-reply-runs/{id}/review` | APPROVER authorizes or rejects exact text hash |
| POST `/social-reply-runs/{id}/execute` | OPERATOR sends an already authorized reply; no automatic trigger |

Publication review body:

```json
{
  "plan_hash": "<exact displayed SHA-256>",
  "decision": "AUTHORIZE_PUBLISH",
  "reviewed_images": true,
  "reviewed_caption": true,
  "confirmed_account": true,
  "comment": null
}
```

Reply review uses `{decision:"AUTHORIZE_REPLY",text_hash:"<exact SHA-256>",comment:null}`. Both support `REJECT`. The UI leaves review boxes unchecked, verifies downloaded JPEG hashes and requires every image to decode before enabling publication authorization. A separate operator confirmation invokes the public action. UI disabling supplements database guards; it is not the security boundary.

## Persistence, tenancy and safety

Migration 0010 establishes OAuth state, versioned social connections, private account ownership and encrypted credentials, revocations, publication plans, exact decisions, immutable reconciliation requests, persisted jobs, insight observations and webhook events. Migration 0011 links authenticated platform comments to existing community reviews and adds protected exact-text reply runs/decisions/jobs. Migration 0012 persists descriptive comparisons of verified post observations. New tenant tables follow forced RLS and composite ownership references; the restricted runtime writes through guarded functions. Existing source provenance, content QA, immutable revisions and protected content/visual approvals remain authoritative.

OAuth state binds the tenant, influencer and initiating administrator, expires and is consumed once. A successful requested scope is not presumed granted: returned permissions and an observed professional profile must support the action. Connections are versioned; revoked, expired or superseded connections cannot authorize new dispatches. Tokens are encrypted in the private schema and never returned by list APIs. Local revocation blocks this application; it does not claim to revoke Meta's remote grant. The account owner can also remove app access on Instagram.

Publication requires current approved content and the latest approved render, then a separate reviewed JPEG/caption/account plan. Re-encoding PNGs as JPEGs produces a new reviewed plan with source/result hashes. The final publish guard holds content/account locks during the bounded provider call. Single-image and parent carousel requests also declare `is_ai_generated=true`; visible AI disclosure remains required. Old approval never transfers to changed content or a changed destination.

Runs checkpoint child containers, parent containers, status polls and final publish separately. Jobs carry bounded attempts, input hashes, SkillRun records and provider cost events; unreported platform cost is null, not fabricated zero. The manual dispatcher does not poll indefinitely or schedule future posts. Safe reads have bounded persisted retry/backoff. Mutation timeouts or missing acknowledgements enter `UNKNOWN_OUTCOME`; no blind POST replay is allowed.

For uncertain final publication, an APPROVER must inspect Instagram and submit `candidate_media_id`, `confirm_external_match:true` and a nonblank evidence comment. A persisted immutable reconciliation request records that authorization. The service checks the candidate's account, exact caption, media type and publication time before recording its exact ID. Similarity does not trigger automatic reconciliation. An unknown child/container creation requires administrative investigation; the post-ID reconciliation endpoint is not a generic retry/reset button.

After reconnecting, historical insights and reconciliation use a reserved current credential
for the same tenant/account/influencer and original API version. The selected connection is
pinned on the read job and checked again before the request. Historical publication approval
does not authorize a new dispatch. An approver can reject an undispatched stale plan (including
AUTHORIZED) only when no provider job exists; the old authorization remains immutable history.

Comments received before an uncertain post is confirmed remain saved. Confirmation retries
local linkage in batches of at most 25 eligible events, with an immutable link per completed
event. The console's "Recover saved comments" action calls
`POST /v1/social-publishes/{id}/comments/relink` as OPERATOR to resume a partial batch without
contacting Instagram or sending replies. Continue while `has_more` is true. Malformed or
unrelated observations remain preserved and cannot block eligible events behind them.

Insight snapshots retain API version, capture time, redacted raw response/hash and normalized definitions. Missing counters stay null. The provider calls its saves counter `saved`; platform comparisons preserve that exact metric key. Real observations do not upgrade old MANUAL/FIXTURE metric subjects or prove causal impact.

Platform learning compares two immutable snapshots from the same post/account/workflow, with identical definitions/key sets and pinned API version, in strictly increasing observation time. It preserves negative deltas and unknowns. The output is `DESCRIPTIVE` or `INSUFFICIENT_DATA`, explicitly `causal_claim=false`, `policy_updated=false`, `network_performed=false`. The console shows each baseline/current/delta/direction and saved limitations. Creating a comparison does not call Meta or modify editorial policy.

Signed webhook comments are bound to the owned account and a confirmed published media ID before becoming `PLATFORM` community events. MANUAL/FIXTURE modes cannot be promoted by a schema field or internal approval. Existing community review and QA are required, followed by a second outbound text/target authorization. Comment receipt does not imply consent, eligibility, subscription or business-audit interest. Neither the webhook nor draft approval sends a reply.

## Access-log and reverse-proxy rules

OAuth callbacks carry one-use authorization codes and signed state; webhook verification carries a token; public image paths carry short-lived capabilities. Treat all three as credentials. Do not log complete incoming URLs, query strings, request headers, response bodies, authorization redirects or callback HTML. The application routes `uvicorn.access` and `gunicorn.access` through the safe JSON formatter: only allowlisted method/status plus existing structured correlation fields survive. Dependency HTTP diagnostics omit sensitive URLs and headers. Do not replace these handlers with a formatter that interpolates request targets. The provided Uvicorn launchers disable access logs as an additional precaution.

A reverse proxy, tunnel, load balancer or hosted request inspector can log secrets before the request reaches Python. Configure it independently. For example, an Nginx access format can retain `$request_method $status $request_time` and omit `$request`, `$request_uri`, `$args`, `$uri`, headers and referers entirely. Omitting only query strings is insufficient because media capabilities are in the path. Disable body capture and protect any operational logs. Verify these settings before using real OAuth credentials; no real-code smoke test was performed here.

The public route serves only an exact immutable image under an expiring capability and rechecks stored authorization. Do not mount `.local/social` as a static directory. Console previews use authenticated internal fetches and temporary browser object URLs; operator bearer credentials never appear in preview URLs.

## Acceptance still required for live use

1. Configure the private dependencies and explicitly enable account connection. An ADMIN follows the generated Instagram authorization link and confirms the intended professional account.
2. Inspect actual returned permissions/profile/expiry. Missing or incompatible fields must block; do not weaken validation to make a demo pass.
3. Prepare content and visual approvals using real evidence, then inspect every saved JPEG, caption and account in the console.
4. An APPROVER separately authorizes that plan. Enable manual publishing only for the controlled test; an OPERATOR explicitly confirms the public post.
5. Demonstrate persisted container/publish checkpoints, exact external media receipt, replay-safe status recovery, and a real post insight observation.
6. Configure the real comment webhook subscription and demonstrate a signed inbound event linked to that owned post.
7. Review a sourced reply, authorize exact outbound text separately and explicitly send the approved reply. Demonstrate that unapproved, fixture, stale or unknown-outcome requests cannot send.
8. Restore disabled flags when controlled testing ends, according to the owner's operating preference.

Live Meta login, posting, metrics and replies have not been exercised by the normal offline suite. Public HTTPS deployment, app permissions, account access, production rate limits and operational monitoring remain external acceptance gates. No milestone should be represented as live-complete from mock transport results alone.
