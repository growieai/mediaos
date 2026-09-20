# Build Plan — First Influencer (roadmap reconciliation)

The user-defined milestones supersede the starter numbering below. **M2 is Spain Intelligence;
M3 is visual production.** The old ingestion/visual sections below are historical scope, not
additional completed milestones. M0/M1 remains frozen at `a021ac1`; M2 implementation and
verification are recorded in `MILESTONE_2_IMPLEMENTATION_REPORT.md`.

M3 is committed at `8827816` on `milestone-3-visual-production`, with passing hosted CI including Docker.
Current work continues on `milestone-6-delivery-preflight`: the original M6's internal delivery rehearsal,
without live account connection or public posting. This is partial M6, not a completed Instagram integration.
M3 adds a proposed versioned character pack,
deterministic 1080×1350 rendering, immutable private PNG files/manifests, visual QA, separate
guarded visual approval, and checked ZIP export. Its one-screen preview also delivers the
starter's Milestone 5 preview capability. Human review is required before treating Sofía's
proposed appearance as final. A built-in design-tool generation is not a verified runtime image API.

The remaining roadmap proceeds one verified loop at a time:

| Capability | Independent work | Live dependency / completion gate |
| --- | --- | --- |
| Typed model skills | Strict fact/template selection adapter and mock transport tests | User chose mock mode until credentials are configured. Persist real attempts/usage/cost semantics before enabling the adapter; no live API result is claimed. |
| Visual approval | Exact file preview, QA findings, human decision and private ZIP export; 188 tests and hosted Docker/console-proxy acceptance passed for M3 | User review of proposed character appearance; durable object storage is required for production. |
| Instagram (original M6) | Dry-run adapter, persisted intent, idempotency and reconciliation | User will connect an account later. Permissions, token storage, HTTPS media delivery and explicit dispatch authorization are required for a real post. |
| Metrics (M7) | Typed metric snapshots and content/post lineage | Actual connected post and verified insights access. Missing metrics remain unknown, never zero. |
| Community (M8) | Idempotent events, classifications and reviewed reply drafts | Webhooks, account permissions and explicit send authorization. Automatic comments/DMs remain disabled. |
| Conversion (M9) | Consent-aware requests, attribution and dry-run handoff contract | Explicitly chosen destination and credentials. Existing Growie production services/database remain out of bounds. |
| Reels (M10) | Begin only after carousel/source QA and measured metrics are stable | Final character selection, video provider, suitable voice rights, durable storage and audiovisual QA. A storyboard stub is not completed video production. |

Marketplace, external creator UI/signup and customer billing stay deferred. Production operations
also require organizational identity/token lifecycle, private infrastructure, TLS, secrets management,
backup/restore checks, alerting, rollback and durable orchestration. CI verifies only the commits
and flows that actually ran; no deployment to existing Growie services is authorized.

## Historical starter sequence

## Milestone 0 — repository can run
- Docker dependencies start.
- FastAPI `/v1/health` works.
- Console renders Sofía one-screen status.
- Unit tests pass.

## Milestone 1 — typed closed loop
- SourceInput
- ResearchPack
- ContentBrief
- CarouselDraft
- QAReport
- saved WorkflowRun

Acceptance:
- no publishable claim exists outside ResearchPack;
- QA can BLOCK;
- run is traceable end-to-end.

## Milestone 2 — real AI skills
Replace deterministic demo skill-by-skill, not all at once.

Order:
1. Creator skill with structured outputs.
2. Editor skill.
3. Research extractor.
4. QA reasoning.

Each replacement must retain deterministic validation around the model.

## Milestone 3 — real source ingestion
Start with 3-5 authoritative Spain sources, not the entire internet.
- fetch
- checksum/change detection
- store raw snapshot
- parse
- verify
- expire/recheck

## Milestone 4 — visual carousel render
- fixed templates
- Sofía reference lock
- headline/body constraints
- export 1080x1350
- artifact manifest

## Milestone 5 — approval console
One-screen modal flow:
- see what Sofía found
- inspect sources
- preview carousel
- approve/revise/block
- publish manually

## Milestone 6 — Instagram publishing
- OAuth/token storage
- dry-run
- publish one approved carousel
- record platform post id
- retry safely

## Milestone 7 — metrics loop
- fetch reach, saves, shares, comments, follows
- associate with content_id
- create learning object

## Milestone 8 — community loop
- classify comment
- retrieve verified context
- draft reply
- manual approval initially
- later auto-reply only for safe classes

## Milestone 9 — Growie conversion loop
- business identification
- business audit request
- Growie handoff
- attribution

## Milestone 10 — reels
Only after carousels + source QA + metrics are stable.
