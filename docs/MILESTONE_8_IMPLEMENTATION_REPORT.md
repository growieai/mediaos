# Milestone 8 internal community review implementation report

Branch: `milestone-8-community-review`, based on the published M7 checkpoint `e599c81`.

**Scope: partial M8, internal comment reply review.** An operator submits a comment, selects
approved evidence, inspects a deterministic reply and obtains a separate exact human decision.
The terminal approval state is REVIEWED_DRAFT. There is no social account connection, webhook,
send endpoint, automatic reply, DM, consent inference or conversion handoff. Paid AI remains off.
This is a testable internal foundation, not a completed live community integration.

## Behavior and configuration

The source carousel must have current approved content and passing evidence QA. The workflow's
immutable CharacterConfig can contain a strict optional CommunityPolicy. Seed versions Sofía's
initial Spanish phrases/templates as configuration data; generic runtime code has no character,
tenant, country or brand defaults. Old workflows retain their original configuration. Missing
policy produces HUMAN_REVIEW rather than inventing a response.

Three explicit deterministic skills run:

1. `community.classify` matches a whole configured phrase after trimming only ASCII spaces.
   Supported intents are ACKNOWLEDGEMENT and SOURCE_REQUEST. Everything else, including appended
   instructions or ambiguous eligibility questions, becomes HUMAN_REVIEW without a draft.
2. `community.draft` uses configured creative text, exact selected fact statements, the pinned
   language and mandatory AI disclosure. Facts must already be claimed by the exact approved
   carousel; the caller cannot submit replacement factual prose.
3. `community.qa` checks exact output, disclosure, selected evidence, length, fixture status and
   current parent QA. Missing required facts or fixture data fail closed. Length overflow requires
   revision. A PASS produces AWAITING_REVIEW, never approval.

The operator may create an explicit new review revision. An APPROVER reviews exact draft/QA hashes
and records APPROVE or REJECT. OPERATOR cannot approve. Historical decisions never transfer to a
new reply or a changed content/research/QA revision. No operation changes the original WorkflowRun
state; community failures do not mark the carousel FAILED.

## Schema and guarded writes

Migration **0006** follows unchanged migrations 0001–0005:

| Table | Boundary |
| --- | --- |
| `community_events` | Immutable MANUAL/FIXTURE origin, opaque participant reference, exact comment, UTC capture/receipt times, input/content hashes, tenant, creator and idempotency. |
| `community_reviews` | Consecutive event review revisions, pinned parent approval/artifacts and character configuration, selected facts, policy hash, staged state, retries and immutable typed checkpoints. |
| `community_reply_claims` | Relational links from reply blocks to exact parent content_claims and brief_facts. |
| `community_decisions` | Immutable approver identity, timestamp, decision/comment, review and exact parent/draft/QA linkage. |

All four tables force tenant RLS and grant runtime SELECT only. Seven public guarded functions
perform event registration, review creation, parent findings, stage claim/completion/failure and
human decision. Private functions independently compute expected classification, draft and QA.
Supplying a rehashed fabricated result or directly writing an approval state cannot bypass them.

Approval locks the parent workflow and review before reevaluating evidence. The inherited QA guard
also locks any official Opportunity, serializing source changes with decisions. It requires the
latest review revision, exact current approved parent and current source eligibility. Composite
foreign keys preserve ownership and evidence/approval lineage independently of application filters.

## State, retries and idempotency

```text
CREATED → CLASSIFYING → CLASSIFIED → DRAFTING → DRAFT_COMPLETE → QA_RUNNING
→ AWAITING_REVIEW → REVIEWED_DRAFT / REJECTED
```

Branches are HUMAN_REVIEW, BLOCKED, REVISION_REQUIRED, RETRY_WAIT and FAILED. Policy results do not
trigger retries. Every stage commits a RUNNING SkillRun before execution; output, successful attempt
and checkpoint commit atomically. Interrupted attempts remain visible as INTERRUPTED. Each stage
permits three attempts, with persisted 1s/2s backoff for transient failures. Explicit execute resumes
the last valid checkpoint. There is no background scheduler or external side effect to replay.

Tenant-scoped keys and canonical input hashes make event/review creation concurrency-safe. Identical
requests return the same persisted identity, including while another caller executes it. Changed
payloads conflict. Explicit execute returns a busy conflict when another execution holds its session
lock. Successful checkpoints and cost records do not duplicate on recovery.

Skills record provider=deterministic, model=none, adapter=community-exact-v1, zero tokens and zero
cost. CostEvents, stage audits, errors, retry times and latency remain persisted. Logs retain only
operational identifiers and categories; raw comments, credentials and evidence are not logged.

## API and console

- `POST/GET /v1/workflow-runs/{id}/community-events`
- `GET /v1/community-events/{id}`
- `POST /v1/community-events/{id}/reviews`
- `GET /v1/community-reviews/{id}`
- `POST /v1/community-reviews/{id}/execute`
- `POST /v1/community-reviews/{id}/approve` and `/reject`

The existing one-screen console adds **Comment reply review**, explicitly labelled **Draft only —
nothing sent**. It shows manual/fixture provenance, exact comment, evidence choices, classification,
reply, disclosure, QA, attempts and decisions. Review acknowledgement is bound to the exact review
ID and draft/QA hashes; refreshing to a different revision clears that acknowledgement.

Origins and comments are bounded data, never URLs to fetch or instructions to execute. MANUAL means
an operator assertion, not independently verified platform provenance. Fixtures remain permanently
synthetic and cannot be approved. Participant references avoid requiring contact details. The
existing size limits, tenant authentication, secret handling and disabled publishing flags remain.

## Files changed

- `backend/migrations/versions/0006_community.py` and `.sql`.
- `backend/app/community/`: typed schemas, pure policy, persistence/orchestration, routes and acceptance.
- `backend/app/models/schemas.py` and `characters/sofia/runtime.json`: optional versioned policy and seed data.
- Main router, repository discovery, readiness and migration-head test: integration.
- `backend/tests/test_community_unit.py` and `test_community_integration.py`: new negative/concurrency coverage.
- `apps/console/app/CommunityReview.tsx` and `page.tsx`: internal review controls.
- CI and `scripts/dev.py`: full-chain community acceptance commands.
- README and architecture/data/workflow/security/local-development/roadmap/testing documents,
  `COMMUNITY_TESTING.md` and this report.

No new package, external credential, service, image generation or production infrastructure is added.

## Verification

| Check | Observed result |
| --- | --- |
| Development migration/seed | Passed through 0006 on standalone Media OS PostgreSQL. |
| New pure tests | 58 passed. |
| Full suite with clean migrations | 408 passed in 158.40s after the review fix (308 inherited and 100 new); one upstream Starlette/AnyIO deprecation warning. |
| Backend quality | Ruff check/format passed (81 files); mypy passed (61 source files). |
| Frontend build | Passed. |
| In-process acceptance | Passed, including all inherited visual/delivery/metrics checks. |
| Actual console-proxy acceptance | Passed after the review fix at `2026-09-21T07:58:55Z`. |
| Independent review | Architecture review and Codex review completed; the attempt-history performance finding is fixed, with no material findings in the focused follow-up review. |
| Hosted CI | Not yet run for M8; public branch publication requires its own authorization. |

The first migration attempt caught an SQL CASE syntax error before commit; it was corrected without
resetting development data. Early test runs caught a missing-policy fixture that accidentally loaded
the new seed configuration and a worker helper treating a UUID as a string. Tests were corrected to
exercise their intended paths; guards and assertions were preserved. Review also caught concurrent
same-key creation returning a busy conflict and UI acknowledgement carrying across revisions; both
were fixed before the final run.

The final Codex review found that each reply loaded every attempt in its parent workflow before
filtering. Attempt queries now filter by tenant, workflow and exact review stage keys in SQL. A
regression verifies that other tenants, workflows, reviews and unrelated skills are excluded.

The persisted proxy demonstration is in `.local/community-acceptance-report.json`. Its internal
operator source request concerns the real approval rule in `docs/SECURITY.md`; it is not presented as
a social comment or audience demand. Approval calls use a simulated separate reviewer identity.
Fixture controls remain blocked and an appended instruction goes to HUMAN_REVIEW. The report records
zero messages sent and no external network operation. Current demonstration IDs:

- Community workflow: `e796e566-30a6-45e5-a3b9-696b5bd8c81f`.
- Operator-authored event: `95c6f0ad-bca7-4b39-babc-1876bf5bd79a`.
- Fresh reply awaiting the user's review: `b10ba6f4-b719-4e57-993d-0c73f31b49d6`.
- Fixture-blocked reply: `bbb70e72-4523-4711-9c99-7aabd68933c5`.
- Fresh image-preview workflow: `9f5b8c3f-6ead-4e85-bbaa-069a22c1c8b8`.

Use the report's current IDs after rerunning; acceptance never resets the development database.

## Local commands and remaining work

```bash
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py test
python scripts/dev.py check
python scripts/dev.py dev
python scripts/dev.py community-acceptance
```

The prepared Windows host uses `scripts/start-local.ps1` and the separate native PostgreSQL service.
See [COMMUNITY_TESTING.md](COMMUNITY_TESTING.md) for manual steps. Images remain under **Carousel
visual review**; [VISUAL_TESTING.md](VISUAL_TESTING.md) explains previews and approved ZIP export.

Live community work still needs an account, verified inbound events, permissions, token lifecycle,
abuse handling and an explicit sending policy. Conversion/consent-aware handoff, business audits and
reels are not implemented here. Existing Growie production services remain out of bounds. Cámara
permission, optional model activation, final character review and the previously documented durable
storage/deployment requirements remain separate dependencies.
