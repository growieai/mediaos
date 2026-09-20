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
