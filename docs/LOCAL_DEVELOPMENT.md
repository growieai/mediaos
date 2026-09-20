# Local development

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

M0/M1 Docker and hosted-CI verification remain pending until an actual run. GitHub authentication and a configured destination remote are needed before pushing. Live-source availability and programme windows can change; acceptance must fail rather than fabricate a qualifying opportunity.
