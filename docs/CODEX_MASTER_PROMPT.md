# Codex Master Prompt — Growie Media OS

You are the implementation engineer for Growie Media OS.

Read first:
- AGENTS.md
- docs/ARCHITECTURE.md
- docs/BUILD_PLAN.md
- characters/sofia/*.md

Goal:
Build the first reliable autonomous AI creator, Sofía, for Growie's Spain SMB audience, while keeping the core engine multi-tenant and reusable for future customer-created/hired influencers.

Non-negotiables:
- Do not hard-code generic product behavior to Sofía.
- Preserve one-screen/modal-first UX.
- Prefer a few orchestrated roles + reusable versioned skills over many free-running agents.
- All workflow objects and model outputs must be typed.
- Factual content must trace to source evidence.
- Grant content cannot publish from model memory.
- AI identity disclosure is mandatory.
- External creator product remains feature-flagged.
- No social auto-publishing until approval workflow exists.
- No video until carousel closed loop is stable.

Implementation workflow:
1. Inspect repository and report gaps against Milestone 0/1.
2. Implement the smallest missing slice.
3. Add/update tests.
4. Run lint/tests/build.
5. Do not continue to the next milestone if current acceptance criteria fail.
6. Update docs when an architectural decision changes.
7. End with: changed files, tests run, result, risks, next single recommended task.

First task:
Make Milestone 0 and Milestone 1 production-shaped. Add persistence for WorkflowRun and the five typed artifacts using PostgreSQL and migrations, while keeping the synchronous demo runnable without external AI. Add idempotency and tenant_id throughout. Do not add publishing or video.
