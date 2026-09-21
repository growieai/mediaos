# Growie Media OS

For the prepared M0–M2 review flow and local startup, see [Testing guide](docs/TESTING.md).
For rendered images and separate visual approval, see [Visual testing](docs/VISUAL_TESTING.md).

Standalone, multi-tenant internal media workflow. Growie is the first seed tenant and Sofía the first influencer. Character behavior comes from versioned database configuration.

Milestones 0/1 implement:
`SourceSnapshot → ResearchPack → ContentBrief → CAROUSEL revision → QA → AWAITING_APPROVAL → human approval → APPROVED`.

Every step, attempt, artifact and state change is persisted in PostgreSQL. M2 adds bounded official-source discovery, immutable raw snapshots, opportunity versions, verification, audience scoring and sourced drafts. M3 adds deterministic PNG rendering, visual QA, exact human visual approval and private ZIP export. Runtime workflows remain deterministic/mock with zero model cost. Nothing is published to social platforms.

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

See [M2 implementation report](docs/MILESTONE_2_IMPLEMENTATION_REPORT.md) for its results and remaining limitations. For Cámara permission, use the draft in [Cámara access request](docs/CAMARA_ACCESS_REQUEST.md); no email has been sent automatically.

## Render and review (M3)

After migration and seed, run `python scripts/dev.py visual-acceptance`. It verifies separate content
and visual approvals, checked PNG/ZIP output, stale-revision rejection and blocked claims, then leaves
a fresh render for your review. The report is `.local/visual-acceptance-report.json`; approvals in this
harness are simulations. The one-screen console previews every image and supports exact visual
approval and download. Downloading does not post anything.

The proposed Sofía portrait/reference pack was generated with Codex's built-in image tool and imported
as versioned configuration. Final appearance still needs your review. The application does not require
an AI key to render carousels. As requested, paid model calls stay disabled until you configure them;
Instagram stays disconnected until you connect an account. The tested structured-output adapter is
not yet enabled in runtime workflows. See [Build plan](docs/BUILD_PLAN.md) for dependency gates.

## Delivery rehearsal (partial M6)

After migrating and seeding, an operator can run **Delivery preflight** below an approved render in
the console. It validates the exact approved caption and PNG files, then saves a `DRY_RUN_COMPLETE`
receipt. No account is connected and nothing is posted. A previous receipt does not authorize a
future post. Run `python scripts/dev.py delivery-acceptance` for the persisted acceptance simulation.
See [Delivery testing](docs/DELIVERY_TESTING.md) for the controls, limits and remaining dependencies.

## Reported metrics (partial M7)

The console can attach manual self-reported observations or explicitly synthetic fixtures to an
exact historically approved render. Counts remain immutable; unavailable values stay Unknown.
Two observations with matching definitions can produce a saved descriptive comparison, including
reported decreases. This does not fetch Instagram insights, infer causes or update editorial policy.
Run `python scripts/dev.py metrics-acceptance` after migration/seed to exercise the entire local
flow with clearly labelled fixture observations. See [Metrics testing](docs/METRICS_TESTING.md).
