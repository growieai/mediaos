# Build Plan — First Influencer (roadmap reconciliation)

The `milestone-completion` branch includes M9 consent-aware manual handoff, the persisted M10
speaking-video path, opt-in real text execution, and connected Instagram publishing, platform
metrics and comment/reply paths. These paths have separate persisted authorization and remain
disabled until configured. It does not mark unperformed live acceptance complete. See
[SPEAKING_VIDEO.md](SPEAKING_VIDEO.md), [SOCIAL_INTEGRATION.md](SOCIAL_INTEGRATION.md),
[REAL_MODEL_EXECUTION.md](REAL_MODEL_EXECUTION.md), and the actual verification/dependency record
in [MILESTONE_COMPLETION_REPORT.md](MILESTONE_COMPLETION_REPORT.md).
No credential, paid policy or externally sent message is seeded automatically.

The user-defined milestones supersede the starter numbering below. **M2 is Spain Intelligence;
M3 is visual production.** The old ingestion/visual sections below are historical scope, not
additional completed milestones. M0/M1 remains frozen at `a021ac1`; M2 implementation and
verification are recorded in `MILESTONE_2_IMPLEMENTATION_REPORT.md`.

M3 is committed at `8827816` on `milestone-3-visual-production`, with passing hosted CI including Docker.
The original M6's internal delivery rehearsal is published on `milestone-6-delivery-preflight`,
with passing hosted CI through `a45c29a`. It has no live account connection or public posting;
this is partial M6, not a completed Instagram integration. The
`milestone-7-metrics-foundation` branch adds manual/fixture observations and descriptive comparisons.
Its implementation `8f24688` passed 308 tests and hosted CI, including Docker and console acceptance.
It is partial M7; verified platform insights remain dependent on an account and actual posts.
The `milestone-8-community-review` foundation adds internal manual comment classification,
evidence-backed reply drafts, QA and separate human review. It has no inbound social integration
or sending capability in that historical foundation. The current branch adds the connected bridge
and separate dispatch authorization; live community acceptance still requires an account and a real
signed inbound event followed by an explicitly authorized reply.
M3 adds a proposed versioned character pack,
deterministic 1080×1350 rendering, immutable private PNG files/manifests, visual QA, separate
guarded visual approval, and checked ZIP export. Its one-screen preview also delivers the
starter's Milestone 5 preview capability. Human review is required before treating Sofía's
proposed appearance as final. A built-in design-tool generation is not a verified runtime image API.

The remaining roadmap proceeds one verified loop at a time:

| Capability | Independent work | Live dependency / completion gate |
| --- | --- | --- |
| Typed model skills | Strict fact/template selection runtime, persisted reservations/attempts/usage, explicit tenant caps and typed tests | User chose mock mode until credentials are configured; real-call acceptance remains pending. |
| Visual approval | Exact file preview, QA findings, human decision and private ZIP export; 188 tests and hosted Docker/console-proxy acceptance passed for M3 | User review of proposed character appearance; durable object storage is required for production. |
| Instagram (original M6) | Separate connected-account OAuth/token vault, exact JPEG plan review, guarded dispatch, durable attempts/receipts and reconciliation | User will connect an account later; permissions, public HTTPS media access and an explicit real post are needed for live acceptance. |
| Metrics (M7) | Immutable manual/fixture and distinct verified-platform snapshots, exact post lineage and descriptive comparisons | Actual connected post and two real insights observations. Missing metrics remain unknown; manual reports never become platform-verified data. |
| Community (M8) | Signed comment bridge, classifications/reviewed drafts and separately authorized exact reply dispatch | Real signed inbound webhook, account permissions and explicit real reply. Automatic comments/DMs remain disabled. |
| Conversion (M9) | Consent-aware versioned requests, attribution, revocation and guarded manual export | Actual consent, chosen destination and confirmed external handoff. Existing Growie production services/database remain out of bounds. |
| Reels (M10) | Persisted ElevenLabs/HeyGen speaking-video workflow, exact captions/fact-card cutaways, bounded spend and separate review; Sara Martin selection recorded | Provider accounts and verified prices, authorized budget, actual generation and human audiovisual review. Higgsfield is an optional later illustrative-cutaway path; its transport adapter alone is not a completed generation workflow. |

Marketplace, external creator UI/signup and customer billing stay deferred. Production operations
also require organizational identity/token lifecycle, private infrastructure, TLS, secrets management,
backup/restore checks, alerting, rollback and durable orchestration. CI verifies only the commits
and flows that actually ran; no deployment to existing Growie services is authorized.

The user's video-direction request adds a separate `video-concept-preview` branch: a local silent
MP4 motion study with Sofía's proposed portrait, captions and cutaways. The chosen final direction
is a speaking presenter. This prototype does not enable the runtime video flag or complete M10;
see `VIDEO_PREVIEW.md` for the actual output and remaining speech/provider dependencies.

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
