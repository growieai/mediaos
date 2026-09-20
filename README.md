# Growie Media OS — Starter

A production-shaped starter for Growie's first autonomous AI influencer, **Sofía**, with a path to a future multi-tenant "create/hire an AI influencer" product.

## What already works

The repo contains a deterministic first closed loop:

`SourceInput -> ResearchPack -> ContentBrief -> CarouselDraft -> QAReport -> saved run`

This lets the team prove orchestration, schemas, QA gates and tenant boundaries before connecting live sources, image/video generation or social publishing.

## Why Codex is separated from runtime

Use Codex to build, refactor, test and review this repository. The runtime influencer is implemented as durable workflows + typed skills. This prevents your production product from depending on a coding-agent session.

## Local setup

```bash
cp .env.example .env
./scripts/bootstrap.sh
```

Then start dependencies:

```bash
docker compose up -d postgres redis minio temporal temporal-ui
```

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -e '.[dev]'
python -m app.workflows.sofia_demo
pytest -q
uvicorn app.main:app --reload
```

Demo API:

```bash
curl -X POST http://127.0.0.1:8000/v1/influencers/sofia/demo \
  -H 'Content-Type: application/json' \
  -d '{
    "source_id":"official-demo",
    "title":"Spain SMB support update",
    "source_type":"official",
    "market":"ES",
    "raw_text":"Put an official source extract here."
  }'
```

Console:

```bash
cd apps/console
npm install
npm run dev
```

## Codex setup

From the repository root:

```bash
codex
```

Codex supports `/init`, `/status`, `/permissions`, `/model` and `/review`. This repo already includes an opinionated `AGENTS.md`, so ask Codex to read it rather than overwriting it.

First prompt:

```text
Read AGENTS.md, docs/ARCHITECTURE.md, docs/BUILD_PLAN.md, and docs/CODEX_MASTER_PROMPT.md.
Inspect the entire repository. Do not code yet.
Give me a gap analysis for Milestone 0 and Milestone 1, including schema, migrations,
idempotency, tests, observability, tenant isolation, and failure handling.
Then propose the smallest implementation sequence.
```

Second prompt, after you approve the plan:

```text
Implement only Milestone 0 and Milestone 1 from the approved plan.
Do not add video, social publishing, external creator onboarding, or marketplace features.
Run all tests and fix failures. Update documentation if architecture changes.
```

After implementation:

```text
/review
```

Use Git checkpoints before/after each milestone.

## Server path

### Pilot
Use an EU-hosted Linux server for app workers, with managed PostgreSQL and S3-compatible storage if possible. Dockerize everything. Do not run GPU/video generation on the app server initially; use provider APIs or separate rendering workers.

### Production
Split into stateless API, workflow workers by permission type, managed database/cache/storage, and a durable workflow service. Keep publishing credentials isolated from research/render workers.

See `docs/ARCHITECTURE.md`.

## Feature flags

External creators, auto-publishing, auto-replies and video are disabled by default.

The deliberate sequence is:

1. Sofía text/carousel loop.
2. Source verification.
3. Visual rendering.
4. Approval.
5. One social publisher.
6. Metrics.
7. Community.
8. Growie conversion.
9. Video.
10. External creator product.
