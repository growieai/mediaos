# Architecture — Growie Media OS

## Reviewed handoff and standalone pilot

Migration 0016 extends manual conversion export with versioned business identity evidence,
exact destination transports, separate human outbound decisions and signed receipt history.
The endpoint and verification key are provisioned with the migration identity; the API cannot
invent destinations or fabricate recipient receipts. Execution is disabled by default. A
committed attempt precedes network work, and consent/destination/identity locks cover final
validation and transmission. Uncertain POSTs are never replayed. See
[conversion delivery](CONVERSION_DELIVERY.md) for the explicit recipient protocol and limits.

Migration 0017 requires media prices checked within 30 days before each paid reservation,
while retaining free checkpoint recovery. Failed attempts keep sanitized provider correlation
IDs. The administrator console can version a voice profile and explicit spending policy;
configuration does not execute a provider call.

The [standalone pilot scaffold](STANDALONE_DEPLOYMENT.md) separates runtime and maintenance
credentials, private persistence and TLS ingress. It does not deploy a host or implement
multi-host orchestration. Local physical erasure of conversion records remains a separate
requirement; expiry blocks use rather than deleting immutable history.

## Connected providers and operations

Migrations 0009–0015 extend the frozen content, evidence and approval foundation. The optional
OpenAI creator selects only configured text and allowed evidence through a strict typed schema;
its attempts, explicit tenant budget reservations and usage are persisted. Mock mode remains the
default. See [real model execution](REAL_MODEL_EXECUTION.md).

Instagram has a separate connected-account boundary with encrypted tokens, a restricted SOCIAL
principal, purpose-bound OAuth state and exact JPEG/caption/account review. A content approval or
dry-run receipt never authorizes a public post. The final bounded publish call holds the existing
content/source/configuration locks; persisted receipts can recover a database interruption without
reposting. Uncertain writes stop in UNKNOWN_OUTCOME. Signed comments can enter the existing
community review flow; a separate decision authorizes the exact reply. Verified platform insight
snapshots and descriptive comparisons retain their own provenance. See
[social integration](SOCIAL_INTEGRATION.md) and [platform learning](PLATFORM_LEARNING.md).

These paths are manually invoked and disabled by default. Local fixture tests do not establish live
provider acceptance. Production scheduling, organizational identity and durable object storage
remain deployment work. [Operations](OPERATIONS.md) describes bounded standalone backup/restore.

## Current media and conversion additions

Migrations 0007/0008 add isolated media and consent/handoff services; see
[speaking video](SPEAKING_VIDEO.md) and [conversion](CONVERSION_TESTING.md). Provider transport
adapters do not own state or approval. Media jobs commit an exact input, attempt and budget
reservation before network work, then checkpoint validated output in a separate transaction.
The local compositor keeps bytes private and reuses verified speech for final audio. Content
approval and perceptual video approval remain separate. Default text generation stays mock;
media execution is disabled unless its independent flag, credentials and budget are configured.

Conversion is a consent-reviewed manual export, not a call to an existing Growie service.
Its historical content attribution does not confer consent or imply audit completion.

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

All five foundation skills have typed inputs/outputs and persisted attempts. MockAdapter validates strict Pydantic schemas. Real creator execution requires an explicit unexpired tenant policy and configured credentials. Local mock operation has no provider credential dependency.

Persona, voice, visual policy, brand policy, language, editorial settings and safe non-factual templates are versioned configuration. Markdown under characters/sofia remains canonical source material; seed imports it with runtime.json into immutable character and influencer versions. A changed content hash creates a new version. Existing runs retain their original configuration.

## Deliberate boundaries

The synchronous runner uses an advisory execution lock and short checkpoint transactions. Approval and revision use a shared workflow row lock. These boundaries can become Temporal activities later. PostgreSQL supplies the required locking; Redis is not required locally. M3 adds a private local filesystem boundary for rendered binaries; production needs durable S3-compatible storage. Temporal and Redis remain later operational dependencies.

External creator UI, signup, marketplace, automatic publishing/replies and billing remain disabled. Manual video/social paths are described above. Official ingestion is described below. M3 imports a generated character reference pack and renders fixed carousel templates; it does not expose a live image-generation API. Startup rejects unsupported automatic feature flags and real text mode without a credential.

## Production deployment still deferred

Production readiness requires an EU deployment decision, managed PostgreSQL, TLS ingress, an organizational identity/token lifecycle, service-secret distribution and rotation, backup/restore exercises, release/rollback procedures, centralized logs/alerts, resource/concurrency limits, and durable orchestration before unattended execution.

Docker Compose is local development configuration, not a production manifest. It binds ports to localhost. Keep database infrastructure private in production. The privileged migration identity must never be available to API/console workloads.

No connection to Growie production infrastructure is needed or permitted by this setup.

## Milestone 2: source intelligence

A generic SourceConnector interface separates discovery and normalization from tenant-scoped orchestration. SourceDefinition supplies authority, trust, parser version, enabled/access policy, polling limits and normalization preference. Country-specific code stays in connectors and seed data. No generic workflow branches on Sofía, Growie or Spain.

The synchronous IngestionRunner commits its request, HTTP attempts, exact raw response/RAW SourceSnapshot, normalized projection, opportunity/evidence/version and cursor checkpoints independently. It can resume an interrupted page at its saved document offset. A dedicated INGESTOR principal performs official capture; an operator can request execution but cannot mint official attestations.

A live-source workflow binds an immutable Opportunity version and editorial decision to the existing M1 workflow. Its ResearchPack carries typed opportunity context and exact allowed facts. The M1 factual-text policy remains strict: claims must equal their referenced evidence excerpts; free factual paraphrasing is not accepted. Content is a structured excerpt carousel with the configured character voice, disclosure and CTA. This implementation makes no LLM calls and does not claim semantic model reasoning.

Only BDNS and BOE public APIs are enabled. Cámara's parser is available for recorded documents; its live connector fails closed pending source permission. There is no production scheduler or external creator surface. Manual social dispatch has an independent approval boundary.

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

Authenticated endpoints deliver PNG previews and a guarded ZIP export. Public JPEG capabilities exist only for separately authorized social plans, expire quickly and cannot expose arbitrary files.
The console fetches binary previews using bearer headers and revokes temporary browser object URLs.
Export revalidates content and visual approvals under the same locks used for revisions and source
changes. The social service repeats this guard at dispatch; a ZIP download is not publishing.

## Internal delivery preflight (partial M6)

A generic delivery adapter consumes the checked render package behind the existing export guard.
The only implementation is `dry-run-v1`; it constructs no HTTP client and records no external post.
The guard holds the current workflow/source/configuration locks while the service verifies PNG bytes,
constructs an exact typed plan, validates the receipt and commits it. PostgreSQL separately checks
the plan against the pinned manifest and approval records. The receipt records a historical internal
check, not Instagram compatibility or future permission to publish.

Delivery state and attempts are persisted separately. WorkflowRun remains APPROVED; a simulation
cannot advance it to a publishing state. Connected delivery uses a separate reviewed extension
with durable dispatch intent, unknown-outcome reconciliation, account permissions and secret storage.
Never convert a saved dry-run row into a live delivery or blindly retry an uncertain external post.

## Historical metrics foundation (partial M7)

Authenticated operators can associate manual observations or explicitly synthetic fixtures with an
exact historically approved carousel render. MANUAL observations are SELF_REPORTED; an asserted
external reference is not proof of a platform post. FIXTURE observations have no external reference.
This historical manual path never becomes platform-verified. Connected insights use the separate social path; scheduled collection remains absent.

The metrics service preserves immutable input payloads, supplied evidence text and canonical hashes.
PostgreSQL computes a descriptive comparison of two observations of the same subject, with matching
scope and definitions and strictly increasing UTC observation times. Unknown values remain null;
negative deltas remain visible as possible reporting corrections. The typed service independently
checks the SQL result. Comparisons make no causal claim and never modify editorial policy.

Historical content and visual approvals establish the association without rerunning today's export
freshness guard. New revisions or expired evidence do not erase historical measurements. Metrics
cannot approve content, authorize export, infer that a dry run published anything, or change workflow
state. Connected platform collection is separate; automatic policy learning remains absent.

## Internal community review (partial M8)

Manual comment intake is a separate review flow attached to a source-backed WorkflowRun. It does
not change the originating carousel's state. The pinned immutable CharacterConfig may contain a
typed community policy. Deterministic skills classify exact configured phrases, build a reply from
configured creative text and exact selected factual excerpts, then validate it. Unknown input or a
missing policy requires human review. This review engine makes no provider calls. The social bridge
adds signed inbound comments and separately authorized dispatch without weakening its QA.

Each stage persists a SkillRun before execution and commits its checkpoint with successful output,
zero-cost telemetry and audit. SQL independently validates expected output; a caller cannot submit
an arbitrary reply or QA PASS. Approval is separate from parent content approval and ends only at
REVIEWED_DRAFT. Parent revisions, source conflicts/expiry and newer reply revisions invalidate old
approval attempts. Comment text cannot establish factual truth, individual eligibility or consent.
