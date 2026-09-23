# Workflow state and recovery

## Speaking video and manual handoff

The content state machine below is unchanged. A separately approved content revision can
start a media run: `CREATED → SPEECH_READY → IMAGE_READY → ASSETS_READY → AVATAR_PENDING →
AVATAR_READY → AWAITING_APPROVAL → APPROVED/REJECTED`. `BLOCKED`, `FAILED` and
`UNKNOWN_OUTCOME` remain distinct. SQL derives narration from selected approved text paths;
operators cannot introduce unsupported claims through a free-form script.

Media creation is tenant-idempotent. Calls reserve a job/SkillRun/cost before network work;
receipts recover committed local results, and uncertain submissions are never blindly replayed.
Provider cooldown and bounded attempts are persisted. Fresh source/content/profile and exact
manifest checks protect approval/download. See [full semantics](SPEAKING_VIDEO.md).

Conversion requests require explicit separately captured consent, exact approver attestation,
current revisions and unexpired/unrevoked consent. Export is idempotent and produces a saved
manual receipt with `delivered=false`; see [conversion workflow](CONVERSION_TESTING.md).

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

## Visual workflow (M3)

`current QA PASS → RenderRun CREATED → RENDERING → PASS / REVISION_REQUIRED / BLOCKED / FAILED`.
PASS means deterministic visual checks passed, not human approval. With exact content APPROVED,
an authorized human reviews the images/caption and creates a separate VisualApprovalRecord.
Only a current, visually approved render can pass the ZIP export guard.

- `GET/POST /v1/workflow-runs/{id}/renders` lists configurations/history or creates and executes a render.
- `GET /v1/renders/{id}` returns the persisted render and visual decisions.
- `POST /v1/renders/{id}/execute` resumes CREATED/RENDERING with a bounded attempt history.
- `GET /v1/renders/{id}/slides/{index}` returns an authenticated PNG preview.
- `POST /v1/renders/{id}/approve` or `/reject` records an exact manifest-hash decision.
- `GET /v1/renders/{id}/export` rechecks approvals/freshness and returns checked PNGs, manifest and caption.

Creation requires asset_version_id, visual_config_version_id and idempotency_key. Repeating identical
input returns the same RenderRun; a different payload conflicts. A terminal failed/rejected render
needs an explicit new request key after correction. A process interruption can resume an already
written immutable output after checksum validation; it never duplicates a committed successful artifact.

Changing content requires the existing new QA/content approval flow and a new render/visual approval.
Changing canonical visual configuration or creating a newer render makes older exports invalid.
An interrupted render that becomes stale is marked FAILED. A failed visual check never advances
the content workflow to APPROVED, and a pre-existing content approval never bypasses visual review.

## Delivery preflight (partial M6)

`CREATED → VALIDATING → DRY_RUN_COMPLETE | BLOCKED | RETRY_WAIT | FAILED`.
A due RETRY_WAIT may enter VALIDATING again. Maximum attempts: three; transient local failures
schedule 1s/2s backoff. Early execution returns the saved waiting state. Policy/freshness failures
are BLOCKED; damaged packages and malformed adapter output fail without retry. Interrupted RUNNING
SkillRuns become INTERRUPTED on resume. A session advisory lock prevents concurrent execution, and
the existing one-success-per-step constraint prevents duplicate committed success.

- `GET /v1/delivery-targets` lists owned target revisions.
- `POST /v1/renders/{id}/deliveries` takes only target_id, idempotency_key and optional mode=DRY_RUN.
- `GET /v1/workflow-runs/{id}/deliveries` lists saved requests/receipts.
- `GET /v1/deliveries/{id}` returns the request and its SkillRun attempts.
- `POST /v1/deliveries/{id}/execute` executes/resumes an eligible saved request.

Writes require OPERATOR. The current exact content and visual approvals are checked on start,
claim and completion; actual bytes are checked before receipt commit. A changed request under the
same tenant/key conflicts. Identical replay returns the historical result; it does not perform a
new check. A terminal failure needs correction and an explicit new request. Nothing is posted.

## Historical metrics workflow (partial M7)

`historically approved render → MANUAL/SELF_REPORTED or FIXTURE subject → immutable observations
→ descriptive comparison`. This flow leaves the original WorkflowRun state and editorial policy
unchanged. It does not require current export eligibility: an expired source or newer asset revision
does not invalidate the historical association. It cannot grant permission for a new dispatch.

- `POST/GET /v1/workflow-runs/{id}/metric-subjects` registers or lists historical associations.
- `GET /v1/metric-subjects/{id}` returns the subject and its saved observations/reports.
- `POST /v1/metric-subjects/{id}/snapshots` imports a typed, explicitly manual or fixture observation.
- `POST/GET /v1/metric-subjects/{id}/learning-reports` creates or lists descriptive comparisons.

Writes require OPERATOR and authenticated tenant context. Observation provenance is inherited from
the immutable subject. Learning accepts only two snapshots of that subject with matching scope and
definitions and strictly increasing UTC times. It subtracts reported counters without turning
unknowns into zeros. Negative differences remain visible as possible corrections; neither positive
nor negative differences establish what caused an outcome. There is no cross-post ranking or
automatic policy update.

Each operation uses a tenant-scoped key and canonical input hash. An identical replay returns the
saved row; changed input conflicts. The learning function computes and commits the report, completed
`learning.describe` attempt, zero-cost telemetry and audit event in one SQL transaction. Validation
rejection or transaction failure rolls back the entire operation; it does not fabricate persisted
failed-attempt history. After an operational interruption or uncertain response, retry the same
request/key to recover the committed result or execute an uncommitted operation once. There is no
external side effect, background retry scheduler or live insights fetch in this slice.

## Internal reply review (partial M8)

`manual event → CREATED → CLASSIFYING → CLASSIFIED → DRAFTING → DRAFT_COMPLETE → QA_RUNNING
→ AWAITING_REVIEW → human decision → REVIEWED_DRAFT / REJECTED`.
Unrecognized input or missing policy ends at HUMAN_REVIEW without a generated reply. Deterministic
QA branches to BLOCKED or REVISION_REQUIRED. Transient execution failures use RETRY_WAIT; exhausted
or non-retryable failures become FAILED. These are community states, not new carousel states.

The `community.classify`, `community.draft` and `community.qa` skills have committed attempt rows
before work begins. Each stage permits three attempts with persisted 1s/2s backoff. Resume marks
orphan attempts INTERRUPTED; a session lock prevents concurrent execution. Exact successful
checkpoints are immutable and never committed twice. Unknown intent and QA findings are policy
results, not reasons to repeatedly retry. No parent WorkflowRun state is changed by this flow.

- `POST/GET /v1/workflow-runs/{id}/community-events` saves/lists observations.
- `GET /v1/community-events/{id}` includes immutable review history.
- `POST /v1/community-events/{id}/reviews` creates and executes a review using selected fact IDs.
- `GET /v1/community-reviews/{id}` returns checkpoints, claims, attempts and decisions.
- `POST /v1/community-reviews/{id}/execute` resumes a saved review.
- `POST /v1/community-reviews/{id}/approve` or `/reject` requires exact draft/QA hashes and APPROVER.

Creation is tenant/key/hash idempotent. An explicit new key creates a new review revision. Approval
locks the workflow then its event/review, checks the latest review and pinned parent revisions,
and reruns the parent evidence QA including official-source conflicts and freshness. It never
inherits an older decision or allows a fixture through. A reviewed draft alone cannot be sent.

## Signed handoff and media follow-up (0016–0017)

Handoff: reviewed business identity + exact reviewed consent → immutable SEND intent →
AWAITING_AUTHORIZATION → separate APPROVER decision → AUTHORIZED → committed attempt →
signed RECEIVED or UNKNOWN_OUTCOME. The runtime verifies receipt HMAC independently in PostgreSQL.
Read reconciliation can recover an old receipt after consent expiry without resending the payload.
A signed NOT_FOUND result does not authorize a replacement send. Revocation/retention expiry can
create a separate REVOKE intent with its own review and signed REVOKED acknowledgement.

All records retain tenant ownership and immutable hashes. The parent content approval is unchanged;
a destination receipt never means a business audit completed. Endpoints, provisioning and exact
state/recovery semantics are documented in [conversion delivery](CONVERSION_DELIVERY.md).

Voice profiles and budgets can be configured by an ADMIN in the speaking-video panel. Saving
configuration does not generate media. Paid reservations require prices checked within 30 days;
free polls/composition/receipt recovery still work when prices age. Failed provider attempts retain
bounded, sanitized request IDs for investigation without permitting uncertain paid replay.

## Explicit connected-provider flows (0009–0015)

Text creator: `exact current brief → persisted policy/budget reservation → typed provider
selection → deterministic fact/template materialization → existing content QA`. A rate-limit
refusal can retry within its saved cap and cooldown. Unknown provider outcomes require review;
an existing checkpoint cannot silently switch providers. Mock mode stays the default.

Social: `current approved content + visual render → exact JPEG/caption/account plan →
AWAITING_PUBLISH_APPROVAL → separate APPROVER decision → AUTHORIZED → CHILD/CONTAINER/POLL
checkpoints → READY → explicit OPERATOR dispatch → PUBLISHING → PUBLISHED`. BLOCKED, FAILED,
REJECTED and UNKNOWN_OUTCOME remain distinct. No automatic publishing is enabled. Unknown posts
require a persisted approver attestation plus a persisted read of the matching provider object;
they cannot be retried by changing the idempotency key. Historical acknowledgements can recover
from a durable receipt even after current content or connection eligibility changes.

Insights: `confirmed connected post → persisted read attempt → immutable PLATFORM snapshot →
second matching observation → descriptive learning report`. Unknown counters stay null, decreases
remain visible, and comparisons never modify policy or confer publication authority.

Community: `signed owned-account comment webhook → immutable PLATFORM event → existing reply
classification/draft/QA/review → separate AUTHORIZE_REPLY → explicit send → SENT`. The reply is
rechecked against current parent evidence and exact text while dispatch holds the relevant locks.
Unknown writes stop for investigation. Manual or fixture events cannot enter platform dispatch.

Workflow provider attempts have persisted telemetry before execution. [Social integration](SOCIAL_INTEGRATION.md)
and [real model execution](REAL_MODEL_EXECUTION.md) document setup, bounded recovery and endpoints.

Rate-limit cooldowns are stored per tenant/account and survive new keys, exhausted attempts and
reconnections. Contradictory unknown/retry flags are rejected by PostgreSQL. Workflow locks precede
account and attempt locks to prevent cycles with audit/evidence constraints. Webhook intake commits
before linking acquires workflow locks; retries recover any persisted but unlinked event.

Account IDs provide stable historical post/comment identity across connection versions. A new reply
intent may use the current connection for an existing reviewed comment, but needs new outbound
authorization. Existing decisions never transfer; a SENT or uncertain reply cannot be repeated by
reconnecting the account. Historical published media IDs are unique within their tenant/account.

Historical reads reserve and pin the latest compatible connection for the original account,
influencer and API version. Revoked, incompatible or changed credentials prevent the read;
an existing durable provider receipt can still recover independently of current credentials.
An approver may reject a stale AWAITING_PUBLISH_APPROVAL or AUTHORIZED plan only before any
provider job exists. This releases the undispatched plan for replacement without losing history.

After publication becomes confirmed, saved comments are linked in bounded local batches.
Immutable links are per-event checkpoints. An interrupted batch can be resumed through
`POST /v1/social-publishes/{id}/comments/relink`; only an OPERATOR may request it. Relinking
makes no provider/model call and grants no reply authorization. A relinking failure leaves
the confirmed post intact and reports RETRY_REQUIRED.
