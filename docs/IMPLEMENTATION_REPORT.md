# Milestones 0/1 implementation report

Frozen baseline: **a021ac1 — M0/M1 — Code Complete / Environment Verification Pending**. The results below describe that baseline. Docker execution and hosted CI were not verified at baseline and must not be represented as complete without an actual run. Milestone 2 continues separately on `milestone-2-spain-intelligence`; see its implementation report.

Verification follow-up, 2026-09-21 local time: all 60 baseline tests pass within the 102-test M2 suite. [Hosted CI](https://github.com/growieai/mediaos/actions/runs/35529691067) successfully built and started the Docker services, migrated an empty standalone container database, seeded it and ran the persisted approval/stale-revision/blocked-claim acceptance through its HTTP API. This resolves the container/hosted-CI follow-up for the integrated M0–M2 branch; the historical results below remain unchanged.

Repository: C:/mediaos/growie-media-os-starter. The starter was imported into Git with baseline commit f7a8061.

## Files changed

Core changes are in backend/app/models/schemas.py, backend/app/db/repository.py, backend/app/services/workflows.py, backend/app/skills/, backend/app/api/routes.py and backend/migrations/. The full file inventory is appended below.

Infrastructure changes include pinned Python and npm lockfiles, application Dockerfiles, isolated Compose configuration, the cross-platform dev helper, Make targets and GitHub Actions CI. Character runtime data is seeded from the existing Sofía Markdown plus characters/sofia/runtime.json.

The console is a minimal internal one-screen view with authenticated source, research, brief, carousel, QA, audit and exact-revision approval/rejection controls.

## Schema created

24 domain tables: tenants, principals, tenant_memberships; influencers, influencer_versions, missions, character_config_versions; source_snapshots, facts, research_packs, research_pack_versions, research_pack_sources, research_pack_facts; content_briefs, brief_facts, content_assets, content_asset_versions, content_claims; workflow_runs, skill_runs, qa_reports, approval_records, audit_events, cost_events.

Private authentication context and Alembic metadata are separate. Entity keys are UUIDs; membership is a composite join key. Tenant-owned rows have composite ownership/lineage constraints. Artifact payloads use strict schema-versioned JSONB and content hashes.

## Migrations

Alembic revision 0001 creates the standalone schema, RLS policies, immutable records, stage guards and protected functions. Fresh-schema migrations were exercised repeatedly by the PostgreSQL integration suite. Seed was run against the standalone development database and is repeatable.

No automatic destructive downgrade exists. Restore a backup or recreate an explicitly disposable database instead. This first, unreleased migration was finalized during implementation; do not edit it after deployment.

## Workflow state machine

CREATED → SOURCE_CAPTURED → RESEARCHING → RESEARCH_COMPLETE → BRIEFING → BRIEF_COMPLETE → CONTENT_GENERATING → CONTENT_COMPLETE → QA_RUNNING → AWAITING_APPROVAL → APPROVED.

QA can instead produce REVISION_REQUIRED or BLOCKED. Execution errors produce FAILED. All transitions are guarded and audited. A content revision invalidates the current QA pointer and requires fresh QA/approval.

## RLS and tenant strategy

23 tenant-scoped tables enable and force RLS. Global principals are inaccessible to the runtime role. Authentication checks a hashed bearer credential and membership, then binds identity in a private backend/transaction context. Custom session variables cannot impersonate a tenant. Pooled connections are tested for context isolation.

The runtime role is separate from the privileged migration/admin role and has no superuser, BYPASSRLS, role-creation or database-creation privilege. Runtime configuration rejects an admin database username.

Tests prove cross-tenant retrieval, raw SQL mutation, evidence linkage, influencer selection and approval are rejected.

## Approval protection

Runtime cannot directly update workflow state, insert approvals or manufacture QA reports. Database QA computes its result from stored revisions and evidence.

The guarded approval function requires an authorized approver, AWAITING_APPROVAL, matching current asset/research/QA IDs, passing QA and currently eligible evidence. Approval and revision lock the same workflow row. A BLOCK cannot be converted to approval for the same content revision. New revisions never inherit old approval.

Rejection retains identity/revision checks but remains possible when evidence becomes ineligible after QA. Approval records are immutable.

## Evidence model

Manual sources retain raw content, checksum, origin, publisher, capture/submission timestamps, submitter, classification, environment and an explicit fixture marker. A separate APPROVER attests a source before execution; an OFFICIAL label alone does not verify it. Fixtures cannot be attested and fail QA.

Fact IDs are stable for a source/span/statement. Bounds and duplicate spans are validated before creating a workflow. Database constraints verify source ownership, exact spans, research membership and content claim references.

Factual content is restricted to verbatim verified statements in this milestone. Non-factual creative text must match versioned configuration templates. Mandatory disclosure, CTA, language and brand policy are checked.

Grant metadata requires GRANT classification. Mandatory eligibility/date evidence, per-field quotations and UTC deadline rules fail closed; confidence never overrides them. Human reviewers remain responsible for source authenticity and correct manual classification.

## Idempotency implementation

A unique tenant/idempotency-key constraint and canonical input hash make creation concurrency-safe. Identical replay returns the existing run; a changed payload returns 409. Different tenants can use the same key independently.

## Retry implementation

Each invocation starts with a committed SkillRun attempt. Validated artifacts, successful attempts and checkpoints commit atomically. A PostgreSQL advisory lock serializes execution; unique successful step keys and artifact constraints prevent duplicate committed results.

Transient failures default to three bounded attempts with persisted exponential backoff. Configuration permits at most five. Deterministic failures do not retry. Interrupted attempts are recorded and the next execute request resumes the last valid checkpoint. Exhaustion persists FAILED.

## Model and cost logging

The five skills are research.extract, research.verify, editor.build_brief, content.carousel and qa.validate. Model/provider calls are not implemented. MockAdapter strictly validates typed outputs.

Attempts record versions, input hash, output, timing, state, errors, retry metadata and resulting artifact references. Mock provider/model/adapter metadata is explicit; provider cost and token usage are zero. Cost events and audit/skill timestamps provide persisted operational telemetry without logging credentials.

## Tests added and results

The final suite contains 60 passing tests against PostgreSQL 18.4, including:

- Clean migration, restricted role, RLS and persisted artifact round trips.
- Cross-tenant API reads, SQL writes, references, evidence reuse and approval.
- Missing/fabricated/unrelated evidence, unverified sources, fixtures and grant deadlines.
- Unsupported claims, disclosure, CTA/brand/language checks and revision-required behavior.
- Approval without QA, BLOCK bypass attempts, stale revisions and concurrent revision/approval.
- Concurrent source attestation, immutable approvals and sealed evidence membership.
- Same-key replay, conflicting payloads, tenant-separated keys and concurrent creation.
- Retryable/non-retryable failures, exhaustion, interruption/resume and artifact deduplication.
- Strict schemas, request-size limits, auth, pooled-context isolation and log redaction.
- Character configuration rollback, token rotation, selected mission objectives and duplicate-span rejection.

Final result: **60 passed** in 80.50 seconds. One upstream Starlette/AnyIO deprecation warning remains; it is not a test failure.

Backend Ruff lint and formatting pass. Mypy passed for all 24 application source files.

## Build results

Next.js 16.3.5 production build and TypeScript checking passed. The output contains the one-screen console and internal API proxy.

Live localhost checks passed for rendered console HTML, API proxy health, authenticated persisted-run retrieval and rejection of unauthorized requests. The in-app browser failed to attach, so no visual-browser verification is claimed.

Compose YAML structure and loopback bindings were checked. Container builds/startup were not run because no Docker engine is installed. Hosted CI has not run because no remote is configured. The committed CI workflow includes backend lint/type checks, PostgreSQL tests, migrations from empty, seed/acceptance, frontend build and container builds.

## Acceptance demonstration

The persisted acceptance harness uses separate OPERATOR and APPROVER API requests. It attests a real policy statement from docs/SECURITY.md, runs all five skills, observes QA PASS and AWAITING_APPROVAL, then creates an exact-revision approval record.

It also proves identical idempotency replay, rejected cross-tenant identity, unsupported claim → BLOCKED → rejected approval, and changed asset → stale approval rejected → fresh QA/approval required.

Results and actual run/revision IDs are saved in .local/acceptance-report.json. This is an automated simulation of human-role API actions, not a claim that a person reviewed the content.

## Review

Ran codex review --uncommitted twice. Five reported material findings were fixed with regressions:
1. Source span bounds and whitespace mismatch.
2. Rejection blocked by evidence that became ineligible after QA.
3. Character configuration rollback failing to become active.
4. Grant metadata mislabeled GENERAL.
5. Duplicate evidence creating duplicate fact IDs.

Additional implementation review tightened audit order, source-attestation concurrency, sealed evidence links, terminal skill attempts, retry exhaustion telemetry and runtime-role configuration.

## Known limitations

- Docker execution and hosted CI remain required before milestone sign-off.
- The runner is synchronous and interrupted runs need an explicit resume request.
- Research/source corrections create a new run; content-only revisions stay within the run.
- Evidence is manually attested and factual text is verbatim; there is no automatic source research.
- Internal token lifecycle/SSO, backup/restore operations, TLS ingress, central monitoring and durable orchestration remain production deployment requirements.
- The migration/admin account currently needs trusted schema/role-management and RLS-bypass capabilities; it is never used by API workloads.
- No rendered carousel images or publishing capability exists.

## Deferred work

Milestone 2 and every explicitly excluded capability remain unimplemented: real AI providers, crawling, image/video/voice generation, social publishing, community automation, external creator UI, signup, marketplace, billing and production Temporal workflows.

## Commands to run locally

From the repository root, with Python 3.12+, Node 24+ and Docker Compose installed:

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

Equivalent Make targets exist. See LOCAL_DEVELOPMENT.md for PowerShell/WSL and host-mode commands. The currently running native fallback uses its separate localhost PostgreSQL on port 55432; normal Compose uses 55433. Console: http://127.0.0.1:3000. API: http://127.0.0.1:8000.

Credentials are in .local/credentials.json; they are excluded from Git and should remain private. No Growie production database or service was contacted.

The known local credential scan passed across all 54 changed/new files.

Inspection correction: the starter's original repo_root calculation was already correct. The actual configuration-path fix was anchoring .env loading to the repository rather than the caller's working directory.


## Complete changed-file inventory

- .dockerignore
- .env.example
- .gitattributes
- .github/workflows/ci.yml
- .gitignore
- Makefile
- README.md
- apps/console/Dockerfile
- apps/console/app/api/internal/[...path]/route.ts
- apps/console/app/page.tsx
- apps/console/next-env.d.ts
- apps/console/next.config.ts
- apps/console/package-lock.json
- apps/console/package.json
- apps/console/tsconfig.json
- backend/Dockerfile
- backend/alembic.ini
- backend/app/acceptance.py
- backend/app/admin.py
- backend/app/ai/client.py
- backend/app/api/routes.py
- backend/app/config.py
- backend/app/db/__init__.py
- backend/app/db/repository.py
- backend/app/main.py
- backend/app/models/schemas.py
- backend/app/observability.py
- backend/app/seed.py
- backend/app/services/__init__.py
- backend/app/services/workflows.py
- backend/app/skills/creator.py
- backend/app/skills/editor.py
- backend/app/skills/qa.py
- backend/app/skills/research.py
- backend/app/workflows/sofia_demo.py
- backend/migrations/env.py
- backend/migrations/versions/0001_guards.sql
- backend/migrations/versions/0001_initial.py
- backend/migrations/versions/0001_schema.sql
- backend/pyproject.toml
- backend/requirements.lock
- backend/tests/conftest.py
- backend/tests/test_demo.py
- backend/tests/test_invariants.py
- characters/sofia/runtime.json
- docker-compose.yml
- docs/ARCHITECTURE.md
- docs/DATA_MODEL.md
- docs/IMPLEMENTATION_REPORT.md
- docs/LOCAL_DEVELOPMENT.md
- docs/SECURITY.md
- docs/WORKFLOWS.md
- scripts/bootstrap.sh
- scripts/dev.py
