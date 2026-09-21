# Optional real text execution

Mock mode remains the default and does not require an AI account. This implementation enables only `content.carousel` through the OpenAI Responses API. Research extraction, verification, brief construction, evidence checks, dates, workflow transitions, QA and approval remain deterministic. No real API call was used to verify this integration; tests intercept HTTP locally.

The creator uses a strict `CreatorPlan` schema to select and order verified fact IDs and approved creative-template IDs. Deterministic assembly copies exact sourced factual text and approved templates into the carousel. The model cannot write new claims, invent fact IDs, omit the required disclosure, choose an unapproved CTA, or approve its result. This deliberately bounded first integration provides editorial selection; unrestricted model-authored copy is deferred.

## Enabling it deliberately

1. Configure an OpenAI project key in the local secret environment as `OPENAI_API_KEY`; never send a key through the console or commit it. Keep `AI_MOCK_MODE=true` until ready for paid calls.
2. An authenticated tenant `ADMIN` creates a policy with `POST /v1/text-ai-policy`. Set an explicitly chosen model that supports strict Responses Structured Outputs; there is no default paid model. Configure all limits and a current, account-appropriate price reference. No assumed public pricing is silently applied.
3. Restart the backend with `AI_MOCK_MODE=false` only when ready to authorize paid execution. Existing tenant policies must be enabled and unexpired. The normal workflow execute endpoint then uses real selection when it reaches its first creator attempt.
4. Review the sourced result and existing QA report, then approve the exact revisions through the existing guarded approval endpoint. A successful model request never grants approval.

The policy schema is defined in `backend/app/ai/policy.py`. Required fields are:

| Field | Purpose |
| --- | --- |
| `schema_version` | `1` |
| `enabled`, `model` | Explicit opt-in and chosen model ID |
| `max_input_bytes`, `max_output_tokens` | Bounded request size and output token cap |
| `max_calls_per_run`, `max_calls_per_day` | At most three attempts per workflow, tenant daily call limit |
| `input_usd_per_million_tokens`, `output_usd_per_million_tokens` | Decimal strings from the administrator's checked rate card |
| `per_run_usd`, `per_day_usd` | Decimal-string reservation limits, in USD |
| `price_reference`, `price_checked_at` | Evidence of the price check and UTC time |
| `expires_at` | UTC policy expiry, within thirty days of the price check |

`GET /v1/text-ai-policy` returns immutable policy versions. Creating a disabled version immediately prevents new reservations. A retry may not switch policy, model, request or evidence: changing policy requires a new workflow, avoiding silent changes to a paid attempt's contract. `GET /v1/workflow-runs/{id}/text-ai-attempts` exposes authenticated tenant-scoped operational history without repeating the full prompt.

## Persistence, spending and recovery

Migration `0009` adds RLS-protected `text_ai_policies` and `text_ai_attempts`. Guarded database functions create an attempt, `SkillRun`, audit event and `CostEvent` in a committed transaction **before** the network call. The request has a canonical hash and exact pinned ResearchPack, ContentBrief, influencer configuration, source checksums and source-verification state. The provider request runs outside a database transaction. Afterward, current evidence is checked again; the result, exact ContentAsset revision, skill completion and telemetry commit atomically. Revoked, stale, fixture or changed evidence prevents the artifact commit.

Tenant reservations serialize under a database advisory lock. The input token reservation conservatively uses the UTF-8 request JSON byte count plus 4096 tokens for protocol overhead; output uses the explicit maximum. USD reservations use these bounds and the administrator's dated input/output rates, rounded upward to a micro-dollar. The model must have rates covered by that administrator configuration; provider pricing changes or an incorrect rate card cannot be prevented by the application. Configure provider-side project budgets as an additional account control.

Every attempt, including failed or ambiguous ones, consumes its reservation permanently. No refunds or guessed billing reconciliation are performed. Actual `cost` remains `null` because a usage response does not establish a billed monetary amount. Reported input/output tokens are stored when valid; missing or invalid usage remains `null`, never fabricated zero. The reservation and actual usage are distinct. Mock and deterministic work retain their existing zero-cost behavior.

An explicit HTTP 429 can retry, within the stored policy limits, after persisted exponential backoff and any larger bounded `Retry-After`. Execution returns at this checkpoint; a subsequent manual execute resumes it. Timeout, connection loss, server error, truncated response or process interruption after reservation becomes `UNKNOWN_OUTCOME`, with a failed workflow and **no automatic replay**. A result whose database commit acknowledgement is lost is inspected on the next execute: a committed artifact continues normally; an unresolved running attempt is held. There is no invented provider idempotency guarantee. Manual reconciliation of an ambiguous paid request is required before deliberately creating another workflow.

Provider refusal, invalid plan, fabricated selection and unsupported output fail without retry. If valid usage accompanied an invalid plan, that usage is still persisted. Attempts and terminal results are immutable. The restricted runtime cannot directly forge OpenAI skill/cost rows or change their protected usage fields; guarded functions enforce linkage and state.

## Security and verification

The adapter uses a fixed HTTPS API host, `store: false`, no redirects, no environment proxies, one bounded request at a time, size/time limits, schema validation, and safe error categories. API credentials are held as `SecretStr`; request bodies, provider error bodies and credentials are not logged. An attempt UUID is sent as `X-Client-Request-Id` for correlation, not as an assumed idempotency key. Official evidence and configured persona/brief text are included in the paid provider request only after opt-in, so administrators must ensure the provider project is appropriate for their data.

The focused suites cover strict output and input limits, policy validation, committed attempts before network I/O, nullable billing, retry/resume and interruption, concurrent budget and tenant restrictions, immutable source attestation during inference, unsupported claims, and the unchanged human approval transition. Normal tests require no provider credentials or external network. Live paid verification remains a dependency until the user configures credentials, a model and an explicit policy.

Official references: [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [API error codes](https://developers.openai.com/api/docs/guides/error-codes).
