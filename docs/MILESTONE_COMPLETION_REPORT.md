# Remaining-milestone implementation report

Branch: `milestone-completion`, based on `5bd3ab5` (local video concept).

The product is **not yet live-complete across all milestones**. This branch implements the
remaining independent consent/handoff and speaking-video foundations. Paid generation,
social account integration and production deployment need real credentials and acceptance.

## Delivered changes

- M9: immutable consent-aware conversion requests, versioned manual-export destinations,
  exact approver attestations, revocation and guarded handoff receipts. No network delivery or
  business audit is represented as completed. See `CONVERSION_TESTING.md`.
- M10 foundation: strict ElevenLabs/HeyGen/Higgsfield transport adapters; persisted talking-video
  jobs from exact approved excerpts; versioned voice/rate profiles and spend policies; private
  audio/video, captions, sourced fact-card cutaways, disclosure, structural QA and separate human audiovisual review.
- Minimal console panels expose saved videos, dependency state, script preparation, explicit
  execution, exact preview/review/download, and historical conversion receipts.
- Existing tenant, content/evidence, current-revision and approval guards remain authoritative.
  Mock text mode and all social dispatch flags remain unchanged.

## Schema and operational behavior

Files changed are grouped by responsibility:

| Files | Change |
| --- | --- |
| `backend/app/media_providers/*` | Typed fixed-host ElevenLabs, HeyGen and Higgsfield adapters |
| `backend/app/media/*` | Persisted media execution, receipts, private composition, exact human review and APIs |
| `backend/app/conversion/*` | Consent, request revisions, review, revocation and guarded manual export |
| `backend/migrations/versions/0007_media.*`, `0008_conversion.*` | Ten new forced-RLS tables and guarded database functions |
| `backend/app/config.py`, `main.py`, `db/repository.py`, `api/routes.py`, `seed.py` | Configuration, router/table integration, schema readiness and private storage setup |
| `backend/app/services/workflows.py`, `observability.py` | Redacted provider/consent history and secret-safe HTTP diagnostics |
| `apps/console/app/MediaPanel.tsx`, `ConversionPanel.tsx`, `page.tsx`, internal proxy | One-screen media and consent review, bounded authenticated video responses |
| `backend/tests/test_media_*`, `test_conversion_*`, `test_invariants.py` | Provider, composition, SQL/API, tenant, failure, privacy and recovery regressions |
| `.env.example`, `backend/Dockerfile`, `docker-compose.yml`, `scripts/dev.py`, CI workflow | Opt-in configuration, FFmpeg, private media mounts and offline CI checks |
| README and architecture/data/workflow/security/local/media/voice/conversion guides | Setup, invariants, actual status and deferred live requirements |

Migration `0007_media` adds five tenant-owned tables: `media_profiles`, `media_spend_policies`,
`media_runs`, `media_jobs`, `media_approval_records`. All use forced RLS and composite ownership
constraints. The restricted runtime can read them and call guarded functions; direct writes
are denied. External provider costs can be unknown (`null`); deterministic/mock costs stay zero.

Migration `0008_conversion` adds five tenant-owned tables: `conversion_destinations`,
`conversion_requests`, `conversion_attestations`, `conversion_revocations`, `conversion_exports`.
It provides explicit consent lineage, revisions, expiry/revocation and manual export protection.

Each media job commits its SkillRun/cost reservation before provider work. Bounded attempts,
saved cooldowns, receipt recovery and unknown-outcome holds prevent blind paid replay.
Source-backed narration is derived in SQL; captions reproduce exact aligned narration.
Human approval pins the exact manifest and parent asset/research/QA/content-approval revisions.
Downloads rehash and probe files. New source/content/profile/media revisions invalidate access
according to policy. No API takes an arbitrary remote output URL from an operator.

## Verification

- Full backend suite, including existing milestone regressions and PostgreSQL integration:
  **811 passed, 1 skipped, 1 warning in 626.22 seconds** on 2026-09-21. Command from `backend`:
  `.venv/Scripts/python.exe -m pytest -q --tb=short --basetemp=../.local/pytest-completion-reviewed-final`.
  The skip is a symbolic-link test because this Windows account cannot create symlinks; hosted
  Linux CI must exercise it. The warning is Starlette's deprecated AnyIO `BlockingPortal` alias.
- Empty-database Alembic migration chain through `0008` and seed are exercised by the
  PostgreSQL suite against disposable `mediaos_test`; the preserved local development database
  was also upgraded and seeded.
- Ruff lint and format checks pass; 111 Python files are formatted. Mypy passes across 80
  application source files.
- Next.js production build and TypeScript validation pass. Six isolated internal-proxy checks
  pass, including authentication and bounded binary responses.
- The restarted localhost API/console report schema `0008` ready. Authenticated media and
  conversion reads pass through the console proxy; unauthenticated requests return 401.
  Paid generation is off, provider credentials are absent, and text remains in mock mode.
- The persisted content/render/delivery/metrics/community acceptance rehearsal passes through
  the console proxy. Approvals in that rehearsal are explicitly simulated with separate
  approver credentials; social delivery is a dry run and no message or post is sent.
- Actual FFmpeg composition tests verify captions, permanent disclosure and exact sourced
  fact-card cutaways. Synthetic media proves composition behavior, not real avatar quality.
- Two Codex CLI `review --uncommitted` passes and independent bounded reviews were completed.
  Material findings were fixed: repeated-poll uniqueness, consent-history data leakage,
  transient media-download recovery, uncertain accepted submissions, and unsupported portrait
  formats before paid calls. Regression tests also cover atomic receipts, database checkpoint
  interruption, superseded media, and secret-safe HTTP diagnostics. The final independent
  review reported no new material findings.
- `git diff --check` passes. A tracked/unignored source scan found no private-key or provider-token
  patterns; this is a bounded hygiene check, not a comprehensive security audit.

Docker is unavailable on this host. Docker execution and hosted CI for this branch remain
unverified. No real provider output, live account post or production deployment is implied by
local tests. Recorded HTTP fixtures never contact paid providers.

## Dependencies and deferred acceptance

| Capability | Remaining acceptance dependency |
| --- | --- |
| Speaking Sofía video | ElevenLabs/HeyGen keys, selected authorized voice, verified account prices, explicit budget, actual provider generation and human review |
| Higgsfield cutaways | API credentials/budget and integration into persisted exact media lineage; current transport adapter alone is not a completed cutaway workflow |
| Real text AI | User still chose mock mode; OpenAI credentials and persisted real-call integration/usage verification are required |
| Instagram publishing | Connected account, required permissions, token lifecycle, public HTTPS media delivery and explicit live dispatch authorization |
| Verified metrics | An actual connected post and provider insight responses, then a later observation |
| Live community | Verified inbound webhooks/account mapping and separately authorized reply dispatch |
| Conversion delivery | Actual consenting subject, chosen authorized destination and confirmed handoff receipt; no existing Growie production connection |
| Cámara ingestion | Authorized access to the electronic-office source; no bypass or automated crawl claimed |
| Production operations | Standalone hosting/domain/TLS, private durable storage, identity/token lifecycle, backup restore, alerting, worker reconciliation and deployment acceptance |
| Hosted CI for this branch | Publish the concrete committed branch after public-publication authorization |

External creators, marketplace and customer billing remain deferred. This report does not
relabel partial milestones as complete. See `SPEAKING_VIDEO.md` for provider contracts and
the exact local setup/test flow.

## Commands to run locally

From the repository root, using Python 3.12+, Node 24 and Docker Compose:

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

On the already prepared Windows workspace, standalone PostgreSQL is already running on 55432.
Use `powershell -File scripts/start-local.ps1` to start the prepared API/console; add `-Stop` to
stop only those workspace-owned services. The console is `http://127.0.0.1:3000`. Credentials
remain in ignored `.local/credentials.json`, not in this report. No provider keys are required
to inspect dependency state or test the mock content workflow.

Read [the voice-selection guide](VOICE_SELECTION.md) before configuring a voice ID. No paid
generation or publication is activated by these startup commands.
