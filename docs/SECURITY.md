# Security

An OPERATOR cannot approve content.

OPERATOR can submit sources, execute/resume workflows and revise content. APPROVER can attest source evidence and approve/reject current passing revisions. ADMIN includes both permissions. Any internal tenant member can inspect their tenant's workflows. There is no public signup.

## Authentication and RLS

Bearer tokens are cryptographically random; only SHA-256 hashes are stored in principals. Membership binds identities to tenants and roles. Credentials are never accepted from request body identity fields. In production, token issuance, expiry, rotation, revocation and organizational SSO need an operational policy; the initial local seed is not a customer authentication product.

The restricted mediaos_runtime PostgreSQL login is NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE and NOINHERIT. Tenant tables enable and force RLS. Public schema creation is revoked. Database business reads/writes need transaction-local authenticated context. Security-definer authentication stores the context in a private table keyed by backend and transaction. SET app.tenant_id cannot impersonate a tenant.

The migration/admin identity is privileged and separate from runtime. Security-definer functions are owned by that trusted migration identity, use a fixed search path and explicitly scope tenant access. That administrator can change schema/policies and is outside the application-role threat boundary.

## Approval invariant

Runtime has no UPDATE permission on workflow state, no INSERT/UPDATE permission on approval records, and no INSERT/UPDATE permission on QA reports. It cannot manufacture a PASS or set status=APPROVED.

QA is computed over stored evidence and payload inside PostgreSQL. Mandatory missing evidence/disclosure, fixture sources, unverified facts, unsupported claims, mismatched character policy and expired grant dates fail closed. A passing report moves only to AWAITING_APPROVAL.

The approval function authenticates an approver, locks the workflow, compares exact current artifact/QA IDs, requires PASS, rejects prior BLOCK on the same asset version, rechecks evidence/time eligibility, and atomically inserts approval plus the APPROVED transition. The revision function locks the same row and invalidates current QA. New versions never inherit old approval.

Factual content is a verbatim verified excerpt in M1. CREATIVE blocks must match versioned non-factual templates exactly; labeling arbitrary claims as creative does not bypass evidence QA. Brand/language/disclosure are checked against pinned character configuration.

## Secret and input handling

.env and .local are ignored by Git and Docker build context. Configuration uses SecretStr for runtime database credentials. Generated seed tokens stay in .local/credentials.json; protect that directory and do not share its contents. Application logs allowlist metadata and omit request bodies, headers, exception messages and SQL parameters.

API and console proxy enforce a 256 KiB request cap. Source text and typed fields have additional bounds. The UI keeps bearer tokens only in memory and renders data as escaped text. No tokens are placed in URLs or browser persistent storage.

Compose binds services to loopback and passes migration credentials only to maintenance jobs. Do not reuse development credentials in production, expose database services publicly, or connect this product to Growie's existing infrastructure.

All external-creator, publishing, replies and video flags must remain false; enabling them fails startup. There is no publishing code, even for APPROVED content.

Test data runs only in the explicit disposable mediaos_test database. Fixture markers cannot be removed after source submission and always block QA. Synthetic tests that exercise positive paths are not production content and must never be copied into production.

## Official-source trust boundary (M2)

INGESTOR is a separate internal principal, with the same restricted database runtime role and forced tenant RLS. Its secret is server-only: local seed writes `.local/ingestion-credentials.json` separately from operator credentials; production must inject INTELLIGENCE_TOKENS through a secret manager. API request bodies never accept source bodies, authority assertions or an ingestion credential for automatic official verification. Operators cannot invoke the protected attestation successfully. ADMIN is trusted maintenance authority, as in M1.

The HTTP adapter permits only fixed HTTPS official hosts, forbids credentials, alternate ports and redirects, enforces timeouts/size bounds, serializes host access, and applies bounded backoff/cache policy. Discovered links remain untrusted; application/document links are retained without unrestricted fetching. XML DTD/entity declarations are rejected. No browser login or automated application submission is implemented.

Government-host Retry-After cooldowns persist across tenants and new request keys in the private source_host_cooldowns infrastructure table. Restricted runtime callers have no direct table privileges. Guarded functions require an INGESTOR with the host in its seeded source configuration and only extend cooldowns. Minimum request spacing and concurrency are process-local; a future distributed worker deployment requires shared request-slot allocation too.

Raw response checksums and exact raw snapshots are database-validated. All normalized data and source observations are immutable and restricted to INGESTOR writes. Fixture flags persist through every projection and workflow; neither schema validity nor a score can make a fixture approvable. An original fixture record is never relabelled as live.

The old QA/approval guards remain intact. The additional approval trigger locks the current opportunity and checks conflicts, versions, source eligibility and expiry. Binding expiry is capped against actual source observations and the mission policy in PostgreSQL, so an operator cannot extend freshness by fabricating a relevance-score expiry. An approved historical revision may later become stale; future publishing must revalidate evidence at dispatch. Publishing is absent in this milestone.

Cámara states that its electronic-office conditions prohibit automation. Its source registry is disabled and its connector rejects live discovery pending permission: https://sede.camara.es/sede/html/titularidad . No access-control bypass is implemented.
