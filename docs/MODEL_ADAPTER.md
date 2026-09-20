# Typed creator selection adapter

`backend/app/ai/structured.py` supplies an optional OpenAI Responses adapter. It is a pure boundary: it does not connect to PostgreSQL, authorize tenants, execute retries, change workflow state or approve content. Existing deterministic/mock execution remains usable without an AI credential.

The caller provides `SelectionSettings(api_key=SecretStr(...), model=...)`, then calls `OpenAISelectionAdapter(settings).select(research, brief, character_config, influencer_version_id)`. There is no default model or configurable API host. The caller must explicitly configure a model supporting Structured Outputs and load its API key from a server-side secret. Never send the key through the console, put it in a URL, commit it or paste it into a task.

The adapter sends `text.format` with `type=json_schema` and `strict=true` to the fixed Responses endpoint. Requests disable response storage. It rejects incomplete responses and refusals before considering structured content, as described in the [official OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).

## Contract

The model chooses an ordered list of allowed fact IDs, approved headline template IDs, a caption template ID and the configured CTA template ID. Template IDs are hashes of exact versioned creative strings. The request schema includes only the supplied ID choices. Server validation independently rejects invented IDs, repeated facts, out-of-brief facts, invalid slide counts and changed CTA selections.

The model cannot supply prose. Deterministic assembly copies the selected facts' exact evidence text, existing creative strings and pinned language, disclosure, brand policy and influencer version into `CarouselDraft`. Repeating the same approved headline across slides is allowed; repeating a fact is rejected. This is model-assisted selection and ordering, not unrestricted copywriting or autonomous verification. Source freshness, fixture status, conflicts, tenant ownership and publication permission must still pass the persisted QA/approval guards.

`SelectionResult` contains the typed plan, assembled draft and `ExecutionMetadata`. Metadata contains the requested model, reported model, safe request/response identifiers, provider, adapter/prompt versions, mock=false and observed token counts/details. Missing usage remains null. Monetary cost is null: usage alone does not establish a billed amount. Integration must attach a versioned pricing policy or reconcile provider billing before claiming a known cost.

## Persistence and failures

The orchestrator must persist a SkillRun attempt before making the call, then atomically persist the typed result/artifact and final execution metadata. A `ModelSelectionError` exposes a fixed category, retryability, safe execution metadata where available and an optional Retry-After delay. Persist metadata from errors too: a refused or invalid model response can still consume tokens.

HTTP 429, HTTP 408, server failures and transport failures are retryable. Invalid input, authorization/client errors, refusal, incomplete or invalid output are not automatically retried. The adapter performs one request, follows no redirects, ignores ambient proxies, limits response bytes, bounds request size and uses a configured timeout. The existing persisted orchestrator owns bounded attempts and backoff. A network interruption can leave provider usage unknown; it must not be recorded as zero usage or a free call.

The adapter does not log request/response bodies, credentials or provider exception messages. Only predefined error categories and allowlisted metadata are returned. Provider-generated refusal text and arbitrary output are never promoted to diagnostic messages.

## Verification and remaining integration

Unit tests use `httpx.MockTransport`; no live OpenAI call or paid account is required. They cover strict schemas, exact text, fact/template scope, repeat rejection, input/output limits, refusal, incomplete responses, HTTP/network classification, usage accounting and secret-safe errors.

Live model behavior is not yet verified. Enabling this adapter requires integration with persisted attempts/costs, an explicitly configured API model/key, tenant-scoped persisted input loading and a live acceptance run. A successful isolated adapter test does not mark these integration requirements complete.
