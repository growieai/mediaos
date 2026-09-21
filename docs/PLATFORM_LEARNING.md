# Descriptive platform learning

The internal console/API can compare two saved Instagram insights observations for one confirmed published post. This is a deterministic historical comparison, with no model calls, no provider requests during comparison, no causal claims and no automatic strategy changes.

`POST /v1/social-publishes/{id}/learning` accepts:

```json
{
  "baseline_snapshot_id": "UUID of the earlier saved platform observation",
  "current_snapshot_id": "UUID of the later saved platform observation",
  "idempotency_key": "operator-chosen-key"
}
```

Both IDs must refer to the same tenant, published post, workflow and pinned connection/API version. Capture timestamps must strictly increase. Metric key sets and exact metric definitions must match; changing a metric definition requires a new compatible observation pair. The endpoint requires an authenticated `OPERATOR`; the corresponding `GET` returns tenant-scoped immutable reports. Same tenant/key/input returns the existing report, including under concurrent requests. Reusing the key for another pair conflicts.

Each metric contains its exact baseline and current values, a signed delta, and `INCREASED`, `DECREASED`, `UNCHANGED` or `UNKNOWN`. If either value is missing, its delta is `null` and direction is `UNKNOWN`. A decrease is retained; it may reflect platform corrections and does not prove negative content impact. When no metric can be compared, the report is `INSUFFICIENT_DATA`. The report always states `causal_claim=false`, `policy_updated=false` and `network_performed=false`.

Migration `0012` creates `social_learning_reports`, with forced tenant RLS, composite snapshot/publish/workflow ownership constraints, immutable reports, canonical hashes and guarded generation. It links both exact snapshot hashes, platform media/account IDs, pinned API version and observed metric definitions. A restricted runtime role cannot insert a report or upload a platform snapshot directly. The database generates report values from successful connector `INSIGHTS` jobs and their immutable observed payloads; a manually entered M7 metric or fixture record cannot be relabeled `PLATFORM` through this API.

`PLATFORM` describes the observation's connector lineage, not its causal validity. Tests use intercepted, synthetic provider responses in the disposable test database, never real platform metrics. The production feature still requires an actual connected professional Instagram account, confirmed posting permission and two successful insights captures. Existing manual M7 observations retain their own `MANUAL`/`FIXTURE` provenance and remain separate.

Each new comparison writes a deterministic `learning.platform_describe` SkillRun, zero-token/zero-cost CostEvent and correlated `SOCIAL_LEARNING_CREATED` audit event. The report commits atomically with that telemetry. Historical observations can still be compared after account disconnection or a later content revision; a learning report conveys no authorization to publish or refresh insights. Full provider billing and live account verification remain separate operational evidence.

Tests cover null/zero/decreased counters, strict payload schemas and evidence hashes, mismatched posts/API versions/definitions, tenant and role isolation, reversed/equal timestamps, immutable protected writes, idempotent and concurrent creation, exact operational telemetry, and historical comparisons after revocation.
