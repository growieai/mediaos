# Hosted pilot verification

The owner-authorized Ubuntu host already runs Growie staging services. Media OS uses a separate
`mediaos-standalone` Compose project, a fresh `mediaos_standalone_postgres` volume, private
networks, `/srv/mediaos` storage and `/etc/mediaos` secrets. No existing Growie service, database,
configuration, container or volume was changed. The host address and operator IP remain in
private deployment notes.

## Verified on the target

- [Hosted CI for `a2405d2`](https://github.com/growieai/mediaos/actions/runs/36565327139)
  passed 1,580 backend tests, 234 frontend tests, clean migrations, lint/format/type checks,
  builds, persisted application/container acceptance and the hardened ingress check. This
  verifies the deployed application source plus the corrected isolated CI volume setup.
- Docker Engine 29.8.1 and Compose 5.5.1; sufficient available memory/disk; no pre-existing HTTP
  or HTTPS listener. Existing Growie containers remained running throughout commissioning.
- Source archives built with the checked-in lockfiles. Application containers use full local
  SHA-256 image IDs; PostgreSQL/Caddy use official registry manifest digests. Exact running app
  IDs are checked against the deployment record. See [local image releases](LOCAL_IMAGE_RELEASE.md).
- Separate random admin/runtime credentials generated on the host without printing values.
  Runtime has no migration credentials. Protected human credentials are separate from local
  development credentials and from server-only connector credentials.
- Clean migrations through `0020`, seed, preflight and healthy PostgreSQL/API/console containers.
- Console-to-API authenticated readiness for OPERATOR, APPROVER and ADMIN, expected role
  membership, creator/category/overview contracts, and rejection of missing/invalid credentials
  and another tenant ID.
- Mock mode and authenticated creator setup enabled; paid media, social connection/publishing,
  automatic dispatch and conversion delivery disabled.
- Actual hardened Caddy configuration validation. Only ingress retains `NET_BIND_SERVICE`,
  required to execute the official binary with its file capability. The application services
  retain an empty capability set and all services retain the existing privilege restrictions.
- Initial quiesced database/private-assets backup with SHA-256 checksums. The dump was restored
  into a separate `mediaos_restore_initial_c34a386` database on the Media OS PostgreSQL instance.
  Schema `0020`, forced tenant RLS and authenticated reads using the restricted runtime identity
  were verified. Assets were extracted separately and checksums verified. This was a same-host,
  same-cluster recovery exercise, not an off-site disaster-recovery test.

Target verification caught a source-draft compatibility gap with canonical Sofía's `es-ES`
configuration. The helper and UI now use the supported primary language for the generated
preview while preserving the saved creator configuration. Regional-language regression tests
cover the actual canonical configuration, provenance and rejected mismatched responses.

The deployed application source is `1d50447`, with API image
`sha256:09ee178eadd0920d2e18fae2093d7ec854b51fa986e14d0f1deea7882da23e4c`
and console image `sha256:43a85bf68d7026e5371de2742db9c1a2fb33b5c3c3d1a3d235bf990579a28c06`.
The exact application and infrastructure images were archived on the host with a SHA-256
manifest; an off-host archive copy is still pending. All 16 private-server checks passed after
the regional-language fix, including the actual seeded Spanish draft response and the
APPROVER's inability to invoke operator drafting.

## Public access still pending

The initial AWS group allowed SSH only. After renewing the existing AWS SSO session, the exact
instance, VPC and public network interface were verified. A separate tagged Media OS group now
allows only inbound IPv4 TCP 80/443. Its default egress was removed before attachment, and every
existing interface group was preserved. No existing group rule was edited. Exact AWS identifiers
and the before/after group lists are retained in private deployment notes.

Caddy is running on ports 80/443. External HTTP now reaches the origin and redirects to the
expected HTTPS hostname; origin TCP 443 is reachable. The console/API/database remain private.
The hostname now resolves through Cloudflare's proxy, and the public HTTPS request returned
525 while the origin did not yet have a usable certificate. Public HTTPS and browser acceptance
are therefore still pending. The next steps are:

1. The DNS operator sets only the `mediaos` A record for the supplied host to Cloudflare
   **DNS only** and TTL Auto. Existing Growie records and zone-wide settings remain unchanged.
2. Verify direct-origin DNS and a matching HTTPS certificate on the already-running dedicated
   Media OS ingress. Do not disable certificate verification or weaken application access checks
   to bypass the current proxy/certificate mismatch.
3. Verify browser access from the configured operator address, denial from outside that scope,
   and application authentication/tenant enforcement through HTTPS. The current Caddy policy
   uses the direct peer; enabling the Cloudflare proxy needs a separately reviewed policy.

Do not call the public deployment ready before those checks pass. Operator IP changes require
updating only the Media OS allowlist; do not open it to every address as a workaround.

## Operational limits

The initial backup stays on the server. An encrypted off-site destination, retention policy,
external alerts and full host-loss recovery are not configured. Human token issuance/rotation
and durable unattended orchestration remain operational work. This is an authenticated internal
pilot, with no public signup or customer billing. No live model/video/social acceptance was
performed. The source/onboarding draft helpers remain local mock templates, not real research.

See [standalone deployment](STANDALONE_DEPLOYMENT.md) for release operations and
[operations](OPERATIONS.md) for recovery boundaries. Never run test-suite schema rebuilds or
simulated approval acceptance against the hosted pilot database.
