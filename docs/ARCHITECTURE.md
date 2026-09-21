# Architecture — Milestones 0/1

The runtime is a generic typed workflow engine, separate from Codex. The internal Next.js console proxies authenticated requests to FastAPI. SQLAlchemy handles PostgreSQL transactions; Alembic owns schema changes. PostgreSQL is the sole source of truth.

The source is submitted manually. A separate authorized reviewer attests that the exact source/evidence is trustworthy. Labels such as OFFICIAL never confer trust automatically. Fixture sources cannot be attested and QA blocks them.

## Current topology

```text
Internal console → FastAPI → restricted PostgreSQL role
                       ↓
                 synchronous runner
                       ↓
research.extract → research.verify → editor.build_brief → content.carousel → qa.validate
```

All five foundation skills have typed inputs/outputs and persisted attempts. MockAdapter validates strict Pydantic schemas. Live provider execution remains disabled pending configured credentials and persisted usage/cost integration. Local mock operation has no provider credential dependency.

Persona, voice, visual policy, brand policy, language, editorial settings and safe non-factual templates are versioned configuration. Markdown under characters/sofia remains canonical source material; seed imports it with runtime.json into immutable character and influencer versions. A changed content hash creates a new version. Existing runs retain their original configuration.

## Deliberate boundaries

The synchronous runner uses an advisory execution lock and short checkpoint transactions. Approval and revision use a shared workflow row lock. These boundaries can become Temporal activities later. PostgreSQL supplies the required locking; Redis is not required locally. M3 adds a private local filesystem boundary for rendered binaries; production needs durable S3-compatible storage. Temporal and Redis remain later operational dependencies.

No external creator UI, signup, marketplace, video, publishing, community automation or billing is implemented. Official ingestion is described below. M3 imports a generated character reference pack and renders fixed carousel templates; it does not expose a live image-generation API. Startup rejects unsupported feature flags or real AI mode.

## Production deployment still deferred

Production readiness requires an EU deployment decision, managed PostgreSQL, TLS ingress, an organizational identity/token lifecycle, service-secret distribution and rotation, backup/restore exercises, release/rollback procedures, centralized logs/alerts, resource/concurrency limits, and durable orchestration before unattended execution.

Docker Compose is local development configuration, not a production manifest. It binds ports to localhost. Keep database infrastructure private in production. The privileged migration identity must never be available to API/console workloads.

No connection to Growie production infrastructure is needed or permitted by this setup.

## Milestone 2: source intelligence

A generic SourceConnector interface separates discovery and normalization from tenant-scoped orchestration. SourceDefinition supplies authority, trust, parser version, enabled/access policy, polling limits and normalization preference. Country-specific code stays in connectors and seed data. No generic workflow branches on Sofía, Growie or Spain.

The synchronous IngestionRunner commits its request, HTTP attempts, exact raw response/RAW SourceSnapshot, normalized projection, opportunity/evidence/version and cursor checkpoints independently. It can resume an interrupted page at its saved document offset. A dedicated INGESTOR principal performs official capture; an operator can request execution but cannot mint official attestations.

A live-source workflow binds an immutable Opportunity version and editorial decision to the existing M1 workflow. Its ResearchPack carries typed opportunity context and exact allowed facts. The M1 factual-text policy remains strict: claims must equal their referenced evidence excerpts; free factual paraphrasing is not accepted. Content is a structured excerpt carousel with the configured character voice, disclosure and CTA. This implementation makes no LLM calls and does not claim semantic model reasoning.

Only BDNS and BOE public APIs are enabled. Cámara's parser is available for recorded documents; its live connector fails closed pending source permission. There is no production scheduler, external creator surface or social publishing.

## M3: deterministic visual production

The renderer accepts only a persisted CarouselDraft and an immutable admin-seeded visual configuration.
It preserves exact text and fact references, with fixed 1080×1350 regions, pinned Inter font bytes,
mandatory disclosure, minimum font sizes and contrast checks. Overflow produces REVISION_REQUIRED;
unsafe/missing glyphs or disclosure produce BLOCKED. Failed visual QA emits no PNGs. Caption text
remains exact publication metadata rather than being silently added to the artwork.

Rendering has its own persisted state and SkillRun; it does not mutate the original content state
machine. Each request pins content/research/QA/configuration and character-reference hashes.
The service writes a temporary private directory, then atomically renames it before committing the
manifest. A process restart can validate those bytes and resume the database checkpoint.

Content approval remains the original M1 transition. Visual approval is an additional immutable
record, covering the exact manifest and underlying content approval. PostgreSQL rechecks source
freshness, current revisions, latest visual configuration and latest render. It validates text
coverage and manifest hashes; the server verifies actual image bytes and dimensions. PostgreSQL
does not inspect raster pixels. Human review remains necessary for appearance and semantic visual quality.

Authenticated endpoints deliver PNG previews and a guarded ZIP export. No public media URLs exist.
The console fetches binary previews using bearer headers and revokes temporary browser object URLs.
Export revalidates content and visual approvals under the same locks used for revisions and source
changes. A future social adapter must repeat this guard at dispatch; a ZIP download is not publishing.

## Internal delivery preflight (partial M6)

A generic delivery adapter consumes the checked render package behind the existing export guard.
The only implementation is `dry-run-v1`; it constructs no HTTP client and records no external post.
The guard holds the current workflow/source/configuration locks while the service verifies PNG bytes,
constructs an exact typed plan, validates the receipt and commits it. PostgreSQL separately checks
the plan against the pinned manifest and approval records. The receipt records a historical internal
check, not Instagram compatibility or future permission to publish.

Delivery state and attempts are persisted separately. WorkflowRun remains APPROVED; a simulation
cannot advance it to a publishing state. Future live delivery requires a separate reviewed extension
with durable dispatch intent, unknown-outcome reconciliation, account permissions and secret storage.
Never convert a saved dry-run row into a live delivery or blindly retry an uncertain external post.

## Historical metrics foundation (partial M7)

Authenticated operators can associate manual observations or explicitly synthetic fixtures with an
exact historically approved carousel render. MANUAL observations are SELF_REPORTED; an asserted
external reference is not proof of a platform post. FIXTURE observations have no external reference.
No account, insights API, scheduled collection or live performance verification is connected.

The metrics service preserves immutable input payloads, supplied evidence text and canonical hashes.
PostgreSQL computes a descriptive comparison of two observations of the same subject, with matching
scope and definitions and strictly increasing UTC observation times. Unknown values remain null;
negative deltas remain visible as possible reporting corrections. The typed service independently
checks the SQL result. Comparisons make no causal claim and never modify editorial policy.

Historical content and visual approvals establish the association without rerunning today's export
freshness guard. New revisions or expired evidence do not erase historical measurements. Metrics
cannot approve content, authorize export, infer that a dry run published anything, or change workflow
state. Real platform collection and any reviewed policy-learning loop remain future work.
