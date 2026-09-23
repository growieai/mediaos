# Local development

The current schema head is 0017. Run migrate and seed after updating this branch, then rebuild
and restart the API/console. Seed creates `.local/media` and `.local/social` for private bind mounts; no paid
profile or budget is seeded. On this prepared Windows host the standalone PostgreSQL service
uses port 55432 and `mediaos_dev`; the Docker example uses port 55433 and database `mediaos`.
These are different standalone environments. Keep the environment URLs consistent with the
chosen one and never substitute an existing Growie production URL.

`python scripts/dev.py check` includes the offline console behavior tests. An ADMIN can configure
the selected voice, current provider prices and an explicit expiring budget in **Speaking video →
Configure voice, verified prices and budget**. Saving settings does not generate a video. Keep
provider keys in the ignored environment file. The existing selected Sara Martin voice is recorded
in `characters/sofia/voice_selection.md`; account access still needs verification.

The new [signed handoff](CONVERSION_DELIVERY.md) requires an explicitly provisioned destination,
reviewed business/consent and separate outbound authorization. Keep `CONVERSION_DELIVERY_ENABLED=false`
for ordinary local testing. Its normal tests use signed synthetic HTTP responses. The
[standalone Linux scaffold](STANDALONE_DEPLOYMENT.md) is separate from this development environment.

For the speaking-video setup, keys, voice/rate profile, budgets and manual test sequence, see
[SPEAKING_VIDEO.md](SPEAKING_VIDEO.md). Keep `MEDIA_LIVE_ENABLED=false` until paid execution is
authorized. Pure provider tests use recorded HTTP responses; media-file tests use a local
synthetic clip. They do not prove a real provider's lip sync. FFmpeg/FFprobe are needed for
media encoding; Docker installs them. Normal mock text workflows still need no AI credentials.

For opt-in real text see [real model execution](REAL_MODEL_EXECUTION.md); for connected Instagram
setup see [social integration](SOCIAL_INTEGRATION.md). Keep all external flags off for local
fixture tests. The seed writes a separate server-only `.local/social-credentials.json`; never
paste that connector credential into the console. [Operations](OPERATIONS.md) covers an isolated
backup/restore rehearsal with PostgreSQL client tools and all application writers stopped.

For M3 rendering, run migration and seed after pulling the visual-production branch. Setup/seed create
`.local/renders` as the current user; Compose refuses to auto-create that bind mount as root.
Run `python scripts/dev.py visual-acceptance` for persisted render/approval/ZIP checks. See
`VISUAL_TESTING.md` for the one-screen testing procedure. Native Windows/WSL use the same
standalone database and private render directory; no AI credentials are required in mock mode.

Use a fresh standalone database. No Growie production credentials or services are involved.

## Windows and WSL

Install Python 3.12+, Node 24+ and Docker Desktop with Compose. In PowerShell or WSL, run from the repository root:

```bash
python scripts/dev.py setup
python scripts/dev.py up
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py test
python scripts/dev.py check
python scripts/dev.py dev
python scripts/dev.py acceptance
```

On WSL use python3 if python is not configured. Make targets invoke the same commands.

Setup creates random local database passwords and installs the Python/frontend lockfiles. Existing non-placeholder .env files are preserved. PostgreSQL starts separately so migrations and seed complete before API/console use it.

Host tools use 127.0.0.1:55433. Containers receive postgres:5432 through explicit environment overrides. Console uses api:8000 inside Compose and 127.0.0.1:8000 when run on the host. This distinction is intentional.

The only required dependency for M0/M1 is PostgreSQL 18.4. Redis, object storage and Temporal are deferred until needed. Their absence does not weaken the PostgreSQL transaction/approval guarantees.

## Host application commands

```bash
cd backend
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
```

For WSL use .venv/bin/python. In another terminal:

```bash
cd apps/console
npm run dev
```

Full container maintenance is also available:
`docker compose run --rm migrate`, then `docker compose run --rm seed`, then `docker compose up -d api console`.

The seed reads the four Sofía Markdown files and runtime.json. Repeat it safely; changed character data creates a new immutable version.

## Internal console flow

1. Open .local/credentials.json locally. Enter tenant_id and the OPERATOR token in the console.
2. Submit a strict manual-source JSON request using the seeded influencer_id and mission_id.
3. Switch to the APPROVER token, inspect the source and enter an attestation comment.
4. Switch to OPERATOR and execute.
5. Inspect research, brief, carousel, QA and audit. PASS waits at AWAITING_APPROVAL.
6. Switch to APPROVER and approve or reject the exact displayed revision.

A one-fact request shape:

```json
{
  "influencer_id": "<seeded UUID>",
  "mission_id": "<seeded UUID>",
  "idempotency_key": "<unique key>",
  "source": {
    "source_type": "MANUAL",
    "origin": "internal:policy",
    "title": "Internal approval policy",
    "publisher": "Operations",
    "raw_content": "An OPERATOR cannot approve content.",
    "captured_at": "2026-09-20T00:00:00Z",
    "classification": "INTERNAL",
    "is_fixture": false,
    "evidence": [{"start": 0, "end": 35, "statement": "An OPERATOR cannot approve content."}]
  }
}
```

Use real supplied evidence for non-fixture submissions. For experiments mark is_fixture=true and expect BLOCKED. The captured timestamp must reflect your actual source capture.

## Verification and troubleshooting

Tests require TEST_DATABASE_URL and TEST_MIGRATION_DATABASE_URL, both naming mediaos_test. They rebuild only that explicitly disposable database's public/private schemas. Never point these variables at valuable data. Tests use real PostgreSQL and the restricted runtime role, not SQLite or a migration-role substitute.

Run migrations before readiness. An unknown token/tenant returns 401; missing role returns 403; invisible tenant resources return 404; invariant/idempotency/stale-revision conflicts return 409.

Python dependency refresh: uv pip compile pyproject.toml --extra dev --universal --generate-hashes -o requirements.lock, then review changes. Frontend uses npm ci with its committed lockfile.

On native Windows, keep rendering-test scratch paths short. Long pytest base paths plus nested
tenant/render/attempt UUIDs can exceed Windows path limits and surface as a render
`FileNotFoundError`. Create the parent first, then use a dedicated disposable child directory:

```powershell
New-Item -ItemType Directory -Path C:\mediaos\.tmp -Force | Out-Null
# From backend; pytest may clear this dedicated scratch directory on subsequent runs.
.venv/Scripts/python.exe -m pytest -q --basetemp=C:/mediaos/.tmp/tests
```

Do not use the repository, `.local`, private asset storage or a backup directory as `--basetemp`.

The acceptance harness writes .local/acceptance-report.json and leaves its workflows persisted for console inspection. It tests a real statement sourced from docs/SECURITY.md; approver API requests are simulated by the harness, not presented as a real person's approval.

Docker Desktop is required for container validation. Native PostgreSQL can run the same checks by overriding all four database URLs with a separate local server. The implementation session used that fallback; see the implementation report for actual test/build results.

## M2 operation

Run the existing migrate and seed commands again. Seed adds the source registry, seven Spain audience segments, a mission editorial policy and a separate INGESTOR credential. No new service dependency or AI credential is required. The API container mounts only the ingestion credential file read-only; run seed before starting it. Host PostgreSQL and container hostname rules above are unchanged.

From backend, use the platform-specific virtualenv Python:

```bash
.venv/Scripts/python.exe -m app.intelligence.cli ingest --source BDNS --query PYME
.venv/Scripts/python.exe -m app.intelligence.cli ingest --source BOE --since 2026-09-18 --until 2026-09-18 --query ""
.venv/Scripts/python.exe -m app.intelligence.cli draft
.venv/Scripts/python.exe -m app.intelligence.acceptance
```

The dates above are an explicit historical BOE example; choose the desired daily issue. A normal BDNS CLI request defaults to the last 20 days, one page, five records and 1.5-second source spacing. Supply --key to resume/replay a request. Use the one-screen console to inspect source versions, score each audience and create a sourced draft. Final approval uses the same approver identity and exact-revision controls as M1.

The opt-in live acceptance command uses current official responses and writes `.local/milestone-2-acceptance.json`. It creates an isolated verification tenant for controlled fixture changes/conflicts, preserving the real Growie approved workflow. Approver requests are a simulation, not an actual person's sign-off. Normal pytest runs use seven attributed/checksummed official fixtures and never require government websites. Fixtures remain ineligible for approval.

Internal APIs: GET intelligence/sources, GET intelligence/audiences, POST intelligence/ingestions, GET intelligence/ingestions/{id}, POST intelligence/ingestions/{id}/execute, GET intelligence/opportunities, GET intelligence/opportunities/{id}, POST intelligence/opportunities/{id}/evaluate, POST intelligence/opportunities/{id}/workflow, all under /v1. All require authenticated tenant context; writes require OPERATOR.

M0/M1 invariants and the M2 tests now pass in [hosted CI](https://github.com/growieai/mediaos/actions/runs/35529691067), including container builds, startup, migration, seed and approval acceptance through the running API. The Windows host still uses native PostgreSQL; its local launcher and prepared human-review flow are documented in TESTING.md. Live-source availability and programme windows can change; acceptance must fail rather than fabricate a qualifying opportunity.

## Visual and delivery testing

M3's [hosted verification](https://github.com/growieai/mediaos/actions/runs/35533385755) passed with
188 tests, clean migrations, frontend build, Docker startup and visual approval/export through the
console proxy. The Windows host continues to use the standalone native PostgreSQL instance.

Run migrate and seed again for migration 0004 and the local DRY_RUN target, then build/restart the
API and console. `python scripts/dev.py delivery-acceptance` exercises the content/visual approval
flow and the saved delivery rehearsal. No additional package, AI key or social account is required.
The report is `.local/delivery-acceptance-report.json`; approval requests in this harness are
simulations, and it leaves a fresh content/render revision for the user's review. See
[DELIVERY_TESTING.md](DELIVERY_TESTING.md) for manual and console-proxy instructions.

## Manual metrics foundation

Migration 0005 adds immutable metric subjects, snapshots and descriptive learning reports. No new
runtime package or external account is needed. Run migrate, seed, build and restart using the same
standalone infrastructure. `python scripts/dev.py metrics-acceptance` runs the existing approval,
render and delivery simulation followed by synthetic metric observations. Its report is
`.local/metrics-acceptance-report.json`. It never contacts a social platform, and does not label
fixture observations as real performance. [METRICS_TESTING.md](METRICS_TESTING.md) describes manual use.

## Internal reply review

Migration 0006 adds the separate community review records. Seed versions the optional community
policy; create a new workflow to use it. Existing workflows retain their original configuration.
Build/restart as usual. `python scripts/dev.py community-acceptance` composes the visual/delivery/
metrics acceptance with internal reply-review checks. Its report explicitly distinguishes internal
operator requests, synthetic controls and simulated approver calls; no external messages are sent.
No new dependency, secret or service is needed. See [COMMUNITY_TESTING.md](COMMUNITY_TESTING.md).
