# Growie Media OS

Standalone, multi-tenant internal media workflow. Growie is the first seed tenant and Sofía the first influencer. Character behavior comes from versioned database configuration.

Milestones 0/1 implement:
`SourceSnapshot → ResearchPack → ContentBrief → CAROUSEL revision → QA → AWAITING_APPROVAL → human approval → APPROVED`.

Every step, attempt, artifact and state change is persisted in PostgreSQL. This release has deterministic mock skills only. It does not call AI providers, fetch sources, render images, or publish content.

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
