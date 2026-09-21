# Delivery preflight implementation report

Branch: `milestone-6-delivery-preflight`, based on M3 commit `8827816`.

This implements the internal dry-run portion of the original Milestone 6. It is not completed
Instagram publishing. No social account, OAuth flow, external post, paid model call or production
Growie service was used. The application remains in the user's selected mock mode.

## Behavior and schema

An authenticated operator selects a current, content-approved and visually approved render. The
service persists the delivery request before execution, validates its exact approved PNG package
and caption, and saves a typed no-network receipt. The console displays saved runs, failures,
backoff and receipts, with explicit DRY RUN wording. A successful historical receipt does not
authorize a future post or certify continuing source freshness.

Migration **0004** adds `delivery_targets` and `delivery_runs`, without modifying migrations 0001–0003.
Target configurations have immutable consecutive revisions; a new version can disable a target.
Runs pin the tenant, workflow, target version, render, manifest hash, exact asset/research/QA and
both content and visual approval records. Composite foreign keys enforce ownership and lineage.
Both tables force RLS. Runtime receives SELECT only; guarded functions own protected writes.

`start_delivery`, `claim_delivery`, `complete_delivery` and `fail_delivery` enforce authenticated
operator permission and explicit state. Start/claim/completion reuse the existing export guard for
current approvals, revisions and source freshness. The same workflow/render/configuration/source
locks serialize these checks with upstream changes. Target version checks use a tenant/key lock,
in consistent order after export checks. Actual PNG bytes remain the service's responsibility;
PostgreSQL validates the manifest, exact caption, ordered hashes and no-network receipt.

## State, retries and idempotency

```text
CREATED → VALIDATING → DRY_RUN_COMPLETE
                    → BLOCKED
                    → FAILED
                    → RETRY_WAIT → VALIDATING
```

WorkflowRun stays APPROVED. QA or visual approval cannot be skipped by entering delivery state.
Identical tenant/key/input returns the same saved run; changed input conflicts. Different tenants
can use the same key. Database uniqueness handles concurrent creation. A per-delivery advisory
lock prevents simultaneous execution; terminal receipts and targets are immutable.

Transient local failures have at most three attempts and persisted 1s/2s backoff. Early execution
returns the waiting state. Interrupted attempts are preserved as INTERRUPTED before resuming.
Policy/freshness failures block; corrupt packages and invalid typed output fail without retry.
Uncertain database commits require reconnection and inspection of persisted state, not a blind
failure rewrite. There is no external network side effect to reconcile in this adapter.

Every invocation records `delivery.dry_run`, provider=mock, model=none, adapter=dry-run-v1,
is_mock=true, zero tokens and zero cost. CostEvents, attempt latency and audit transitions are
persisted. Logs use the existing safe field allowlist and request correlation context.

## Verification

The local full suite passed **234 tests**: all 188 inherited M0–M3 cases, 32 delivery integration
cases and 14 adapter/timezone unit cases. Migration from an empty disposable database through 0004
passed. The separate development database was migrated and seeded. Ruff lint/format passed across
64 files, mypy passed across 50 modules, and the frontend production build/typecheck passed.

In-process acceptance and the running console-proxy acceptance passed. The latter completed at
`2026-09-20T20:18:07.054137+00:00`, with 31 retrieved audit events. It verified the complete
source/research/content/render/approval flow, a saved no-network receipt, idempotent replay,
authorization denial, stale-render denial and an unsupported-claim BLOCK. Approvals were simulated
using separate local identities; no public post or actual human appearance approval is implied.

| Persisted local artifact | ID |
| --- | --- |
| Internal policy workflow | `c6ffb5af-0670-48ab-b90c-9f4200e137d4` |
| Historical approved render | `cb16422a-b80f-4c69-9c21-c799b6fe69d1` |
| Completed delivery rehearsal | `20b1e3f2-2259-46ea-90ff-d561dcd1a58e` |
| Delivery SkillRun | `09bec096-b03a-4157-8f3d-e77db5a30ac3` |
| Fresh render awaiting user content/visual approval | `5873d7df-1076-4236-870d-5c9a2220ee0f` |

The receipt records `network_performed=false`, null post/publish IDs and timestamps, exact package
and media hashes, mock provider and zero cost. Its previous approval remains historical after the
controlled content revision. Use current IDs from `.local/delivery-acceptance-report.json` if rerun.

Implementation commit: `5b567d5`. The final full rerun passed **234 tests** in 235.39 seconds,
with only the pre-existing Starlette/AnyIO deprecation warning. Lint, formatting and mypy passed.

The user explicitly approved public publication of this branch. Commit `019c3fe` is published on
`growieai/mediaos`, and [hosted run 35538971128](https://github.com/growieai/mediaos/actions/runs/35538971128)
passed, completing at `2026-09-20T21:35:08Z`. It verified clean migrations, all 234 tests,
quality checks, frontend build, Docker builds/startup and persisted console-proxy acceptance.
The earlier publication gate is resolved. This completes verification of the internal dry run;
it does not complete live Instagram integration.

M3's inherited hosted check is separately confirmed:
[run 35533385755](https://github.com/growieai/mediaos/actions/runs/35533385755) passed for `8827816`,
including Docker/console-proxy acceptance. It does not verify 0004.

The delivery tests cover exact approved package/caption hashes, no HTTP calls, permission and RLS
denial, direct SQL bypass attempts, wrong-tenant references, missing approvals, stale content and
targets, corrupt files, malformed or rehashed output, tenant/concurrent idempotency, retry limits,
backoff, restart and concurrency with upstream changes. Normal tests do not contact live sources.

Codex review ran using `codex review --uncommitted`. It reported no actionable regressions and
passed 13 isolated adapter tests. It did not execute PostgreSQL tests; the implementation session
owns those checks separately.

Integrated testing caught local PostgreSQL returning Asia/Calcutta time. Database timestamps now
normalize to UTC without changing their instant, and strict UTC receipt validation remains intact.
The regression is covered by a dedicated test. Invalid adapter serialization warnings are suppressed
before strict revalidation so malformed values cannot leak through warning logs; invalid output is
still rejected and persisted as FAILED without retry.

## Files changed

- `backend/app/delivery/`: strict contracts, local adapter, persistence service, authenticated routes,
  target seed and acceptance wrapper.
- `backend/migrations/versions/0004_delivery.py` and `.sql`: two RLS tables and guarded transitions.
- `backend/tests/test_delivery_unit.py` and `test_delivery_integration.py`: negative and concurrency coverage.
- `backend/app/main.py`, repository discovery, seed, readiness and migration-head test: integration.
- `backend/app/rendering/acceptance.py`: optional delivery checks within the real internal-policy flow.
- `apps/console/app/DeliveryPreflight.tsx` and `VisualReview.tsx`: minimal rehearsal controls and receipts.
- CI, `scripts/dev.py`, README and architecture/data/workflow/security/local-development documents:
  reproducible checks, operating instructions and clear live-integration limits.

No new runtime dependency was added. Migration and seed are required before restarting the API.
Use `python scripts/dev.py migrate`, `seed`, `test`, `check`, `dev` and `delivery-acceptance` as
described in [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md). The prepared Windows native launcher
is `scripts/start-local.ps1`. Manual review steps are in [DELIVERY_TESTING.md](DELIVERY_TESTING.md).

## Remaining dependencies

- Cámara: the user must obtain an approved API/feed or explicit access conditions using
  [CAMARA_ACCESS_REQUEST.md](CAMARA_ACCESS_REQUEST.md). No email was sent automatically.
- Sofía: the proposed portrait and actual slides still require the user's appearance review.
- Models: paid calls remain disabled until the user configures credentials; persisted real usage,
  pricing and live adapter acceptance remain necessary before runtime activation.
- Instagram: account connection, permissions, secure token storage, verified platform requirements,
  HTTPS media hosting and uncertain-outcome reconciliation remain unimplemented live requirements.
- Metrics/community/conversion: real posts/insights, inbound event permissions and a chosen
  consent-aware handoff destination are still required. No fake metrics or business audit was added.
- Reels remain gated on a stable carousel and measured metrics loop. Video, voice, marketplace,
  external creator UI/signup and billing remain disabled/deferred.
- Production: private durable object storage, multi-host operation, identity/token lifecycle,
  TLS/secrets management, backups, monitoring and durable orchestration remain operational work.
