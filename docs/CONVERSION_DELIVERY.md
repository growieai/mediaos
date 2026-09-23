# Explicitly reviewed consent handoff

Migration `0016` extends the existing manual export with a generic, manually invoked HTTPS
handoff protocol. An external destination must implement this contract and be explicitly chosen
and provisioned. No existing Growie service is contacted. The default live flag is false.

A destination receipt means that the destination acknowledged the exact request. It never means
that a business audit was performed, a customer converted, or any marketing contact was authorized.
Normal tests use an isolated test database and synthetic HTTP responses; they are not live delivery
verification or real subject-consent evidence.

The minimum path is:

```text
historically approved content + separately reviewed explicit consent
    + current, manually reviewed business identity
    + privileged exact destination provisioning
→ immutable outbound payload
→ AWAITING_AUTHORIZATION
→ separate APPROVER reviews exact payload hash and endpoint
→ AUTHORIZED (15-minute authorization)
→ explicit OPERATOR dispatch, committed attempt before HTTP
→ authenticated RECEIVED receipt | UNKNOWN_OUTCOME
```

The workflow's content state and approvals remain unchanged. Business identity review is recorded
as `OPERATOR_ASSERTION_REVIEWED`; the service neither fetches registry evidence nor infers a business
from a comment. Synthetic identities cannot be reviewed or promoted into manual identities.

## Provision an independent destination

First use the existing ADMIN destination route to create a `MANUAL_EXPORT` destination version.
Manual export remains available. Transport provisioning is a separate action using only the
migration identity, with no runtime/API route capable of installing a signing key:

```powershell
cd backend
# Set CONVERSION_SHARED_SECRET privately in the ignored environment, at least 32 characters.
.venv/Scripts/python.exe -m app.conversion.provision `
  --tenant <tenant-uuid> --destination <destination-version-uuid> `
  --endpoint https://independent.example/consent-handoff --retention-days 7
```

The command prints only the new transport UUID. Put the matching shared secret in the API's
ignored environment:

```dotenv
CONVERSION_DELIVERY_ENABLED=false
CONVERSION_DELIVERY_CREDENTIALS={"<transport-uuid>":"<shared-secret>"}
```

Enable the flag only after the independent recipient, credentials, consent and test protocol have
been verified. Exact host/path configuration is immutable; endpoint or secret changes require a
new destination version and fresh request/consent/identity/outbound review. A destination cannot
be supplied by an operator on the dispatch route. HTTPS, standard port, public DNS addresses,
no query strings, no embedded credentials and no redirects are required. The client pins a
validated public address while preserving TLS hostname verification. No local/private address is
supported by live transport; offline tests inject `httpx.MockTransport`.

## Business identity and exact review

`POST /v1/business-identities` accepts a typed, versioned manual identity:

```json
{
  "schema_version": 1,
  "idempotency_key": "separately-captured-request-key",
  "business_reference": "opaque-business-reference",
  "legal_name": "Exact registered name",
  "country": "ES",
  "registry_reference": "opaque-registry-reference",
  "evidence_origin": "internal:separately-recorded-registration-evidence",
  "evidence_sha256": "<64-lowercase-hex-evidence-checksum>",
  "mode": "MANUAL",
  "retain_until": "<UTC-ISO8601-time-within-90-days>"
}
```

Use `FIXTURE` for demonstration data. Do not put phone numbers, email addresses or credentials in
opaque reference fields. Evidence origins are stored as data, never fetched. An APPROVER posts
`/v1/business-identities/{id}/review` with `content_hash`, `identity_attested=true` and `comment`.
Changing an identity creates a new revision, invalidates old pending handoffs and requires review.

Create a handoff with `POST /v1/conversion-requests/{id}/deliveries`, supplying
`idempotency_key`, `business_identity_id`, `transport_id`, `attestation_id` and exact `request_hash`.
The identity's business reference must match the request. The transport must match that exact
destination version. The source request must be current, manual, consent-reviewed and unrevoked.

Inspect `/v1/conversion-deliveries/{id}`. It includes the exact outbound payload, endpoint,
payload hash, decisions, attempts and results. An APPROVER posts `/authorize` with
`payload_hash`, `decision="AUTHORIZE"` or `"REJECT"`, and a meaningful review comment.
An OPERATOR separately posts `/dispatch`. No route automatically performs outbound dispatch.

`GET /v1/conversion-dependencies` exposes flags and configuration availability without secrets.
`GET /v1/conversion-transports` is tenant-scoped. A global credential availability flag does not
mean that a particular tenant's transport credential is configured; execution checks the exact ID.

## Signed webhook protocol v1

Both peers derive a binary signing key as `SHA256(shared_secret.encode("utf-8"))`.
Canonical JSON means UTF-8, sorted object keys, no insignificant whitespace, literal Unicode,
no duplicate object keys, and no NaN/Infinity. Payloads contain strings, booleans and integer schema
versions, avoiding floating-point canonicalization ambiguity.

`payload_hash = SHA256(canonical_json(payload))`, represented as lowercase hex.
`signature = HMAC_SHA256(signing_key, payload_hash.encode("ascii"))`, also lowercase hex.

The sender performs exactly one `POST` to the provisioned endpoint:

```json
{"payload": "<exact JSON object from the saved delivery>", "payload_hash": "<hash>"}
```

The shown payload placeholder must be replaced by the JSON object, not a string. Headers include
`X-MediaOS-Signature`, `X-MediaOS-Payload-Hash`, and `Idempotency-Key: <delivery-uuid>`.
The recipient must verify the signature and hash before processing, enforce its own unique delivery
ID constraint, and preserve its receipt for subsequent reads. It must enforce purpose and retention
from the request. It must not treat the received payload as consent for another purpose.

Successful response is HTTP 200 with this exact schema:

```json
{
  "receipt": {
    "schema_version": 1,
    "delivery_id": "<exact-delivery-uuid>",
    "payload_hash": "<exact-payload-hash>",
    "status": "RECEIVED",
    "receipt_reference": "opaque-recipient-receipt-reference",
    "audit_completed": false
  },
  "signature": "<HMAC-of-canonical-receipt-hash>"
}
```

Response size is capped at 16 KiB. Redirects, invalid/unsigned/mismatched responses, compression,
network errors and uncertain POST errors become `UNKNOWN_OUTCOME`. The Python client verifies
the receipt; PostgreSQL independently verifies its HMAC using a private signing-key table before
accepting `RECEIVED`. Runtime SQL cannot fabricate a successful receipt. Neither keys, complete
handoff bodies nor contact/identity evidence appear in general audit or SkillRun payloads.

DNS waiting has a five-second bound and four-resolver concurrency cap. The live network transport
has a twenty-second absolute socket budget, including connection, TLS, partial writes, response
headers and body reads; it closes the stream synchronously on failure. It never releases a database
dispatch lock while a background HTTP worker continues transmitting.

## Interruption and reconciliation

The delivery attempt is committed before network work. A process crash before or after the POST
therefore leaves an uncertain durable attempt. Dispatch cannot be repeated, even with another
idempotency key, a new request revision, or a signed `NOT_FOUND` response.

A per-delivery PostgreSQL session execution lock remains on the same physical connection across
the attempt commit and the guarded network transaction. Concurrent service calls return the saved
state with `execution_in_progress=true`; they cannot mark an active attempt interrupted. Guarded
SQL also takes this execution lock before the consent-series lock, so direct runtime SQL callers
must wait for the active dispatcher. Process/database-session termination releases the session lock;
a later explicit reconciliation may then checkpoint a genuinely interrupted attempt.

An OPERATOR may explicitly post `/reconcile`, which commits a separate read attempt, marks any
interrupted prior attempt `UNKNOWN_OUTCOME`, and performs a signed GET to
`<endpoint>/receipts/<delivery-uuid>`. GET signing covers the canonical object
`{"delivery_id":"<uuid>","payload_hash":"<hash>"}`. It returns the same signed receipt schema;
`NOT_FOUND` is allowed for reads only and never establishes non-delivery or permission to resend.
At most ten reconciliation read attempts are permitted. There is no retry loop or scheduler.

Receipt recovery remains possible after consent expires or is revoked, because acknowledging
historical delivery does not authorize a new disclosure. Current eligibility is rechecked under
the same series, destination and business locks before dispatch. A concurrent revocation commits
either before dispatch and blocks it, or after dispatch and requires a separately tracked notice.

An APPROVER can reject an undispatched stale intent. Its immutable decision history remains;
a corrected replacement needs a new exact authorization. An intent with any network attempt
cannot be rejected to bypass duplicate protection.

## Revocation and retention boundary

The existing `/conversion-requests/{id}/revoke` blocks further transmission. It cannot recall
already received bytes. After a prior dispatch and either revocation or retention expiry,
`POST /conversion-deliveries/{id}/revocation` with an `idempotency_key` creates a minimal
`REVOKE` operation naming only the original delivery UUID and hash. It requires its own human
authorization and explicit dispatch. A signed `REVOKED` receipt records the recipient's deletion/
revocation acknowledgement. It is not an independent proof of physical deletion from that system.

The recipient must retain a revocation tombstone keyed by the original delivery UUID even when
that request is not found. It must reject a delayed SEND for the revoked UUID. An acknowledgement
without this protection cannot satisfy the protocol: a timed-out request may still be in transit.

Outgoing `retain_until` is the earliest of consent expiry, identity retention and the destination's
configured retention maximum (1–90 days). Expired data cannot enter a new SEND. Retention expiry
is visible in delivery detail and can drive a manually authorized revocation request.

**Local physical retention/purge is not implemented.** Existing consent and new business/evidence
records are immutable historical rows; time limits block use, not storage. Before storing real
subject/business data in production, implement and approve a data-minimization, legal-retention,
redaction/erasure and backup-retention policy. An expiring authorization or remote receipt must
never be presented as local erasure.

## Verification

Offline unit coverage:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_conversion_delivery_unit.py -q
```

Persisted PostgreSQL/API verification, using only the explicitly disposable `mediaos_test` database:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_conversion_delivery_integration.py tests/test_conversion_integration.py -q
```

Do not run concurrent PostgreSQL test sessions; the fixture rebuilds that database. Tests provision
a test-only signing key using the test migration identity, then run real restricted-role guards
with synthetic signed HTTP fixtures. They cover human roles, signed receipt forgery, tenant
references, unavailable credentials, immutable attempts, unknown outcomes, read recovery,
revocation, concurrent dispatch/revocation, stale identity, rejected replacement and idempotency.
They do not contact any external destination and do not prove real handoff completion.
