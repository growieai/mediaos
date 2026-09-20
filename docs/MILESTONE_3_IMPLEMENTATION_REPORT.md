# Milestone 3 implementation report

Branch: `milestone-3-visual-production`.

**Status: ready for local visual testing.** M3 adds a proposed character reference pack, deterministic carousel graphics, persisted visual QA, exact-render approval and checked ZIP export. This does not mark all remaining milestones complete. Sofía's appearance still requires human review; runtime image generation, integrated live text-model execution and social publishing are not complete.

M0/M1's content/evidence approval invariants remain in place. M2 official-source discovery remains available, with Cámara disabled pending permission. The revised milestone order and remaining external dependencies are recorded in [BUILD_PLAN.md](BUILD_PLAN.md).

## Implemented behavior

The current closed loop is:

```text
SourceSnapshot → ResearchPack → ContentBrief → CarouselDraft
→ content QA PASS → AWAITING_APPROVAL
→ deterministic render → visual QA PASS
→ human content approval → APPROVED
→ human approval of the exact render → checked ZIP export
```

Content can be rendered while awaiting content approval so that the reviewer can inspect it. Visual approval requires the exact content revision to have been approved first. Visual QA PASS does not create either human approval. Export does not publish anything.

The generic renderer uses versioned configuration, bundled regular/bold Inter fonts and Pillow 12.3.0 to produce 1080 × 1350 PNGs. It draws exact source-backed headline/body text, configured CTA, AI disclosure and display name, with an optional pinned character portrait. Caption text remains explicit publication metadata in the manifest and `caption.txt`.

Preflight checks cover exact text reconstruction, missing glyphs, unsafe control characters, contrast, fixed font-size limits, region overflow, image format/size and required disclosure. Overflow requires content revision; factual text is not silently truncated or shrunk. BLOCKED and REVISION_REQUIRED runs preserve their manifest/findings and produce no publishable PNGs.

The one-screen console now shows render history, authenticated PNG previews, the caption, visual findings, character-reference metadata, current artifact IDs and manifest hashes. It distinguishes content approval from visual approval, requires the reviewer to acknowledge inspecting the slides, and downloads an approved ZIP only after server validation. Historical previews remain labelled as historical when revisions or configuration change.

## Character reference and generation provenance

`characters/sofia/references/v1/` contains the proposed portrait, a reference sheet, generation prompts and metadata. The canonical portrait SHA-256 is:

```text
93255d07d569fd4c27c9f99f5d98bd3745462e10c3f5d8265a0a40ab95043572
```

These are imported design assets produced with the built-in image-generation tool. They represent an original fictional adult character. Their status is `PROPOSED_HUMAN_REVIEW_REQUIRED`; neither the generated reference sheet nor an automated acceptance call establishes final human approval of the identity.

The tool did not report its model ID, tokens or monetary cost. Metadata preserves those values as unknown, not zero. Each carousel uses the pinned portrait bytes; it does not regenerate Sofía's face. There is no application-side image-generation API integration in this slice.

## Schema, ownership and migration

Alembic **0003**, following the unchanged 0001/0002 migrations, adds:

| Entity | Purpose |
| --- | --- |
| `visual_config_versions` | Immutable tenant/influencer configuration revisions, payload hash, font hashes, optional portrait path/hash and reference metadata. Admin seed creates consecutive revisions. |
| `render_runs` | Tenant-scoped idempotent render requests pinned to exact workflow/content/research/content-QA/configuration revisions; status, manifest/hash, execution times and error category. A database sequence orders newer renders deterministically. |
| `visual_approval_records` | Immutable decisions linking an exact manifest/render to the exact content approval and approver identity. One decision per render. |

Every new business table forces tenant RLS. Runtime receives SELECT access to these tables, with writes restricted to guarded functions. Composite foreign keys enforce same-tenant workflow/artifact lineage and exact content-approval linkage. Configuration and approval revisions are immutable, including to routine administrative UPDATE/DELETE operations.

The public guarded functions are `start_render`, `claim_render`, `complete_render`, `fail_render`, `decide_render` and `check_render_export`. They use fixed search paths and explicit authenticated tenant/role checks. OPERATOR can render; APPROVER is required for visual decisions. Existing generic content approval remains authoritative for content.

## Approval and concurrency protection

Render completion validates the canonical manifest hash, pinned draft/configuration/font/reference hashes, exact text and fact references, ordered line spans, fixed placement bounds, numbered PNG filenames and the result derived from findings. A caller-supplied PASS cannot override a BLOCKED finding.

Approval and export require the latest passing render, the latest visual configuration, exact current content/research/QA revisions, an immutable matching content approval, and fresh content/source QA. A rejected visual decision cannot be changed to APPROVE. Changing content, adding a newer render or replacing the canonical visual configuration requires fresh visual review.

Workflow locks serialize render decisions with content revisions. The existing QA function also locks the bound Opportunity, preserving M2's source-change/freshness protection. Configuration creation and visual decisions serialize on the influencer row so that a newer visual policy cannot commit between checking the current version and approving/exporting it.

The service verifies actual PNG bytes, hashes, dimensions and format before committing approval and during preview/export. Missing or modified files prevent use. Export holds the workflow/source locks while it reads checked files and builds the response. PostgreSQL seals and validates the manifest and lineage; it does not independently inspect raster pixels.

## Storage, idempotency and recovery

PNG storage is private and separate from PostgreSQL, under the configured asset root, then tenant UUID/render UUID directories. The default is `.local/renders`. Content cannot choose filenames or arbitrary URLs. Fresh attempts write to unique staging directories; a completed directory is promoted atomically and committed files are never overwritten.

Both workflow creation and render creation have tenant-scoped idempotency. A matching key/payload returns the saved identity; a changed payload conflicts. A render-scoped advisory lock prevents concurrent execution. The database also serializes concurrent same-key creation and prevents direct runtime state/approval writes.

Render states are `CREATED → RENDERING → PASS | REVISION_REQUIRED | BLOCKED | FAILED`. An interrupted process can resume its persisted request. If files were promoted before the database checkpoint committed, resumption validates and reuses them. Orphan RUNNING skill attempts become INTERRUPTED; attempts are bounded by the existing setting. Deterministic failures and terminal/rejected renders require a new request rather than indefinite automatic retries. Failures before a skill starts also receive a persisted render failure status.

Setup and seed create the local render folder with the intended ownership. Docker's API bind mount uses `create_host_path: false`, preventing Docker from silently creating an unwritable root-owned asset directory. Local services remain loopback-bound and use standalone Media OS infrastructure.

## Skills, cost and model boundary

Each visual execution records a persisted `visual.render` SkillRun and CostEvent, with attempt metadata, latency, input hash and resulting manifest. The renderer records `provider=deterministic`, zero model tokens and zero model cost. Audit events record creation, execution/resumption, completion/failure, visual decisions and authorized export.

An isolated typed OpenAI selection adapter is included in `app/ai/structured.py`. It can choose only allowed fact IDs and approved creative-template IDs; deterministic assembly retains exact evidence text and the pinned CTA/disclosure. Tests cover schema validation, refusals, malformed output, request/response limits, safe errors and unknown usage/cost.

That adapter is **not wired into the persisted workflow** and no live OpenAI call is claimed. The application remains in the user's selected mock/deterministic mode. Persisted real attempts, pricing/unknown-cost semantics, explicit model/credential configuration and live acceptance are required before activation. [MODEL_ADAPTER.md](MODEL_ADAPTER.md) describes that boundary.

## Verification results

| Check | Observed result |
| --- | --- |
| Full backend suite after material review fixes | **188 passed**, including all prior M0/M1/M2 coverage and new rendering/model-boundary cases. |
| Backend lint/format/types | Final aggregate Ruff lint/format checks passed across 54 files; mypy passed across 43 modules, including the console-acceptance helper. |
| Frontend | Production build and TypeScript checks passed. |
| Browser inspection | The console sign-in screen rendered with the tenant prefilled; generated PNGs were visually inspected. Authenticated browser clicks were not exercised. The complete approval/download contract was exercised through the console proxy below. |
| Database | Migration from an empty disposable database through 0003 passed; the standalone development database was migrated and seeded. |
| In-process visual acceptance | Passed with persisted separate operator/approver API simulation, checked export, stale-revision denial and blocked-content denial. |
| Console-proxy HTTP acceptance | Current machine-readable report records **PASS / CONSOLE_PROXY_HTTP**, completed `2026-09-20T19:39:44.166963+00:00`. It exercised authenticated JSON, PNG and ZIP through the console proxy. |
| Hosted CI for the M3 branch | **Pending first push and run.** Earlier M2 CI does not verify these new changes. |

New tests exercise deterministic rendering, actual file hashes, newline coverage, overflow, missing disclosure, contrast/glyph checks, invalid references, immutable revisions, protected runtime writes, cross-tenant operations, exact approval linkage, rejection, stale configuration/content, concurrent idempotency, interruption recovery, tampered files and model selection boundaries. Normal tests do not require a live government site, AI account or publishing platform.

Codex `/review` ran via `codex review --uncommitted`. Its three material findings were fixed and regression-tested: local container asset-directory ownership; preservation of the pinned reference hash when image validation blocks; and preservation of explicit DISCLOSURE_MISMATCH/BLOCKED diagnostics rather than converting expected QA results into execution failures. The full 188-test suite passed after these fixes.

## Persisted demonstrations and review handoff

The actual M2 official-source workflow `b740e4b4-0162-44ed-8c1d-b8db6a35ded5` produced visual QA PASS render **`e86ba339-0f2e-44f3-97c8-80ff73358ed2`**, with the pinned portrait. Its PNG was inspected. It remains AWAITING_APPROVAL for the user's own review; no human approval or public publication is implied by the successful render. Live-source expiry still applies.

The repeatable visual acceptance uses the real internal policy statement “An OPERATOR cannot approve content.” from `docs/SECURITY.md`. It does not fabricate a grant or relabel a recorded fixture as live evidence. Its approval calls simulate separate human roles and are not the user's editorial sign-off.

Current console-proxy acceptance artifacts:

| Artifact | ID |
| --- | --- |
| Internal acceptance workflow | `63f5a9c4-a622-4918-ba09-98d314d28a4e` |
| Historical approved render | `bf3ec197-6a40-4663-ae11-511b183f2b83` |
| Simulated content approval | `7e87bcb0-5928-4017-be87-91b5d0ad0aaa` |
| Simulated visual approval | `3d5df81f-8c3b-4ad5-978e-607a0289cfac` |
| Fresh render awaiting the user's content and visual approvals | `a1486217-91ba-4bad-935d-9913bdc6acc0` |
| Unsupported-claim blocked workflow | `37277c2f-5309-450a-ad67-d04e6868d990` |

The harness retrieved 24 audit events, verified two persisted deterministic rendering attempts/cost records, rejected unauthorized tenant-context substitution, replayed idempotency keys, and rejected the old export after changing the content. The historical downloaded ZIP and first PNG are saved under `.local/visual-acceptance/`; the fresh render is the one to review now.

Machine-readable evidence is `.local/visual-acceptance-report.json`. It contains no credentials and may be replaced by a later successful run; use its `user_review` IDs when they differ from this report. [VISUAL_TESTING.md](VISUAL_TESTING.md) gives the one-screen review steps.

## Files changed

- `backend/app/rendering/`: typed configuration/manifest contracts, renderer, admin seed, authenticated service/routes, integrity checks and acceptance harness.
- `backend/migrations/versions/0003_visuals.py` and `0003_visuals.sql`: tenant-owned tables, exact lineage constraints, guarded visual states/approval/export and immutable configuration policy.
- `backend/assets/fonts/` and `characters/sofia/references/v1/`: pinned licensed fonts, proposed character assets, prompts and provenance metadata.
- `apps/console/app/VisualReview.tsx`, `page.tsx` and the internal proxy: current/history previews, approval controls and authenticated PNG/ZIP responses.
- `backend/app/ai/structured.py` and `test_structured_model_unit.py`: isolated typed selection adapter and mocked-transport validation, without runtime activation.
- `backend/tests/test_rendering_unit.py`, `test_visual_workflow.py` and migration-head assertion: rendering, persistence, tenancy, approval, recovery and failure regression coverage.
- Backend configuration, repository table discovery, app routes/seed and pinned dependency files: visual storage/configuration integration and Pillow dependency.
- `docker-compose.yml`, `scripts/dev.py` and CI workflow: owned local asset-directory setup, explicit bind mount and visual acceptance jobs.
- Architecture/data/workflow/security/local-development/roadmap documents, README, `CAMARA_ACCESS_REQUEST.md`, `MODEL_ADAPTER.md`, `VISUAL_TESTING.md` and this report: operation, scope, dependencies and verification evidence.

## Commands for local testing

For fresh setup, use the existing standalone development flow from the repository root:

```bash
python scripts/dev.py setup
python scripts/dev.py up
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py test
python scripts/dev.py check
python scripts/dev.py dev
```

The prepared Windows native setup can use `scripts/start-local.ps1`; Docker is not required to test that already provisioned native database. Do not substitute existing Growie production services or database credentials.

From `backend` on Windows, with migrations/seed complete:

```powershell
.venv/Scripts/python.exe -m app.rendering.acceptance
$env:ACCEPTANCE_API_URL='http://127.0.0.1:3000'
$env:ACCEPTANCE_VIA_CONSOLE='true'
.venv/Scripts/python.exe -m app.rendering.acceptance
Remove-Item Env:ACCEPTANCE_API_URL
Remove-Item Env:ACCEPTANCE_VIA_CONSOLE
```

Use `.venv/bin/python` on Linux/WSL. Keep `AI_MOCK_MODE=true`. Acceptance adds development records without resetting that database; normal pytest execution rebuilds only the disposable test database.

## Dependencies and deferred work

- **Sofía appearance:** review the proposed portrait and actual slides. A final canonical identity remains a human decision.
- **Cámara:** send the copyable request in [CAMARA_ACCESS_REQUEST.md](CAMARA_ACCESS_REQUEST.md) and supply the authorized API/feed or explicit access conditions. This does not block visual testing with existing sources.
- **OpenAI:** keep mock mode as selected. Do not paste credentials into the console or task. A future approved integration needs server-side secrets, explicit model access and verified persisted usage/cost handling.
- **Instagram:** account connection is deferred until the user provides it. No post has been sent; dispatch permissions, safe token storage, public HTTPS media delivery and real publishing acceptance remain required.
- **Production assets:** local private files prove the loop; S3-compatible durable storage, backup/recovery, retention and deployment operations remain outstanding.
- **Layout:** the initial fixed template/font pipeline targets Spanish/English Latin-script content. Complex-script shaping and RTL layout have not been verified. Human visual review remains required; there is no autonomous visual understanding or identity-fidelity guarantee.
- **Remaining milestones:** metrics, community replies, conversion handoff and reels are not complete. No social auto-publishing, video, voice, marketplace, external creator UI/signup or billing was activated. Existing Growie production infrastructure remains out of scope.
