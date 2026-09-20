# Data model

All entity primary keys are UUIDs. Tenant-owned rows have tenant_id and UNIQUE(tenant_id,id). Composite foreign keys preserve ownership and lineage independently of application filters. Timestamps are timezone-aware; business date rules use UTC. Audit events also have an ordered sequence because several transitions can commit in one transaction.

## Tables

| Group | Tables |
|---|---|
| Identity | tenants, principals, tenant_memberships |
| Configuration | influencers, influencer_versions, missions, character_config_versions |
| Evidence | source_snapshots, facts, research_packs, research_pack_versions, research_pack_sources, research_pack_facts |
| Content | content_briefs, brief_facts, content_assets, content_asset_versions, content_claims |
| Workflow | workflow_runs, skill_runs, qa_reports, approval_records, audit_events |
| Operations | cost_events |

The private.request_context table is inaccessible to runtime SQL. It binds authenticated tenant/principal identity to a database backend and transaction, not caller-writable session variables.

## Evidence lineage

SourceSnapshot stores raw content, SHA-256 checksum, origin/URL, publisher, classification, capture/submission times, environment, fixture flag and submitter. Capture data is immutable to runtime. The guarded source-attestation function sets verification status/reviewer/time/comment exactly once, before execution.

Facts contain stable UUIDs derived from snapshot, span and statement. Offsets are zero-based Unicode code-point offsets with an exclusive end. Out-of-bounds and duplicate spans are rejected before creating a run. Both input validation and a database trigger verify the exact source span. Normalized statements in M1 are verbatim excerpts with ASCII spaces trimmed consistently with PostgreSQL; arbitrary paraphrases are intentionally unsupported.

ResearchPack versions link to permitted snapshots and facts. Brief fact membership is constrained to the exact research revision. Content claim rows bind an asset revision, field path and fact to the brief's allowed facts. Fabricated, cross-tenant and unrelated references fail database constraints.

Grant data includes optional amount/currency, geographies, business types, opening date, deadline, required evidence, eligibility status and per-field evidence quotations. Mandatory dates and eligibility evidence must exist; supplied structured values must occur in their source quotation. UTC date logic rejects expired/inverted deadlines. Grant metadata cannot be labeled GENERAL. Confidence cannot override these checks.

## Revisions

Character, influencer, research and content versions are immutable. Version numbers are positive and unique within their parent. Typed payloads carry schema_version=1 and content hashes. PostgreSQL columns and foreign keys, rather than JSON alone, enforce current artifact ownership and lineage.

Research and brief generation currently create one immutable research version and brief per run. Correcting source/research requires a new idempotency key and run; content-only changes create a new ContentAsset version in the existing run.

QA binds to a content/research revision and policy version. Approval binds to the exact run, content version, research version and QA report. Historical approvals remain immutable but are never inherited by new revisions.

## Costs

Each SkillRun records skill/schema versions, provider/model/adapter, attempt, hash, timing, status, retryability, output and artifact references. Prompt version is null because no model prompts run. Mock execution is explicitly provider=mock, cost=0, is_mock=true. CostEvent records zero token usage, USD and mock-zero-v1 pricing. No fabricated estimates are used.

## Milestone 2 additions

Migration 0002 adds source_definitions, ingestion_runs, ingestion_attempts, raw_source_documents, source_observations, opportunities, opportunity_versions, source_links, opportunity_facts, verification_conflicts, change_events, duplicate_candidates, audience_segments, mission_editorial_policies, relevance_scores, editorial_decisions and workflow_opportunities. Every table is tenant-owned with forced RLS and composite ownership foreign keys.

SourceSnapshot now also supports ingestion outside a WorkflowRun. RAW snapshots preserve the exact UTF-8 official response and checksum before parsing. NORMALIZED snapshots retain typed-parser text and a parent pointer to the raw snapshot. Workflow source copies link to the normalized parent; relational evidence spans continue to target their exact text. The original response is always retrievable through raw_document_id. A parser failure still leaves a raw snapshot. M1 manual submission remains limited to 100,000 characters; bounded HTTP responses are limited to 2 MB.

Opportunity is a stable tenant/canonical-ID root. Its versions, links, facts, change events and conflicts are immutable. The optional grant profile distinguishes UNKNOWN eligibility from YES/NO, programme budget from applicant maximum, and recorded dates from unresolved relative rules. Repeated identical captures add observations without changing evidence or manufacturing an Opportunity revision.

One Opportunity can reference multiple original sources. BDNS identifiers are canonical when explicitly present; BOE/Cámara cross-references must be explicit and unambiguous. Similar titles create POSSIBLE_DUPLICATE records only. Conflicting known date/amount/eligibility fields retain both sources and block factual approval. Conflicts currently require investigation; automatic conflict resolution is deliberately absent.

Relevance scores store eligibility separately from weighted component scores. Mission policies and segments are versioned/admin-seeded data. Editorial decisions include mission objective, content mix, history references, evidence context and reasons. Workflow bindings pin exact opportunity/source/editorial revisions and a database-capped evidence expiry.

## M3 additions

Migration 0003 adds `visual_config_versions`, `render_runs` and `visual_approval_records`, all with
forced tenant RLS and composite ownership constraints. Visual configurations contain typed layout,
font hashes and optional exact character-reference hash/provenance. They are immutable, consecutive
admin-seeded revisions. A newer canonical visual configuration makes earlier renders stale.

RenderRun records exact workflow/content/research/QA/configuration IDs, tenant-scoped idempotency,
input hash, ordered sequence, state, timestamps, error category and immutable terminal manifest.
The manifest covers PNG checksums/dimensions, exact text coverage/fact IDs, fonts, portrait, renderer
version and visual findings. Files live privately under tenant/render UUID paths in local development.

VisualApprovalRecord pins the exact render manifest and matching immutable Content ApprovalRecord,
approver, decision and timestamp. Runtime cannot directly insert or modify protected visual tables.
New content, QA, visual configuration or render revisions cannot inherit an earlier visual approval.

`visual.render` records a deterministic SkillRun and zero-cost CostEvent. Interrupted attempts are
preserved. Imported reference images record provider/tool and prompt provenance separately; their
unreported model/tokens/cost are null. They are not represented as zero-cost runtime model calls.

## Delivery preflight (0004)

`delivery_targets` stores immutable, consecutive configuration versions scoped by tenant and logical
target key. Only DRY_RUN mode and the typed dry-run adapter configuration are accepted. New versions
can disable a target; execution requires its latest enabled version. There are no credentials here.

`delivery_runs` pins the exact render manifest, target version, workflow, asset/research/QA revisions,
content approval and visual approval. Composite foreign keys enforce ownership and approval lineage.
Tenant/key uniqueness plus a canonical request hash make creation idempotent. State, attempt count,
retry timestamp, errors and timestamps persist before work. A terminal successful run stores immutable
schema-versioned plan and receipt payloads with canonical hashes. No external post exists.

Both tables force RLS and grant runtime SELECT only. Guarded functions perform writes. Terminal
deliveries and all target versions are immutable. `delivery.dry_run` SkillRun attempts and CostEvents
explicitly record provider=mock, zero tokens and zero cost; audit events record each transition.
