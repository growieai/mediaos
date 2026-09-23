# Remaining-milestone implementation report

Branch: `milestone-completion`, based on `5bd3ab5` (local video concept).
Updated: 2026-09-23. Database migration head: `0017`.

## September 23 follow-up

The user approved publishing `milestone-completion`. Commit `42773ea` is now public and
[hosted verification passed](https://github.com/growieai/mediaos/actions/runs/35879186753), including
clean migrations, backend checks/tests, frontend build, Docker builds/startup and persisted API
acceptance. This closes the former hosted verification gap for that commit.

The follow-up adds guarded signed handoff delivery (0016), reviewed business identity evidence,
recipient receipt/revocation reconciliation, the exact handoff console, administrator voice/price/
budget forms and a standalone Linux deployment scaffold with secret-safe operational probes.
Migration 0017 adds a 30-day media rate-review requirement and sanitized failed-provider request IDs.
`httpcore==1.0.9`, already hash-locked transitively, is now an explicit dependency for bounded socket
deadlines. No provider subscription, credentials, budget or actual external transmission was added.

New API/model/migration files are in `backend/app/conversion`, `0016_conversion_delivery*` and
`0017_media_rates*`. New console files are `HandoffReview.tsx`, `MediaSetup.tsx` and offline event
tests. Deployment files are `deploy/*`, `scripts/deployment.py` and
[the release runbook](STANDALONE_DEPLOYMENT.md). Architecture, data model, workflows, security,
local setup, CI and provider documentation describe the new boundaries.

Follow-up verification is recorded separately from the earlier baseline below. The initial
PostgreSQL integration slice passed 148 tests after rebuilding the disposable database through
0017. An actual Codex review identified an active-handoff reconciliation race and two console
recovery issues; all three received regression fixes. Final post-fix results are recorded with
the release verification below.

| September 23 follow-up check | Actual result |
| --- | --- |
| PostgreSQL integration and migrations | Initial 148-case consent/handoff/media slice passed with clean migrations through 0017. After review fixes, all 25 handoff integration cases passed again, including active API/SQL reconciliation races and interruption cleanup. |
| Pure transport/media/operations checks | 33 handoff transport cases, 17 failure-metadata cases and 49 deployment-tool cases passed. One POSIX-only operations case is reserved for Linux CI. |
| Console behavior | 32 tests passed, including actual parent/child StrictMode cancellation ordering and rejection replacement recovery. TypeScript passed. |
| Backend quality | Ruff lint and formatting passed across 166 files; mypy passed across all 103 application source files. |
| Ingress configuration | Caddy 2.11.4 validated with the checksum-verified official Windows binary; no server was started or certificate requested. |
| Review | Actual Codex CLI uncommitted review completed; its three material findings were fixed with regressions. Independent handoff/SQL/CI review found no remaining material issue. |
| Secret scan | 281 source files scanned with no configured secret values or private-key headers found. |

Hosted CI must verify the exact follow-up commit before this release is described as CI-passing.

M9's outbound implementation is available for a recipient implementing the documented
[signed handoff protocol](CONVERSION_DELIVERY.md). A receipt proves the recipient acknowledged
the exact request; it never proves a business audit or conversion completed. Local physical
retention/purge remains unimplemented. A chosen destination and real consent/handoff acceptance
are still required. The deployment scaffold has not provisioned a host or demonstrated production
TLS, off-site recovery, organizational identity, multi-host workers or durable object storage.

**The delivered foundations are available for internal testing. All milestones are not yet
live-complete.** This branch adds persisted speaking video, consent-aware manual handoff,
optional real creator selection, connected Instagram publishing, platform observations and
separately authorized comment replies. The completed local verification is recorded below.
No real provider-generated speaking video, Instagram post/reply or production deployment has
been demonstrated by this branch. Mock text mode remains the default; live media and social
actions remain disabled until explicitly configured.

## Delivered scope

| Capability | Implemented behavior | Completion boundary |
| --- | --- | --- |
| Existing source-to-content loop | Persisted official-source evidence, research, brief, carousel, deterministic QA and exact human approval; inherited rendering and private export | Earlier BDNS/BOE live demonstrations remain recorded in the M2 report. Cámara live discovery is still disabled pending authorized access. |
| Real text selection | Opt-in OpenAI Responses `CreatorPlan`, choosing only verified fact IDs and approved creative-template IDs; deterministic assembly and original QA/approval | Only `content.carousel` uses the model. Extraction, briefing, verification and relevance remain deterministic. No real OpenAI call has been demonstrated. |
| M6 Instagram | Professional-account Instagram Login, encrypted token lifecycle, exact JPEG/caption/account review, explicit dispatch, persisted checkpoints and uncertain-post reconciliation | Offline implementation and tests; a connected account and controlled real post remain required. No automatic publisher. |
| M7 platform metrics | Immutable connector observations and descriptive comparisons of two exact same-post snapshots, alongside historical MANUAL/FIXTURE metrics | No actual post insights have been observed. Missing counters stay unknown; comparisons establish no causality or editorial policy changes. |
| M8 community | Signed webhook comment bridge, existing evidence-backed reply review, separate exact outbound authorization and explicit reply dispatch | A real signed inbound comment and separately authorized real reply remain unverified. No automatic reply scheduler or DMs. |
| M9 conversion foundation | Versioned consent requests, reviewed business identities, exact manual export plus separately authorized signed external handoff/revocation | Manual exports retain their no-delivery flags. The separate transport requires a provisioned recipient and real acceptance; local physical retention/purge and business audit processing are not implemented. |
| M10 speaking-video foundation | ElevenLabs aligned speech, HeyGen photo presenter, local captions/disclosure/sourced fact-card cutaways, structural QA and separate human audiovisual review | Actual generation, voice/likeness/lip-sync quality and human review remain pending. No video publishing. Higgsfield has a transport adapter only. |
| Local recovery operations | Bounded standalone backup/restore tool covering database and private render/media/social files; actual isolated restore and read-only restored API smoke checks succeeded | Production recovery operations remain separate gates. |

Sofía's Sara Martin voice choice is recorded in `characters/sofia/voice_selection.md`; the
user-supplied voice ID still needs authenticated account-access and pricing verification.
The proposed portrait still needs the user's final appearance review. Generic workflow code
loads persisted tenant, influencer, mission and media configuration.

## Files changed

| Files | Responsibility |
| --- | --- |
| `backend/app/media_providers/*`, `backend/app/media/*` | Typed ElevenLabs/HeyGen/Higgsfield transports; persisted media jobs, private composition, manifests, review and APIs |
| `backend/app/conversion/*` | Explicit consent, immutable revisions, business evidence, attestation, manual export, separately authorized signed handoff and receipt recovery |
| `backend/app/ai/*`, `backend/app/services/workflows.py` | Strict creator selection, tenant model policies, spend reservations, usage recording and checkpoint recovery |
| `backend/app/social_provider/*`, `backend/app/social/*` | Instagram Login transport, OAuth/vault, publication/reconciliation, verified observations, signed comments and approved replies |
| `backend/migrations/versions/0007*` through `0017*` | Tenant tables, composite lineage, forced RLS, guarded state changes, cooldowns, recovery locking, signed handoff and media rate freshness |
| `backend/app/config.py`, `main.py`, `db/repository.py`, `api/routes.py`, `seed.py`, `observability.py` | Feature flags, routers, readiness, service principals, private storage and secret-safe logs |
| `apps/console/app/MediaPanel.tsx`, `ConversionPanel.tsx`, `SocialPanel.tsx`, `SocialReplyPanel.tsx`, `CommunityReview.tsx`, `page.tsx`, internal proxy | Minimal review/dispatch/dependency views; authenticated bounded MP4/JPEG transport; interrupted-operation recovery controls |
| `backend/tests/test_media_*`, `test_conversion_*`, text-AI/social tests, access-log and backup tests | Typed contracts, negative SQL/API checks, concurrency, recovery, stale lineage, privacy and offline transport coverage |
| `scripts/backup.py`, `scripts/deployment.py`, `deploy/*`, `.env.example`, Docker files, `scripts/dev.py`, CI workflow | Standalone recovery and pilot deployment, safe opt-in defaults, FFmpeg, private mounts and verification commands |
| README and architecture/data/workflow/security/local/provider/testing guides | Scope, operation, invariants and live acceptance dependencies |

## Schema and migrations

The frozen M0/M1 foundation is retained. The complete migration chain is `0001` through `0017`.

| Migration | Added schema or invariant |
| --- | --- |
| `0007_media` | `media_profiles`, `media_spend_policies`, `media_runs`, `media_jobs`, `media_approval_records` |
| `0008_conversion` | `conversion_destinations`, `conversion_requests`, `conversion_attestations`, `conversion_revocations`, `conversion_exports` |
| `0009_text_ai` | `text_ai_policies`, `text_ai_attempts`; guarded paid attempts and exact artifact/usage commits |
| `0010_social` | `social_oauth_states`, `social_connections`, `social_revocations`, `social_publish_runs`, `social_publish_decisions`, `social_publish_jobs`, `social_reconcile_requests`, `social_insight_snapshots`, `social_webhook_events`; private account ownership and encrypted credentials |
| `0011_social_community` | `social_comment_links`, `social_reply_runs`, `social_reply_decisions`, `social_reply_jobs`; separate platform provenance and outbound authorization |
| `0012_social_learning` | Immutable `social_learning_reports` with exact snapshot/post/account lineage and descriptive-only flags |
| `0013_social_recovery` | Private account-wide cooldowns, consistent failure classifications and workflow-first locking for failure, receipt completion and reconciliation |
| `0014_social_comments` | Stable account identity across reconnects, historical publication/comment lineage, account/comment duplicate-dispatch protection and fresh connection-specific reply authorization |
| `0015_social_reconnect` | Pinned compatible credentials for historical read jobs and exact rejection of never-dispatched stale plans, retaining previous authorization history |
| `0016_conversion_delivery` | Business identity/review, immutable destination transports, exact handoff decisions/attempts and independently verified signed receipts; forced tenant RLS and private verification keys |
| `0017_media_rates` | Database-enforced price-review age before paid reservations and immutable bounded failure request IDs; free checkpoint recovery preserved |

New tenant records use forced PostgreSQL RLS and tenant-composite ownership constraints. The
restricted runtime has read access to public business tables and guarded function execution,
with no unrestricted protected-state writes. Private credentials/account/cooldown tables are
not directly readable by the runtime. Human OPERATOR/APPROVER/ADMIN roles remain separate from
the server-only SOCIAL capability; migration/admin credentials are not used by requests.

## State, approval and evidence

The existing content loop still persists each step through QA and `AWAITING_APPROVAL` before
an exact human approval can yield `APPROVED`. A QA BLOCK, unsupported claim, stale research,
changed asset or foreign-tenant reference cannot gain approval through the new integrations.

Speaking video follows `CREATED → SPEECH_READY → IMAGE_READY → ASSETS_READY → AVATAR_PENDING →
AVATAR_READY → AWAITING_APPROVAL → APPROVED/REJECTED`, with distinct `BLOCKED`, `FAILED` and
`UNKNOWN_OUTCOME` states. SQL derives narration from exact approved text selections. Captions
follow the supplied alignment; factual cutaways copy approved FACT text and evidence IDs.
Structural checks verify format, audio, hashes, captions and disclosure. Human review separately
confirms identity, voice, lip sync, captions and disclosure against the exact manifest. Download
rehashes/probes stored files and rechecks current source/content/profile/media lineage.

Instagram follows `AWAITING_PUBLISH_APPROVAL → AUTHORIZED → PREPARING → READY → PUBLISHING →
PUBLISHED`, with separate rejection, block, failure and uncertain-outcome states. A reviewed
JPEG/caption/account plan needs its own immutable authorization after content and visual
approval. The final dispatch holds existing content/source/account guards over the bounded
provider call. Neither content approval nor connecting an account sends a post. A new key or
reconnection cannot duplicate the same held/published render for that account.

Comments preserve signed webhook evidence, account/media identity and historical publication
lineage. Intake commits before linkage so a crash can resume without retaining an account lock
while acquiring a workflow lock. Internal reviewed drafts become a new outbound request:
`AWAITING_REPLY_APPROVAL → AUTHORIZED → SENDING → SENT`. Reconnection never transfers an old
outbound approval; a fresh intent requires a new exact authorization. `SENDING`, `SENT` and
`UNKNOWN_OUTCOME` holds apply to the stable account/comment across connection versions.

Platform metrics retain capture time, API version, redacted source response/hash and exact
post lineage. Learning compares matching definitions and chronologically ordered observations;
it preserves decreases and nulls, including the provider key `saved`. It never upgrades fixture
metrics, invents missing counts, asserts causality or updates strategy automatically.

Conversion consent is separately captured evidence, never inferred from a comment, draft or
approval. Exact request/destination/consent hashes bind immutable attestations. Expiry,
revocation and current revisions are checked again before every export or replay. Shared locks
serialize export with revocation/revision. General workflow history includes receipt metadata,
not the complete handoff or consent evidence. A manual export does not mean anyone received it.

## Idempotency, retries, costs and observability

Tenant-scoped keys plus independently checked canonical hashes return existing records for
identical input and reject conflicting reuse. Unique checkpoints prevent duplicate committed
artifacts. SkillRun, audit and cost/reservation records are persisted before external work.
Deterministic/mock operations retain zero cost; unreported external billing remains `null`.
Model token usage is recorded only when reported and validated. Administrator rate cards and
spend reservations are distinct from actual provider invoices; no refund is presumed.

Bounded safe retries persist attempts and backoff. Explicit rate-limit refusals can retry;
ambiguous side-effect outcomes cannot. Account-level social cooldowns survive new request keys,
exhausted attempts and reconnects, and apply to publishing, insights and replies. Workflow locks
precede account and job/parent locks, including audit foreign-key locking. Receipt recovery
records already-observed effects without replay or a new freshness requirement; it does not
authorize another side effect. The console exposes explicit interrupted-publication/reply
recovery with fresh confirmations; `UNKNOWN_OUTCOME` has no blind retry button.

Text/media reservations, bounded attempts, nullable billing, provider receipts, hashes and
latencies remain inspectable. Structured logs retain correlation, tenant, workflow, skill,
attempt and state fields. HTTP credentials and sensitive URL paths/queries are excluded from
access logs, including OAuth state/code, webhook verification tokens and signed media
capabilities. A production reverse proxy must apply the same omission policy.

## Verification record

This table records the earlier `42773ea` implementation. September 23 follow-up results are above.

| Check | Actual result as of 2026-09-21 |
| --- | --- |
| Clean PostgreSQL migrations | Passed through `0015` on the disposable test database |
| Preserved development database | Migration through `0015` and seed passed |
| Focused integration verification | Final 23 reconnect/comment-recovery regressions passed; the earlier 61-case integration slice also passed |
| Full backend unit/PostgreSQL suite | **1,161 passed, 2 skipped, 1 warning in 1,168.06 seconds** after all review fixes, including clean migrations through `0015` |
| Ruff lint/format | Passed; 154 Python files formatted |
| Mypy | Passed across 98 application source files |
| Frontend | TypeScript validation and Next.js production build passed |
| Scoped console checks | 42 offline recovery-control assertions and 8 internal-proxy checks passed |
| Final Codex review | Two actual Codex CLI reviews completed for this integration slice. Both sets of material findings are addressed; independent bounded reviews found no additional material issue. The full post-review regression suite passed |
| Actual standalone backup/restore | Passed to separate `mediaos_restore_connected_final_20260921`; 55 private files, schema `0015`, forced RLS, restricted role attributes and guarded-function owners verified; source rows were not modified |
| Restored API smoke test | Passed read-only with all external flags off and no provider keys: 38 tenant workflows readable, exact artifact/audit reads and authenticated PNG preview passed; unauthenticated and wrong-tenant requests returned 401. Evidence: `.local/restored-api-smoke-report.json` |
| Running console/API acceptance | Schema `0015` ready in mock mode; final content/render/delivery/metrics/community rehearsal passed through the console proxy. Approvals were simulated with a separate approver identity; no post or reply was sent. Evidence: `.local/community-acceptance-report.json` |
| Docker on this Windows host | Unavailable; no local Docker run claimed |
| Hosted CI for `42773ea` | Passed on September 23: https://github.com/growieai/mediaos/actions/runs/35879186753; includes Docker startup and persisted API acceptance |
| Paid model/media and live social acceptance | Not performed; offline tests make no paid/provider calls |
| Repository hygiene | Staged diff whitespace check passed; a bounded scan of259 source files found no configured secret values or private-key headers |

The final backend command, from `backend`, was
`.venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=C:/mediaos/.tmp/f15`.
The two skips are Windows symbolic-link privilege cases; hosted Linux CI must exercise them.
The warning is Starlette's deprecated AnyIO `BlockingPortal` alias. Native Windows scratch
paths must remain short; see `LOCAL_DEVELOPMENT.md` for the tested setup.

Prior content/render/delivery/metrics/community acceptance simulations remain documented in
their reports. Their dedicated approver credentials simulate a human review; they are not the
user's editorial sign-off. Synthetic FFmpeg inputs verify composition, not avatar performance.
The local restore proves the recorded backup's recovery checks, not production readiness.
See [OPERATIONS.md](OPERATIONS.md) for boundaries and remaining recovery work.

Review-driven fixes include consent-history minimization, bounded media polling, uncertain
submission holds, exact receipt recovery, unsupported portrait formats before paid calls,
OAuth/capability log redaction, explicit publication recovery controls, account-wide cooldowns,
workflow lock ordering and historical comment lineage after reconnection. The full final
run includes regression coverage for these changes. The final
recovery slice also enforces the refresh feature gate, pins compatible current credentials
for historical reads, permits rejection of never-dispatched stale plans and durably relinks
saved comments after publication confirmation. A PostgreSQL regression caught a redundant
application row lock; removing it keeps the restricted role unchanged and leaves locking to
the guarded database bridge.

## External dependencies and live acceptance

| Capability | Needed before live acceptance |
| --- | --- |
| Speaking Sofía | ElevenLabs and HeyGen credentials; authenticated access to the already-selected Sara Martin voice; verified account rate card and explicit budget/profile; actual generation and human audiovisual review |
| Optional real creator selection | OpenAI project key, supported explicit model and current priced tenant policy; deliberate opt-in from mock mode and actual usage verification |
| Instagram publishing | Professional account and Instagram app, supported API version and confirmed granted scopes, HTTPS callback/media origin, private vault configuration, exact plan review and controlled real dispatch |
| Platform metrics/learning | Confirmed real post, permitted insight responses and a later observation from the same post |
| Platform community | Configured HTTPS subscription, real signed inbound comment and separately authorized exact reply |
| Cámara | Authorized API/feed or explicit permitted access conditions; no access bypass |
| Final character appearance | Human review of the proposed portrait and actual output |
| Production deployment | Dedicated host/domain, operator network ranges, private secrets and recovery/alert destinations; actual deployment acceptance remains separate from passing hosted CI |

Instagram's exact account/app response shapes and permissions have not been live-tested;
activation may reveal contract changes needing implementation fixes. Provider credentials
alone are not evidence that quality, publishing or live acceptance passes. Setup details are in
[SPEAKING_VIDEO.md](SPEAKING_VIDEO.md), [VOICE_SELECTION.md](VOICE_SELECTION.md),
[REAL_MODEL_EXECUTION.md](REAL_MODEL_EXECUTION.md) and [SOCIAL_INTEGRATION.md](SOCIAL_INTEGRATION.md).

## Remaining implementation and deferred work

- M9's business identity evidence and signed outbound/revocation transport are implemented.
  Real recipient acceptance and local physical retention/purge remain outstanding. A business
  audit is a separate product; existing Growie production systems remain excluded.
- Real text AI currently selects facts/templates only. Model-based extraction, editor reasoning,
  semantic relevance and unrestricted model-authored copy are not supplied by this integration.
- Higgsfield's typed adapter is not wired into persisted video composition. Optional generated
  cutaways need their own lineage, spending, QA and review integration; current cutaways are
  exact sourced text cards. Video publishing is not implemented.
- Production still needs durable private object storage, coordinated/versioned backups,
  off-site encryption, PITR/WAL, monitored recurring recovery checks, identity/secret rotation,
  alerting, TLS/ingress, multi-host workers/orchestration and reconciliation/runbooks. The local
  maintenance tool and successful isolated restore do not provide these capabilities.
- Marketplace, external creator UI/signup, customer billing, DMs and automatic social actions
  remain deferred. No connection to existing Growie production services/database was introduced.

## Local commands

For a fresh standalone Docker development environment, with Python 3.12+ and Node 24:

```text
python scripts/dev.py setup
python scripts/dev.py up
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py test
python scripts/dev.py check
python scripts/dev.py dev
python scripts/dev.py community-acceptance
```

Normal tests rebuild only the explicitly configured disposable test database; do not run two
PostgreSQL test sessions concurrently. Acceptance simulations add their own records and must
retain fixture/simulation labels. Read each live acceptance command before invoking it.

The prepared Windows workspace uses standalone PostgreSQL on `127.0.0.1:55432`.
`powershell -File scripts/start-local.ps1` starts its API/console; `-Stop` stops those
workspace-owned services. The console is `http://127.0.0.1:3000`. Human credentials remain in
ignored `.local/credentials.json`; server-only ingestion/social tokens remain separate. Do not
paste secrets into chat or documentation. Startup does not enable paid generation or publishing.

Use [CONVERSION_TESTING.md](CONVERSION_TESTING.md) for manual-handoff checks and
[OPERATIONS.md](OPERATIONS.md) for the explicit quiesced backup/isolated-restore commands.
Provider keys are unnecessary for the existing mock loop, dependency inspection and offline tests.
