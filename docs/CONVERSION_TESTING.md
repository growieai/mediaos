# Internal consent-aware request review (partial M9)

The manual-export path below remains unchanged. Migration `0016` adds a separate, disabled-by-default
[explicitly reviewed signed handoff](CONVERSION_DELIVERY.md), including business-identity review,
provisioned destinations, immutable outbound intent and signed reception/revocation receipts.
No real destination has been configured or contacted by the offline implementation tests.

The manual-export feature prepares a JSON request for a human to hand off. That path does not contact Growie or any
other destination, identify a business from a comment, perform an audit, send email/DMs, or claim
a conversion. `delivered=false`, `network_performed=false` and `audit_completed=false` are fixed.
No provider credentials or network access are needed. Existing Growie production infrastructure
remains out of bounds.

The destination is an ADMIN-created label with mode `MANUAL_EXPORT`, not an endpoint or secret.
The internal request carries opaque subject/business references, an explicit separately captured
consent statement, origin and UTC capture/expiry times. Do not include email addresses, phone
numbers or other raw contact details. The reference fields reject these common contact formats;
the human must also keep free-text evidence minimal. Origin strings are retained as evidence data
and are never fetched.

MANUAL means an operator assertion. Even after review it remains
`OPERATOR_ASSERTION_REVIEWED`, not independently verified platform consent. A social comment,
community classification, reply approval or prior content approval never establishes consent.
FIXTURE requests are permanently synthetic and cannot be reviewed for export. An attributed
fixture community event cannot create a MANUAL request.

## Local API demonstration

Run migration and seed with the normal standalone Media OS commands, then start the API/console.
Use bearer tokens from the ignored local credentials file and `X-Tenant-ID` on every request.
Do not paste tokens into documentation, URLs or chat. The internal API is at `http://127.0.0.1:8000`.

1. As ADMIN, POST `/v1/conversion-destinations` with:

   ```json
   {"schema_version":1,"destination_key":"internal-audit-review","mode":"MANUAL_EXPORT",
    "label":"Internal audit request review","enabled":true,"idempotency_key":"destination-1"}
   ```

2. Choose a WorkflowRun with an exact historical APPROVE record. POST
   `/v1/workflow-runs/{id}/conversion-requests` as OPERATOR. Supply a unique opaque
   `request_reference`, `subject_reference`, `business_reference`, `mode`,
   `purpose="BUSINESS_AUDIT_REQUEST"`, `approval_record_id`, nullable `community_event_id`,
   `destination_version_id`, `schema_version=1` and `idempotency_key`. The separate `consent`
   object requires `schema_version=1`, the same fixed purpose, `statement`, `origin`,
   `captured_at` and `expires_at` in UTC. Capture cannot be in the future; expiry follows capture.
   Use FIXTURE for invented demonstration data. A positive MANUAL case must be the real operator's
   explicitly documented request or a real consenting subject's request, not relabelled test data.

3. Inspect the saved request and evidence with GET `/v1/conversion-requests/{id}`. It starts at
   `AWAITING_REVIEW` for a current unexpired MANUAL request. An APPROVER posts `/review` with
   exact `request_hash`, `destination_hash`, `consent_hash`, `consent_attested=true` and a review
   `comment`. This creates an immutable attestation and yields `REVIEWED_REQUEST`.

4. An OPERATOR posts `/export` with an `idempotency_key`, exact `request_hash`, `destination_hash`
   and `attestation_id`. The server returns a saved JSON receipt/payload with
   `EXPORTED_FOR_MANUAL_HANDOFF`. It contains opaque identifiers, purpose, exact attribution,
   consent expiry and immutable review linkage. The consent statement itself is not copied into
   the handoff. No destination has received anything. The UI/consumer must preserve these labels.

5. Inspect the parent workflow audit trail. The export also saves one deterministic SkillRun and
   zero-cost CostEvent. The parent WorkflowRun state is unchanged. A same-key replay returns the
   same request/export; changed input conflicts. Export replay still rechecks current permission,
   consent expiry/revocation, request revision and destination revision before returning data.

GET `/v1/conversion-destinations` lists saved immutable destination versions. GET
`/v1/workflow-runs/{id}/conversion-requests` lists that workflow's request history. Request detail
includes attestations, revocations and historical export receipt metadata, but the handoff payload
is returned only through the guarded export route.

Export SkillRuns also store only typed receipt metadata: export/request IDs, the handoff content
hash, state/mode and the fixed no-delivery flags. Subject/business references and consent data are
not duplicated into general attempt history. The complete handoff remains in the protected export
record, and retrieving it again requires the guarded endpoint's current role and consent checks.

## Negative checks

- OPERATOR cannot review consent or configure a destination; APPROVER or ADMIN is required for
  review, ADMIN for destination configuration. All records enforce forced tenant RLS.
- Missing review, fixture requests, expired consent, fabricated/cross-tenant references and wrong
  review hashes cannot export. Runtime has SELECT-only table privileges; protected writes use
  fixed-search-path security-definer functions which validate exact JSON keys and hashes again.
- New consent/payload/destination means a new immutable request revision and fresh attestation.
  A new ADMIN destination revision invalidates the old destination version even if its label is
  unchanged. Disabled destinations cannot be used.
- POST `/revoke` with `{"reason":"Subject withdrew this request."}` as OPERATOR/APPROVER.
  Revocation applies to the entire request-reference series and blocks old export replays and
  new revisions of that series. Revocation does not undo bytes that were already downloaded or
  prove that a human has recalled an external manual handoff; the manual-export path does not track external delivery. Separately dispatched signed handoffs have their own receipt and revocation history.
- Creation, revision, review, revocation and export serialize through common series/destination
  locks. Concurrent same-key exports cannot duplicate a receipt or SkillRun. Expiry is checked
  with database time. Historical content revisions do not erase attribution: no content claims
  are included in this handoff, and historical attribution never grants consent by itself.

The five new tables are `conversion_destinations`, `conversion_requests`,
`conversion_attestations`, `conversion_revocations` and `conversion_exports` in migration 0008.
They preserve tenant-composite ownership, consecutive versions, immutable evidence/review/history
and independently computed canonical hashes. This local deterministic export is one atomic
database transaction: no external operation exists to retry or reconcile. An uncertain database
response can be retried with the same key; deterministic policy failures require correction or a
new reviewed revision, not blind retries.

## Verification and live completion boundary

Pure tests: `python -m pytest tests/test_conversion_unit.py` from `backend` (no database needed).
Integration tests: `python -m pytest tests/test_conversion_integration.py` using only the explicit
disposable `mediaos_test` database. Do not run two PostgreSQL pytest sessions concurrently.
Normal tests do not contact any provider or destination. The integration suite covers tenant
isolation, direct SQL bypass, consent evidence shape, fixtures, exact hashes, roles, stale revisions,
revocation, expiry, idempotency and concurrency.

Local verification on 2026-09-21 initially passed 28 pure tests and 24 PostgreSQL/API tests. The latter
recreated only the disposable test schema and migrated from empty through 0008; it also exercised
0007 in the migration chain. After the receipt-history privacy fix, 32 pure tests passed; three
additional PostgreSQL regressions are included in the final aggregate verification. Ruff
check/format and mypy for the four conversion modules passed.
Those figures describe the historical manual-export slice. The current local database is at `0017`; the September 25, 2026 console acceptance rehearsal passed with live integrations disabled. See the [completion report](MILESTONE_COMPLETION_REPORT.md) for exact aggregate tests, commit-specific hosted CI and Docker verification. A real external handoff has not been demonstrated.

## Signed handoff console and tests

At `http://127.0.0.1:3000`, select the workflow, open **Consent and manual handoff requests**, and select a saved request to show **External handoff**. The current console can inspect matching business evidence and a provisioned transport, record separate APPROVER identity/intent review, and expose explicit OPERATOR dispatch and receipt recovery. Preparing or authorizing an intent does not send it. Authorization never transfers to a replacement or modified request.

Request/consent and business-identity creation use the documented APIs; transport endpoint/signing-key provisioning is a separate privileged operation. Follow [CONVERSION_DELIVERY.md](CONVERSION_DELIVERY.md) for the exact contract and configuration. Provider keys and signing secrets never belong in the console. The current local live flag is false and no real recipient is provisioned. Use fixture data for offline negative checks; do not relabel it as genuine consent to enable a positive live path.

The additional focused suites are `tests/test_conversion_delivery_unit.py` and `tests/test_conversion_delivery_integration.py`. They cover bounded transport, receipt signatures, immutable exact authorization, tenancy, uncertain outcomes and concurrent dispatch/reconciliation with synthetic responses. They make no real handoff and do not establish recipient acceptance.

This is partial M9. Business identity review and signed delivery/revocation reconciliation are implemented; their real acceptance requires a chosen authorized recipient, actual consent and a confirmed exact handoff. Local physical retention/purge remains unimplemented, so identity/contact minimization, retention and erasure still need a production policy and implementation. Expiry blocks further sending but does not erase stored records. A business audit is a separate scoped
product and is not implemented by this request. Existing Growie production systems remain excluded.
