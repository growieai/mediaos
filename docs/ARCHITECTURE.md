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

All five skills have typed inputs/outputs and persisted attempts. MockAdapter validates strict Pydantic schemas. Real provider adapters remain deferred to Milestone 2. There is no provider credential dependency.

Persona, voice, visual policy, brand policy, language, editorial settings and safe non-factual templates are versioned configuration. Markdown under characters/sofia remains canonical source material; seed imports it with runtime.json into immutable character and influencer versions. A changed content hash creates a new version. Existing runs retain their original configuration.

## Deliberate boundaries

The synchronous runner uses an advisory execution lock and short checkpoint transactions. Approval and revision use a shared workflow row lock. These boundaries can become Temporal activities later. PostgreSQL currently supplies the required locking; Redis is not required for this slice. S3/MinIO becomes necessary when binary assets arrive; this milestone stores structured carousel JSON only. Temporal, Redis and object-storage services remain architectural destinations, not unused mandatory local dependencies.

No external creator UI, signup, marketplace, crawling, image generation, video, publishing, community automation or billing is implemented. Startup rejects enabled out-of-scope flags or real AI mode.

## Production deployment still deferred

Production readiness requires an EU deployment decision, managed PostgreSQL, TLS ingress, an organizational identity/token lifecycle, service-secret distribution and rotation, backup/restore exercises, release/rollback procedures, centralized logs/alerts, resource/concurrency limits, and durable orchestration before unattended execution.

Docker Compose is local development configuration, not a production manifest. It binds ports to localhost. Keep database infrastructure private in production. The privileged migration identity must never be available to API/console workloads.

No connection to Growie production infrastructure is needed or permitted by this setup.
