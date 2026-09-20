# AGENTS.md — Growie Media OS

## Mission
Build a production-grade, multi-tenant autonomous media operating system. Growie is tenant #1 and Sofía is influencer #1. Never hard-code business logic to Sofía when it belongs to a generic influencer, mission, skill, workflow, or policy abstraction.

## Product principles
1. Value first: content must be useful before it is promotional.
2. One closed loop at a time: source -> research -> brief -> content -> QA -> approval/publish -> metrics -> learning.
3. Agent = orchestrated role; most capabilities are versioned skills, not free-running agents.
4. Persist state. No critical workflow should depend only on chat context.
5. Every generated factual claim must be traceable to a source pack.
6. AI identity must be disclosed. Never impersonate a real person.
7. Growie product placement is a configurable association level, not baked into every post.
8. Multi-tenant from the first schema. External creator UI remains feature-flagged until Sofía is proven.
9. Use typed schemas and deterministic code for dates, scheduling, permissions, billing, retries, idempotency, and state transitions.
10. Keep the console one-screen and modal-first. Avoid unnecessary route/page hopping.

## Engineering rules
- Backend: Python 3.12+, FastAPI, Pydantic v2.
- Frontend: Next.js + TypeScript.
- DB: PostgreSQL; pgvector-ready.
- Workflow: Temporal for production durable workflows. The local demo may run synchronously but must preserve the same typed step boundaries.
- Cache/locks: Redis.
- Assets: S3-compatible object storage.
- AI providers: adapters behind interfaces. No model/provider calls directly from UI/routes.
- All model outputs that feed software logic must use structured schemas.
- Every skill run records: skill version, model, inputs hash, output, latency, cost metadata, status, and errors.
- Every publishable item must pass QA. A BLOCK state cannot be overridden by downstream code.
- Do not delete tests to make a change pass.
- Do not silently weaken types or validation.
- Add migrations for schema changes.
- No secrets in git.
- No production publishing from tests.

## Development method for Codex
For each task:
1. Read this file and relevant docs.
2. Inspect current implementation before editing.
3. State the smallest implementation plan.
4. Implement only the requested slice.
5. Run format/lint/tests.
6. Fix failures caused by the change.
7. Summarize changed files, tests, migration impact, and remaining risks.

## Definition of done
A feature is done only when:
- behavior is covered by tests;
- state transitions are explicit;
- failure/retry behavior is defined;
- logs are useful;
- tenant isolation is preserved;
- sensitive operations have an approval policy;
- docs are updated when architecture or workflow changes.

## First milestone
Make Sofía complete one verified carousel workflow:
source input -> ResearchPack -> ContentBrief -> CarouselDraft -> QAReport -> approval record -> saved run artifact.
Do not add video, marketplace, or auto-replies until this loop is reliable.
