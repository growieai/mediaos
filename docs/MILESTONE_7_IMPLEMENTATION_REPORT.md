# Milestone 7 metrics foundation implementation report

Branch: `milestone-7-metrics-foundation`, following delivery preflight commit `a45c29a`.
Implementation commit: `8f24688701feeb46bf458c698701a498a7e28ac5`, published with the user's explicit permission.

**Scope: partial M7, manual/fixture metrics foundation.** This slice records observations against
an exact historical carousel and computes descriptive differences. It does not connect Instagram,
fetch platform insights, prove that a post exists or complete the live metrics loop. AI remains in
the user's selected mock mode. No existing Growie production service or database was contacted.

## Implemented behavior

An authenticated operator registers a metric subject for a historically content-approved and
visually approved render. MANUAL subjects require an asserted external reference and are permanently
labelled SELF_REPORTED. FIXTURE subjects represent synthetic testing and have no external reference.
Neither mode becomes platform-verified because its schema is valid.

Each imported observation retains its typed original payload, supplied evidence text, metric
definitions, canonical content hash and UTC observation time. Reach, saves, shares, comments and
follows must each be explicit nonnegative integers or null. Zero remains a reported zero; null
remains Unknown. Counts cannot exceed 9007199254740991. Future observations and malformed payloads
are rejected. Evidence text is retained as an assertion, without automatic verification or fetching.

A comparison uses two observations of the same subject with matching scope and definitions and
strictly increasing observation times. The current scope is LIFETIME_CUMULATIVE. SQL subtracts
comparable counters and preserves the exact snapshot IDs and hashes. A missing value produces an
unknown delta. If no counter is comparable, the report is INSUFFICIENT_DATA. Negative deltas remain
visible as reported decreases that may reflect corrections; they do not prove audience loss.

Reports declare DESCRIPTIVE_ONLY and NO_CAUSAL_INFERENCE, with `causal_claim=false` and
`policy_updated=false`. There is no cross-post ranking, attribution model, automatic editorial
weight change or model-generated advice. The service independently validates the SQL-produced
typed report against the saved inputs before committing it.

## Schema, approval lineage and tenant isolation

Migration **0005** follows unchanged migrations 0001–0004 and adds:

| Table | Stored boundary |
| --- | --- |
| `metric_subjects` | Exact workflow, render/manifest, content/research/QA revisions and historical content/visual approvals; permanent provenance mode, creator and idempotency. |
| `metric_snapshots` | Immutable observation payload, raw supplied evidence, counter values, definitions, observation/receipt times and canonical hash. |
| `learning_reports` | Same-subject baseline/current snapshot IDs and hashes, deterministic algorithm version, result/limitations, SkillRun linkage and canonical report hash. |

All three tables force tenant RLS and use composite ownership and lineage foreign keys. Runtime
receives SELECT only. Guarded functions `register_metric_subject`, `import_metric_snapshot` and
`build_metric_learning` require authenticated OPERATOR permission and enforce protected writes.
Subjects, observations and reports cannot be updated or deleted through runtime SQL. The database
copies approval and provenance fields instead of trusting caller-supplied approval identities.

Historical approval establishes which content was measured. Registration deliberately does not
call the current export guard: a newer asset revision or expired source does not erase historical
measurements. This does not permit another export or post. Existing approval, source freshness,
revision and export invariants remain authoritative and unchanged. Metrics do not alter WorkflowRun.

## Idempotency, failure behavior and telemetry

Each operation has a tenant-scoped key and canonical request hash. The same key/input returns the
same saved row; a changed request conflicts. Separate tenants can use the same key. Database
uniqueness and transaction locks prevent duplicate committed work under concurrent requests.

Learning is one bounded SQL transaction. The report, `learning.describe` SkillRun, zero-cost
CostEvent and audit event commit together. The skill records provider=deterministic, model=none,
adapter=descriptive-v1, one attempt, zero tokens and zero cost. A validation rejection or transaction
failure rolls back the operation, including any provisional attempt. No persisted failed-attempt
history is fabricated. There is no long-running model or external network side effect in this step.

After an operational database interruption or uncertain response, retry the identical request/key.
It returns the committed result or performs the uncommitted operation. There is no background retry
scheduler. New observations are required for corrected input; saved snapshots remain immutable.

## API, console and security

- `POST/GET /v1/workflow-runs/{id}/metric-subjects`
- `GET /v1/metric-subjects/{id}`
- `POST /v1/metric-subjects/{id}/snapshots`
- `POST/GET /v1/metric-subjects/{id}/learning-reports`

The existing one-screen visual review exposes **Reported metrics and descriptive comparisons**.
It shows the permanent provenance label, historical render association, saved observation values,
Unknown values, exact report evidence and limitations. Operators can import manual/fixture data and
choose two snapshots for comparison. It does not display synthetic values as real account metrics.

Tenant identity and permissions come from authenticated request context. References and evidence
are bounded data, never fetched URLs or instructions. The console renders text safely, secrets stay
server-side or in the user's ignored local credential file, and the existing request-size/logging
controls apply. No account credentials are required for this foundation. No new dependency was added.

## Files changed

- `backend/migrations/versions/0005_metrics.py` and `.sql`: schema, guarded writes, relational lineage,
  idempotency, immutable comparisons and atomic telemetry.
- `backend/app/metrics/`: strict contracts, validated service, authenticated routes and fixture
  acceptance wrapper.
- `backend/tests/test_metrics_unit.py` and `test_metrics_integration.py`: schemas, descriptive
  comparisons, provenance, auth/RLS, concurrency, immutability, malformed direct SQL and rollback.
- `backend/app/main.py`, repository table discovery, readiness and migration-head test: integration.
- `apps/console/app/MetricsReview.tsx` and `VisualReview.tsx`: compact historical metrics controls.
- `backend/app/rendering/acceptance.py`, `scripts/dev.py` and `.github/workflows/ci.yml`: repeatable
  metrics acceptance alongside existing foundation, rendering and dry-run delivery checks.
- README, architecture/data/workflow/security/local-development/roadmap/testing documents,
  `METRICS_TESTING.md` and this report: operating steps, evidence and remaining live dependencies.

## Verification status

This section records observed checks without treating pending runs as successful.

| Check | Current evidence |
| --- | --- |
| Development migration and seed | Passed through 0005 on the standalone development database. |
| Metrics pure tests | 34 passed. |
| Metrics integration coverage | All 40 passed in the full suite, including SQL timestamp/hash regression. |
| Full backend suite / clean test database | 308 passed in 281.03 seconds; migration 0001 through 0005 rebuilt the disposable database from an empty schema. |
| Backend lint, formatting and types | Ruff passed; 72 files formatted; mypy passed for 55 source files. |
| Frontend production build | Passed. |
| Initial in-process persisted acceptance | Passed at `2026-09-21T06:19:35Z`; final post-fix acceptance also passed through the running console. |
| Final console-proxy acceptance | Passed at `2026-09-21T06:24:37Z` with persisted approval, export, dry-run and fixture metrics. |
| Final Codex review | No actionable regressions; independently reran 34 pure metrics cases, targeted Ruff and TypeScript checks. |
| Hosted CI for M7 | [Run 35568720488](https://github.com/growieai/mediaos/actions/runs/35568720488) passed for `8f24688`, completed `2026-09-21T06:33:20Z`. |

Codex review found a P2 response-integrity issue: schema serialization could normalize an equivalent
UTC timestamp representation while returning the original payload hash. The service now validates
the typed payload without rewriting its stored JSON representation. A regression covers preserving
the original payload/hash pair. The full suite, backend quality checks, frontend build and final
console-proxy acceptance passed after that fix. Final `codex review --uncommitted` found no actionable
regressions. The read-only reviewer did not run PostgreSQL tests; those were run separately by the
implementation session as recorded above.

The only test warning is the inherited Starlette/AnyIO deprecation. Early rerun attempts encountered
Windows sandbox temporary-directory permissions; the completed run used a workspace-local temporary
directory with approved access. The first integration pass also caught two assertions expecting 409
where ownership-scoped lookups intentionally return 404; the tests now require that exact 404 and
verify no record was saved. No tenant or approval guard was weakened.

The M7 hosted run verified empty-database migrations, backend quality checks, all 308 tests, seed,
acceptance simulations, frontend build, Docker configuration/builds/startup and persisted API and
console-proxy acceptance. M6's earlier hosted CI separately passed through `a45c29a`.
The metrics acceptance harness uses
explicit FIXTURE counters, simulated approval identities and no social-platform call. Its report is
saved to `.local/metrics-acceptance-report.json`; use that file's IDs after each successful rerun.

The post-fix console-proxy acceptance created workflow `2e17b4e0-6886-4920-a7fd-a0dd51272499`,
historically approved render `5d75f41c-951e-4ec8-9fe7-2bb5dd2aafe8`, metric subject
`02eef2aa-ba64-4974-9ee4-fffa42bd32b2` and report `b0228ffd-c23a-48fc-b4a5-5cb2a73f46ef`.
Its 35-event audit includes the metrics operations. Fresh render
`c14de755-8197-43ec-bb9d-958703a141ce` remains awaiting the user's own review after the controlled
revision. The older simulated approvals are historical evidence, not the user's approval.

## Local commands and remaining dependencies

With the standalone Media OS database running, from the repository root:

```bash
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py test
python scripts/dev.py check
python scripts/dev.py dev
python scripts/dev.py metrics-acceptance
```

For the prepared Windows native installation, rebuild and restart with `scripts/start-local.ps1`
as described in [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md). The console remains at
http://127.0.0.1:3000/. [METRICS_TESTING.md](METRICS_TESTING.md) contains manual review and actual
console-proxy acceptance commands. Tests rebuild only `mediaos_test`; acceptance adds records to
the development database without resetting it. Keep `.local/credentials.json` private.

Live M7 requires a real post, authorized account/insights access, confirmed metric definitions and
a connector preserving the original platform response. Existing MANUAL/FIXTURE records will retain
their original provenance when that connector is introduced. Instagram stays disconnected, and paid
model calls remain disabled until configured and separately verified. Cámara still needs approved
access, and Sofía's proposed appearance still needs the user's review.

Community automation, conversion handoff and reels are not completed by metrics comparisons. Reels
remain gated on a stable carousel and measured performance loop. Production also needs durable
private object storage, identity/token lifecycle, secrets/TLS, backup/restore, monitoring and safe
multi-host orchestration. Existing Growie production infrastructure remains outside this setup.
