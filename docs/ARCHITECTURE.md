# Architecture — Growie Media OS v0.1

## Product boundary
Codex is used to build/review/test the codebase. Codex is not the production runtime orchestrator for Sofía.

Production runtime is a durable workflow system invoking typed skills through provider adapters.

## MVP topology

```text
Browser
  |
Next.js Console
  |
FastAPI Control Plane
  |
  +--> PostgreSQL (source of truth)
  +--> Redis (locks/cache)
  +--> Temporal (durable workflows)
  +--> S3/MinIO (media assets)
  +--> AI Provider Adapter
  +--> Research Connectors
  +--> Social Connectors [feature flagged]
```

## Production recommendation
For the Spain pilot, keep data/services in an EU region. Start containerized. Prefer managed PostgreSQL and object storage in production. Use Temporal Cloud or a properly operated Temporal cluster rather than the development auto-setup image.

Suggested production split:
- `console`: Next.js deployment.
- `api`: FastAPI stateless service.
- `worker-research`: network-enabled worker.
- `worker-content`: text generation worker.
- `worker-render`: media rendering worker.
- `worker-community`: comment/DM worker, isolated permissions.
- Managed PostgreSQL.
- Managed Redis.
- S3-compatible storage.
- Temporal service.
- Central logs/traces.

## Tenant model
Every mutable business object must resolve to a tenant.

Initial seed:
- tenant: `growie`
- influencer: `sofia_es`
- mission: Spain SMB authority + qualified Growie demand

External creator onboarding stays behind `ENABLE_EXTERNAL_CREATORS=false` until internal validation gates are met.

## Runtime roles
- Scout: discovery
- Researcher: structured evidence
- Editor: decides what deserves content
- Creator: writes in character
- Studio: creates media
- QA: fact/brand/policy gate
- Publisher: social delivery
- Community: comments/DMs
- Growth Brain: experiments and learning

These are workflow roles. Most concrete actions are versioned skills.

## First production loop

```text
source event
  -> normalize
  -> research + citations
  -> verify
  -> editorial score
  -> brief
  -> carousel copy
  -> visual render
  -> QA
  -> approval
  -> publish
  -> ingest metrics
  -> learning record
```

## Approval policy v0
Auto-approval is OFF initially.

Block on:
- missing source provenance;
- unclear eligibility/deadline for grant content;
- AI identity disclosure failure;
- character drift;
- prohibited/deceptive claims;
- missing tenant ownership.

## Model routing
Keep models environment-configurable.

Suggested default policy:
- classification/extraction at scale: cost-efficient model;
- editor/creator: balanced model;
- difficult verification/QA: strongest reasoning model;
- never rely on a model for date arithmetic, retries, idempotency, authorization, billing, or state transitions.
