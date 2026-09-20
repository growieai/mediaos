# Milestone 2 implementation report

Branch: `milestone-2-spain-intelligence`. Baseline: `a021ac1`, frozen as **M0/M1 — Code Complete / Environment Verification Pending**.

The live BDNS-to-Sofía pipeline has been demonstrated through protected approval. Full M2 sign-off is not claimed while Cámara automated access remains disabled pending permission. Docker and hosted CI are operational follow-ups and are not represented as run locally.

## Implementation

- Added the generic connector interface and source registry, with BDNS/SNPSAP public API discovery/detail fetching, BOE daily-summary/XML discovery and authoritative BDNS cross-references, and recorded Cámara listing/programme parsing.
- Added tenant-scoped, idempotent ingestion with persisted page/document checkpoints, immutable HTTP evidence, raw-before-normalization SourceSnapshots, source observations, bounded retry/backoff, URL allowlists, response-size limits, cache and sequential source access.
- Added generic Opportunity roots and immutable versions, source links, stable fact IDs and raw locators, structured grant profiles with UNKNOWN values, deterministic change events, unresolved source conflicts and non-merging duplicate candidates.
- Added seven seeded audience segments, separate eligibility and weighted relevance components, mission policies, content-history-aware editorial decisions and typed opportunity ResearchPack context.
- Reused the existing WorkflowRun, five typed skill boundaries, content revisions, deterministic QA and human approval path. Live rule execution records provider=deterministic with zero tokens/cost; legacy mock mode remains provider=mock. No LLM call, inferred grant entitlement, image rendering, scheduler or publishing is included.
- Extended the internal one-screen console with discovery, evidence/version inspection, scoring and draft creation. Existing exact-revision approval/rejection remains the final gate.

## Schema and migrations

Alembic 0002 follows the untouched frozen 0001 schema/guards. New tables: source_definitions, ingestion_runs, ingestion_attempts, raw_source_documents, source_observations, opportunities, opportunity_versions, source_links, opportunity_facts, verification_conflicts, change_events, duplicate_candidates, audience_segments, mission_editorial_policies, relevance_scores, editorial_decisions and workflow_opportunities.

SourceSnapshot now supports RAW and NORMALIZED ingestion records outside a workflow, with parent lineage. RAW snapshots preserve the exact response before parsing; a normalization failure still leaves evidence. Workflow copies remain pinned to a source and the original M1 relational fact memberships. Manual input limits are unchanged. All new business tables have forced tenant RLS and ownership foreign keys.

## Approval and tenant protection

INGESTOR is a dedicated server-side principal with the same restricted SQL runtime role. Only that capability can create/attest official captures; ordinary OPERATOR credentials cannot fabricate verified source rows. Migration/admin privileges are not used by request processing.

M1's QA function is retained in full and wrapped with opportunity freshness, current-version, deadline, fixture and conflict checks. A separate approval-record trigger repeats the live-evidence gate. Approval and ingestion serialize on the opportunity row. Evidence expiry is capped in PostgreSQL from immutable fetch observations and mission policy; a fabricated score expiry cannot extend it. Historical approval is never transferred to a changed asset or opportunity.

## Evidence and deduplication

A claim traces through ContentAsset claim → allowed brief/research fact → workflow SourceSnapshot → normalized official snapshot → exact RAW SourceSnapshot/raw response, plus the opportunity fact's raw JSON/XML/HTML locator. Downstream factual text remains an exact evidence excerpt. The first BDNS implementation does not infer relative dates, individual eligibility, per-applicant amounts from programme budget, or YES from a missing exclusion.

Canonical external IDs are tenant-unique. Explicit unambiguous BDNS references link BOE documents to the same root. Similar titles produce POSSIBLE_DUPLICATE only. Same-source changes, including a reversion to earlier content, retain original snapshots and create new versions. Conflicting known official fields retain both sides and block approval; no silent priority-based conflict resolution occurs.

## Idempotency, retries and resumption

Tenant/idempotency-key constraints plus canonical request hashes return the existing run for the same request and conflict for different payloads. Ingestion persists pending page, next cursor and committed document offset. It resumes from the checkpoint after interruption. HTTP retries are bounded at three, with exponential/Retry-After metadata; long source backoff stops the cycle. Host cooldowns persist in a private infrastructure table and apply across request keys and tenants. Only an INGESTOR with that registered host can read or extend its cooldown; runtime code cannot shorten it. Source locks and unique artifact constraints prevent duplicate committed work. The existing persisted M1 skill attempts/retries remain intact.

## Verification results

Final PostgreSQL suite: **102 passed** (all 60 M0/M1 tests plus 42 M2 cases), in 74.33 seconds. The suite rebuilt the disposable database from empty public/private schemas and applied migrations through 0002 using the restricted runtime role for application tests. One upstream Starlette/AnyIO deprecation warning remains.

Backend Ruff lint and formatting passed across app, tests and migrations (42 files); mypy passed (35 source files). Next.js 16.3.5 production build and TypeScript checks passed. The standalone development database was upgraded and seeded successfully, preserving existing M1 data. The persisted M1 acceptance simulation also passed again.

Codex `/review` was executed with `codex review --uncommitted`. All three material findings were fixed and covered by regression tests: unresolved letter/numeric NACE hierarchy stays UNKNOWN; malformed BOE XML records an extraction failure and advances the document checkpoint; government-host backoff persists across new keys and tenants. The complete suite and backend checks were rerun after these fixes. The conflict fixture now changes raw evidence before parsing, rather than changing parsed claims independently of their source.

Live acceptance on 2026-09-20 fetched one BDNS page and five details. It selected `ES:BDNS:929780`, generated and persisted research, brief and carousel, passed QA, entered AWAITING_APPROVAL, denied operator approval with 403 and accepted the dedicated approver with an immutable ApprovalRecord. It retrieved 12 audit events, replayed the same workflow key and rejected another tenant's access with 404.

The opt-in harness also used an isolated verification tenant for an unsupported factual claim, a controlled deadline revision, and conflicting controlled BDNS/BOE fixtures. QA returned BLOCKED, stale approval returned 409, and source-conflict findings prevented approval. These controlled changes were explicitly marked fixtures; they were not represented as an actual disagreement between agencies. Approver API calls simulate a human review and are not a person's real editorial sign-off.

Machine-readable evidence: `.local/milestone-2-acceptance.json` (ignored by Git). Final live run completed at **2026-09-20T17:53:48Z** in standalone `mediaos_dev`, with zero extraction failures, retries, model calls, tokens or model cost. Three similar-title candidates were recorded without automatic merging.

| Persisted artifact | ID |
| --- | --- |
| Growie tenant | `e7cd3e53-c329-5066-a3d1-67ac2c3e72cb` |
| Live ingestion | `7bc2f502-b5ff-4ba6-8e0b-6d32098cf68f` |
| Approved workflow | `b740e4b4-0162-44ed-8c1d-b8db6a35ded5` |
| Opportunity | `ec8ed7f0-5dd8-4ca3-9772-84b74b17140e` |
| Opportunity revision | `f63da954-5d09-44f1-9d7e-d48acf3d6804` |
| Raw SourceSnapshot | `bc934240-79dd-426b-944f-6f957ce39cc2` |
| Normalized SourceSnapshot | `2c0bbd5a-9ec1-4395-8c82-544b925eb37b` |
| ResearchPack revision | `aabef067-f994-4495-9b1e-7fae6cec814d` |
| ContentAsset revision | `6a2c26ba-348d-402f-b746-5c604cf589cc` |
| Passing QAReport | `709f279b-75d5-4d3c-8e54-a996084e4a32` |
| Unsupported-claim blocked workflow | `8acb9558-9577-494a-96a3-10711b6e6622` |
| Stale/conflict blocked workflow | `98734e89-9412-47f8-8c29-44393dbbb384` |

Raw SHA-256: `12b0e9c88086c0432881e7c293ce2155ed534d0f4110d1736afcb3a30832dc1a`. The controlled second source version produced DEADLINE_CHANGED and DOCUMENT_CHANGED events; the conflicting BOE fixture produced an OFFICIAL_SOURCE_CONFLICT finding and BLOCKED QA. Neither control altered the approved Growie source.

Live BOE ingestion of the 2026-09-18 issue also ran in `mediaos_dev`: run `20eedca7-db51-4191-bf02-62fafc1283ac`, one summary page, three fetched documents, two normalized opportunities, zero retries/model cost. BOE-B-2026-30165 and BOE-B-2026-30166 resolved to BDNS 929820 and 929846 respectively. BOE-A-2026-19422 contained 114 extracted paragraphs and exceeded the typed 100-fact limit: its raw snapshot was preserved, extraction failure counted, and remaining documents processed. No truncated legal content was approved. This is a normalization limit, not a claim that every discovered official document is understood. CLI evidence is saved in `.local/boe-live-ingestion.json`.

## Files changed

- `backend/app/intelligence/`: typed schemas, three connector parsers, bounded HTTP transport, persistent ingestion, deterministic verification/scoring/editorial policy, seed, internal routes, CLI and live acceptance harness.
- `backend/migrations/versions/0002_intelligence.py`, `0002_intelligence.sql`, `0002_guards.sql`: 17 tenant-owned business tables, private host-cooldown infrastructure, source lineage, RLS and guarded live approval checks. Frozen 0001 files are unchanged.
- `backend/app/models/schemas.py`, `services/workflows.py`, `skills/editor.py`, `ai/client.py`, `db/repository.py`: optional typed opportunity research context, selected audience, existing skill/workflow reuse and explicit zero-cost deterministic execution.
- `backend/app/api/routes.py`, `main.py`, `config.py`, `observability.py`, `seed.py`: readiness, authenticated intelligence routes, server-only ingestion credentials and structured operational correlation.
- `backend/tests/test_intelligence.py`, `tests/fixtures/official/`: deterministic M2 regression coverage and seven attributed, checksummed official recordings. `conftest.py` adds a bounded test connection timeout; `test_invariants.py` expects migration head 0002.
- `apps/console/app/page.tsx`: minimal internal source/opportunity inspection, scoring and draft actions in the existing one-screen console.
- `.env.example`, `.gitattributes`, `docker-compose.yml`, `.github/workflows/ci.yml`: credential injection, byte-preserved fixtures, restricted container user/mount and M2 CI scope.
- `README.md`, `docs/ARCHITECTURE.md`, `DATA_MODEL.md`, `WORKFLOWS.md`, `SECURITY.md`, `LOCAL_DEVELOPMENT.md`, `IMPLEMENTATION_REPORT.md` and this report: implementation, operation, invariants and honest verification status.

## Workflow state machine

Ingestion uses CREATED → RUNNING → SUCCEEDED, with persisted FAILED and retry timestamps/checkpoints. Normalization errors advance the document checkpoint with an extraction-failure count; transport failures preserve the checkpoint for resumption.

The M1 state machine remains SOURCE_CAPTURED → RESEARCHING → RESEARCH_COMPLETE → BRIEFING → BRIEF_COMPLETE → CONTENT_GENERATING → CONTENT_COMPLETE → QA_RUNNING → AWAITING_APPROVAL → APPROVED, following initial CREATED. REVISION_REQUIRED, BLOCKED and FAILED remain distinct. QA PASS cannot directly approve. Every workflow transition and skill attempt remains persisted; new content revisions and stale opportunity evidence require fresh QA and approval.

## Known limitations and deferred work

- Cámara's published conditions prohibit automation. Its parser is tested against attributed official recordings, but live discovery is disabled pending permission. The requested three-live-source scope therefore remains partially blocked.
- Extraction and scoring are deterministic. Unknown legal conditions, relative dates, unparsed PDFs and incomplete eligibility require human review. Documents exceeding the typed 100-fact limit are retained as raw evidence with a recorded extraction failure. Structured semantic/model extraction is not required for the demonstrated official structured fields and is not implemented.
- Conflicts are immutable and unresolved until a future explicit review/resolution mechanism. There is no automatic merge or conflict dismissal.
- Mission policies/source definitions are maintenance-seeded data. There is no public creator/configuration UI or production scheduler. Government Retry-After cooldowns are shared in PostgreSQL. Minimum request spacing and concurrency locks are process-local; a distributed production worker fleet would also need shared request-slot allocation.
- Docker is unavailable on this host; local container execution remains unverified. Hosted CI has not run because the repository has no configured remote. An empty public `growieai/mediaos` repository was found, and destination/publication confirmation is pending before source is pushed. Deployment hardening, backup/restore exercises, secret rotation and production service rollout remain deferred.
- M3 visual identity/reference packs, rendering, image generation and visual QA remain untouched. Video, social publishing, comments/DMs, audit products, marketplace, external signup/creator UI and billing remain deferred.

## Run locally

```bash
python scripts/dev.py setup
python scripts/dev.py up
python scripts/dev.py migrate
python scripts/dev.py seed
python scripts/dev.py test
python scripts/dev.py check
python scripts/dev.py dev
```

From backend, using `.venv/bin/python` on WSL/Linux or `.venv/Scripts/python.exe` on Windows:

```bash
.venv/Scripts/python.exe -m app.intelligence.cli ingest --source BDNS --query PYME
.venv/Scripts/python.exe -m app.intelligence.cli draft
.venv/Scripts/python.exe -m app.intelligence.acceptance
```

Normal tests do not access government sites. The acceptance command explicitly does, and fails if no current official opportunity qualifies.

## Official interfaces inspected

- [SNPSAP official OpenAPI specification](https://www.infosubvenciones.es/bdnstrans/estaticos/doc/snpsap-api.json)
- [BOE OpenData API](https://www.boe.es/datosabiertos/api/api.php)
- [BDNS record used for the live demonstration](https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=929780)
- [Cámara electronic-office access conditions](https://sede.camara.es/sede/html/titularidad)

Recorded fixture URLs, capture times and SHA-256 values are in backend/tests/fixtures/official/manifest.json. Git preserves their bytes across platforms.
