# Growie Media OS

For the prepared M0–M2 review flow and local startup, see [Testing guide](docs/TESTING.md).

Standalone, multi-tenant internal media workflow. Growie is the first seed tenant and Sofía the first influencer. Character behavior comes from versioned database configuration.

Milestones 0/1 implement:
`SourceSnapshot → ResearchPack → ContentBrief → CAROUSEL revision → QA → AWAITING_APPROVAL → human approval → APPROVED`.

Every step, attempt, artifact and state change is persisted in PostgreSQL. M2 adds bounded official-source discovery, immutable raw snapshots, opportunity versions, verification, audience scoring and sourced drafts. Live workflows use deterministic typed skills with zero model cost; manual mock workflows remain available. There is no rendering or publishing.

Baseline `a021ac1` remains frozen. Its M0/M1 invariants now pass in the M2 integration suite, including [hosted CI and container startup/approval acceptance](https://github.com/growieai/mediaos/actions/runs/35529691067). M2 development is isolated on `milestone-2-spain-intelligence`; Cámara access permission remains outstanding.

## Quick start

Prerequisites: Python 3.12+, Node 24+, Docker with Compose.

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

Console: http://localhost:3000. API: http://localhost:8000/v1/health.
Local identity IDs and bearer credentials are generated in `.local/credentials.json`; never commit them. Use the OPERATOR identity to submit/execute and the APPROVER identity to attest source evidence and approve exact revisions.

The acceptance command simulates separate operator/approver API requests and records evidence in `.local/acceptance-report.json`. It does not represent an actual person's editorial sign-off.

- [Local development](docs/LOCAL_DEVELOPMENT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Data model](docs/DATA_MODEL.md)
- [State machine and recovery](docs/WORKFLOWS.md)
- [Security and approval invariants](docs/SECURITY.md)

Production launch requires the deferred operational work described in the architecture document. Do not point this stack at Growie's existing databases or services.

## Spain intelligence (M2)

After migration and seed, use the console's Official-source intelligence section, or from `backend`:

```bash
.venv/Scripts/python.exe -m app.intelligence.cli ingest --source BDNS --query PYME
.venv/Scripts/python.exe -m app.intelligence.cli draft
.venv/Scripts/python.exe -m app.intelligence.acceptance
```

Use `.venv/bin/python` on WSL/Linux. The last command explicitly performs live official-source requests and simulated approver API calls. Normal tests use recorded fixtures and never contact government sites.

BDNS and BOE public API connectors are enabled. Cámara's recorded-page parser is implemented, but automated access is disabled because its published terms prohibit automation. Permission is needed before enabling live Cámara fetching.

See [M2 implementation report](docs/MILESTONE_2_IMPLEMENTATION_REPORT.md) for actual results and remaining limitations. No Milestone 3 functionality is included.
