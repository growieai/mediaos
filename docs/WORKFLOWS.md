# Workflow state and recovery

## State machine

```text
CREATED → SOURCE_CAPTURED → RESEARCHING → RESEARCH_COMPLETE
→ BRIEFING → BRIEF_COMPLETE → CONTENT_GENERATING → CONTENT_COMPLETE
→ QA_RUNNING → AWAITING_APPROVAL → APPROVED
             ↘ REVISION_REQUIRED
             ↘ BLOCKED
active execution → FAILED
```

PASS is a QA result, not a workflow approval. Only the guarded human decision function can reach APPROVED. Human rejection from AWAITING_APPROVAL creates a REJECT approval record and moves to REVISION_REQUIRED.

A content revision can start from AWAITING_APPROVAL, APPROVED, REVISION_REQUIRED or BLOCKED. In one transaction it enters CONTENT_GENERATING, inserts a new immutable version, clears the current QA pointer and commits CONTENT_COMPLETE. Old QA/approval IDs fail approval. Fresh QA and approval are required.

SOURCE_CAPTURED precedes the optional source-attestation request. Executing unverified/fixture evidence is allowed for inspection, but deterministic QA BLOCKS it. Attestation cannot be changed after execution starts; correcting evidence requires a new run.

Every transition is guarded in PostgreSQL and creates an audit event. Generic checkpoint calls cannot request AWAITING_APPROVAL or APPROVED. QA and approval functions own those transitions.

## Idempotency

Creation requires an idempotency key. The canonical JSON input hash excludes the key, includes all source/configuration-selection input, sorts keys and uses explicit JSON representations. A tenant/key unique constraint handles concurrency.

Same tenant/key/input returns the original run. Different input produces HTTP 409. Different tenants may independently use the same key. Source and initial workflow checkpoint commit atomically; no partially created source is returned.

## Retries

A session-level PostgreSQL advisory lock prevents concurrent execution of the same run. Each attempt is committed as RUNNING before skill work. Successful artifact writes, attempt completion and checkpoint updates commit together. A partial failed transaction cannot leave a committed duplicate artifact.

RetryableSkillError, TimeoutError and ConnectionError default to three attempts with persisted exponential backoff (1 and 2 seconds before subsequent attempts). Configuration is bounded to at most five attempts; the persisted retry time is honored. Validation, policy and deterministic execution failures do not retry. Exhaustion produces FAILED. QA BLOCKED/REVISION_REQUIRED are policy results, not execution errors.

After interruption, POST execute resumes the last committed state. Successful step keys return their stored typed output. An orphan RUNNING attempt becomes INTERRUPTED and a new bounded attempt is recorded. A process dying after the final allowed attempt results in FAILED on resume. No background scheduler is included; an operator must resume interrupted runs.

Network loss with an uncertain commit requires reconnect/resume; it must not cause blind external side-effect replay. There are no external side effects in M1.

## Internal API

All business routes require Authorization: Bearer plus X-Tenant-ID. Health is intentionally unauthenticated and contains no tenant data. Readiness is authenticated and checks the restricted database role and schema access.

- POST/GET /v1/workflow-runs
- GET /v1/workflow-runs/{id}
- GET /v1/workflow-runs/{id}/artifacts
- GET /v1/workflow-runs/{id}/audit
- POST /v1/workflow-runs/{id}/execute
- POST /v1/source-snapshots/{id}/verify
- POST /v1/workflow-runs/{id}/revisions
- POST /v1/workflow-runs/{id}/approve
- POST /v1/workflow-runs/{id}/reject
- GET /v1/context, /v1/health, /v1/readiness

Approval/rejection requires asset_version_id, research_version_id and qa_report_id. Revision accepts the complete strict CarouselDraft payload.

## Observability

Structured application logs include correlation ID, tenant, workflow, skill attempt and state; only allowlisted fields are logged. Audit, skill and cost tables provide persisted workflow/skill latency, retries, failures, blocked QA, approval wait time and model-usage telemetry. No external monitoring service is connected.

## Official-source workflow (M2)

`MANUAL request → CREATED ingestion → RUNNING → discover page → commit RAW snapshot → normalize → deduplicate/link → version/change events → verify → score audiences → editorial decision → existing M1 workflow → QA → AWAITING_APPROVAL`. Only an authorized approver can then create APPROVED. SCHEDULE_READY identifies an invocable mode, not a running scheduler.

Ingestion terminal states are SUCCEEDED or FAILED. Page references, next cursor and document offset are persisted. A process interruption leaves RUNNING with the last committed offset; execute resumes it. The source's advisory lock prevents concurrent ingestion for that tenant/source. Canonical ownership constraints and opportunity-row locks serialize cross-source normalization. HTTP attempts retain retry timing and error categories; request retries are bounded at three. Long Retry-After values are persisted and stop the cycle instead of being ignored. Repeating a successful ingestion idempotency key returns that ingestion. A changed payload conflicts.

A successful unchanged fetch records a new observation. A changed document produces a version and typed deterministic change events. Date-based status is reevaluated on ingestion; approval always checks the current date even between ingestion cycles. Missing deadline, expiry, stale research, newer opportunity revision, unverified/fixture source or unresolved official conflict blocks approval. Unknown opening dates are never described as applications being open.

The editor can CREATE_CONTENT, WATCH, IGNORE or HUMAN_REVIEW. Broad programme fit is not a promise that an individual business qualifies. Duplicate recent coverage becomes WATCH. An explicit low brand-fit weight prevents promotion from dominating the initial score. No score or model confidence overrides deterministic source eligibility.

M1 state transitions are unchanged. Opportunity checks extend the original QA function and also run in an approval INSERT trigger. Ingestion and approval lock the same Opportunity row, preventing a concurrent source change from slipping through approval. Refreshing evidence requires a new workflow/ResearchPack binding; an old approval never transfers.
