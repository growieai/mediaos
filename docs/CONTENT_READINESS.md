# Content readiness

Creator Studio exposes an editorial checklist beside saved carousel content. A score is
**not a probability of going viral, predicted reach, a platform algorithm weight, or approval**.
The interface may describe the checklist as help with viral potential, but must keep the
"Content readiness" label and this limitation visible. A missing draft displays "Not scored".

`GET /v1/studio/workflow-runs/{workflow_id}/readiness` requires the same authenticated tenant
context as workflow artifacts. It returns the current content/research/QA IDs, latest render ID,
evaluation timestamp, nullable score, component reasons and suggestions, and a separate gate.
No model call, external request, workflow mutation, approval or metric observation occurs.

## Versioned heuristic

`content-readiness-v1` uses the following **application editorial choices**. The weights and
word thresholds are not attributed to Instagram or empirical engagement results.

| Component | Maximum | Exact rule |
| --- | ---: | --- |
| Opening hook | 15 | First headline: 1–12 words earns 15; 13–20 earns 9; over 20 earns 3; blank earns 0. This checks brevity, not semantic appeal. |
| Reading density | 15 | Maximum headline + body word count of any slide: at most 55 earns 15; 56–85 earns 8; over 85 earns 2. Any empty headline/body earns 0. |
| Slide structure | 10 | Consecutive indices and filled headline/body pairs earn 5; at least three such slides earns another 5. This is structural, not a judgement of narrative quality. |
| Call to action | 10 | Exact configured CTA with 1–16 words earns 10. An explicitly configured NONE CTA with no words also earns 10. A longer matching CTA earns 5; mismatch earns 0 and blocks. |
| Instagram portrait format | 10 | Current saved render has all draft slides at this renderer's fixed 1080 × 1350 (4:5). An absent or stale render is not scored. Other platform formats are not evaluated. |
| Evidence and QA | 20 | Verified allowed facts, exact supported excerpts, approved creative text, exact current QA PASS and freshly recomputed policy checks. Evidence failure earns 0 and blocks. |
| AI disclosure | 10 | Exact nonblank disclosure from the versioned character policy. Missing/mismatched disclosure earns 0 and blocks. |
| Visual checks | 10 | Current persisted visual QA PASS, no findings or overflow. Absent/stale renders are not scored. This does not replace human visual review or reverify files on disk. |

Word counts use whitespace-separated tokens. They are simple reading-density estimates, not
language-independent linguistic analysis. This initial checklist is intended for the current
text carousel renderer. It does not evaluate video, personality, originality, style quality,
the emotional quality of a hook, audience appeal, or likelihood of sharing.

The score is the sum of earned points out of 100. Missing checks earn no points and are shown
as `null` with `NOT_SCORED`, rather than pretending a failed check ran. The denominator is never
rescaled: a draft without a render can earn at most 80/100. `scored_max_points` exposes how much
of the checklist has actually been evaluated. With no saved draft the total is also `null`.

## Gate is independent of score

The pure scorer accepts typed Pydantic objects; the route loads them through the existing
RLS-protected repository using a repeatable-read transaction. It calls the existing
`qa_findings` database policy to recompute evidence, expiry, fixture, opportunity conflict and
revision findings at evaluation time. This function may lock an opportunity while checking it;
it does not create a QA report or change workflow state. Historical BLOCK on the same content
revision remains a veto. New revisions retain their own checks and approvals.

The gates, in precedence order, are:

1. `BLOCKED`: unsupported or stale evidence/content, missing disclosure, blocked content or
   visual QA, mismatched QA/render revision or a superseded visual configuration.
2. `REVISION_REQUIRED`: persisted or recomputed content/visual revision findings.
3. `NOT_READY`: no content, incomplete workflow, missing current QA, unavailable policy check,
   or missing current render.
4. `REQUIRES_APPROVAL_CHECKS`: this checklist has run. The existing database-protected human,
   visual and exact social dispatch approvals must still succeed independently.

A high score may coexist with `BLOCKED`. The UI must show the gate prominently and never use
the score to enable approval or publishing. The response always includes
`grants_publication_permission: false`. It is an informational snapshot and can become stale
immediately after the response; dispatch rechecks its own current locks, revisions and approvals.

The endpoint does not accept caller-provided PASS, arbitrary fact references, a replacement
score, or fake engagement. Unsupported statements cannot be hidden in CREATIVE blocks, which
must match the configured non-factual allowlist. Existing QA remains the authoritative policy.

## Measurement stays separate

Actual verified platform observations belong in the connected insights/learning history.
Manual and fixture observations keep their existing provenance labels. This checklist does not
turn any of those observations into a virality estimate or automatically optimize policy.

For background, see Meta's own
[Instagram Feed ranking system card](https://ai.meta.com/tools/system-cards/instagram-feed-ranking/).
The app's checklist is not an implementation or calibration of that ranking system.

## Verification

`backend/tests/test_content_readiness.py` is offline and requires no PostgreSQL or credentials.
It checks absent content, truthful score/permission semantics, policy BLOCK and revision
separation, stale content/research/QA/render versions, source freshness/conflict vetoes,
missing renders, unsupported or fabricated evidence, disclosure, monotonic reading-density
and hook checks, strict response schemas, and deterministic exact-revision output.

```powershell
cd backend
.venv/Scripts/python.exe -m pytest tests/test_content_readiness.py -q --noconftest -p no:cacheprovider
```

The normal PostgreSQL API integration suite separately verifies endpoint authentication and
tenant isolation. No schema migration is introduced by readiness scoring.
