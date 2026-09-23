# Standalone deployment and release runbook

The `deploy/compose.standalone.yml` scaffold is for one dedicated Linux host with one API worker.
It is separate from local Compose and from every existing Growie service. It prepares a controlled
internal pilot; it does not establish deployed production readiness. No host, domain, certificates,
registry, external alerting or off-site backups have been provisioned by this change.

The database and all private assets survive application container replacement. Only Caddy exposes
ports 80/443. PostgreSQL has no host port and joins only an internal Docker network. The API,
console and ingress run as UID 1000 with read-only roots, bounded resources, dropped capabilities
and no privilege escalation. Mutable data goes to explicitly provisioned persistent directories;
missing bind paths fail instead of being created implicitly as root. A separately provisioned
external Docker volume holds PostgreSQL data. Never run a volume deletion or prune command on it.

## Decisions required before a real deployment

- A new dedicated Linux host, jurisdiction and capacity. The included memory limits total about
  8 GiB before host overhead; choose capacity from measured workload and budget.
- A dedicated DNS name, operator/VPN source CIDRs, certificate contact and direct edge routing.
  Only operator CIDRs can access the console and normal API. App bearer authentication and tenant
  authorization still apply. Do not point the DNS name at an existing Growie service.
- A trusted image registry/release process and independently verified image digests. The example
  intentionally contains invalid placeholders. A digest ensures immutability, not trust; retain
  build provenance, scan results and the reviewed source commit separately.
- Protected storage and encryption, an off-site backup destination, retention/recovery objectives,
  an alert destination and responsible on-call operator. Local persistent disks are not off-site
  backups or multi-host object storage.
- Human identity issuance/rotation and a secret manager or protected host-secret lifecycle. Seed
  is an initial internal bootstrap only; this scaffold does not add SSO, public signup or billing.

Docker Engine/Compose **2.30+**, Python 3.12+, PostgreSQL 18-compatible client tools and Linux
`nsenter` are operator dependencies. Windows/WSL remains the local development path; use a separate
Linux host or isolated VM to exercise this deployment. Do not substitute a production database URL
to get a check to pass.

## Prepare a reviewed release

Build the existing backend/console Dockerfiles from a reviewed, clean commit using the CI-tested
lockfiles. Publish only to the chosen authorized registry. Pin `API_IMAGE` and `CONSOLE_IMAGE` to the
actual returned `@sha256:...` manifest digests. Select and verify compatible official PostgreSQL
18 and Caddy images, also pinned by digest. The scaffold builds nothing on the server and invents
no digest. Keep the previous release's digests and exact environment configuration for rollback.

Copy `deploy/deployment.env.example`, `runtime.env.example` and `maintenance.env.example` to
protected paths outside the checkout, for example `/etc/mediaos/`. Use mode `0600` on secret files
and protect parent directories. These are raw `KEY=value` files: no shell expansion or quotes.
Compose's raw env-file format preserves literal `$` in secrets. Percent-encode password characters
in database URLs. Use different random admin/runtime passwords of at least 32 characters.

`runtime.env` contains only the restricted `DATABASE_URL` and, after seed, `INTELLIGENCE_TOKENS`.
`maintenance.env` contains `MIGRATION_DATABASE_URL` and `POSTGRES_RUNTIME_PASSWORD`. The separate
`postgres_admin_password` file contains only the same decoded admin password. The API never mounts
either maintenance file, bootstrap secret or human credential file. Do not put secrets in command
arguments, shell history, source control, screenshots or `docker compose config` output. Use
`config --quiet` for validation; Docker administrators can still inspect container environments.

Create `/srv/mediaos/state/{renders,media,social}` and `/srv/mediaos/tls/{data,config}`, including
their parents, owned by UID/GID `1000:1000`, mode `0700`. Choose an encrypted durable filesystem.
Create the exact dedicated external PostgreSQL volume named in `POSTGRES_VOLUME`; verify it is new
and not attached to any existing service. No automated initializer here chooses or clears a volume.

Run from the reviewed checkout:

```bash
python3 scripts/deployment.py preflight --config /etc/mediaos/deployment.env
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml config --quiet
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml pull
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml run --rm --no-deps ingress caddy validate --config /etc/caddy/Caddyfile
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml up -d --wait postgres
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml run --rm migrate
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml run --rm seed
```

Preflight does not connect to a database, start containers, verify image provenance, validate DNS,
check a Docker volume, request a certificate or prove restore success. It reports safe booleans,
checks password agreement and role separation, rejects placeholder images/domains or unrestricted
CIDRs, and checks private existing storage. On non-Linux hosts it reports `linux_host=false`.
The initial absent ingestion map is reported separately and does not block bootstrap.

After seed, transfer the tenant/token map from the private state directory's
`ingestion-credentials.json` into `INTELLIGENCE_TOKENS` in the runtime secret file. Protect the
separate human `credentials.json` and distribute individual roles through the chosen secure
channel. Do not grant an operator approval authority simply to make the demo easier. Repeating seed
with its existing private credentials file is idempotent; losing/replacing that file can rotate
seeded identities, so do not treat seed as a routine release action without examining its inputs.

Run preflight again, then start API, console and ingress. Check ingress validation/TLS from the
actual operator network. Initial configuration forces text mock mode and disables media execution,
social connections/publishing/replies, conversion delivery and all unsupported automatic features.
Adding paid credentials alone cannot turn these features on.

```bash
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml up -d --wait api console ingress
```

Use real manually submitted evidence and separate human operator/approver identities for the
pilot. Do not run the acceptance harness against production data: it intentionally creates fixtures
and simulates approval. Run it on an isolated staging deployment instead. Readiness must report
the expected migration head, and missing/wrong-tenant authentication must remain rejected.

## TLS and public routes

Caddy uses the configured hostname and certificate contact to obtain/renew TLS certificates.
Persist its private `/data` and `/config` mounts; restrict access to certificate keys. Firewall the
host so only SSH from the administration network and 80/443 as appropriate are reachable. Do not
publish PostgreSQL, Redis, object stores, API port 8000, console port 3000 or Docker's control socket.

Ingress exposes only GET OAuth callback, GET/POST signed webhook, and GET short-lived social JPEG
capability routes to provider traffic. Every other route requires an operator source CIDR before
application authentication. The existing public-route signatures, OAuth state, expiry checks and
server-side integration flags remain authoritative. It has no filesystem-serving route.

The CIDR check uses the **direct peer IP**. Put this Caddy instance directly at the edge. A CDN or
load balancer would change that peer and needs a separate reviewed trusted-proxy policy; blindly
allowing a proxy's address could allow every visitor through. Do not trust a client-supplied
`X-Forwarded-For` header. Never use `0.0.0.0/0` or `::/0` for console access.

Raw proxy logs are discarded because they can contain OAuth codes or media capability URLs.
Application metadata logs remain bounded in Docker's local log driver and omit raw request data.
Forward only those safe logs to the selected collector. Use the checks below for ingress/service
health; do not enable ordinary proxy access/error logs as a shortcut during a credential incident.

## Alert-ready checks

`scripts/deployment.py probe` checks authenticated database/schema readiness, private-storage free
space and the age/checksum of a completed version-2 backup manifest. It returns a nonzero exit code
on failure and emits JSON or Prometheus text without tenant IDs, credentials, file paths, remote
response bodies or exception text. It rejects redirects and environment HTTP proxies so a bearer
token is not forwarded to another host. HTTPS certificate verification stays enabled. HTTP is
permitted only for an explicit loopback port.

Put an existing limited internal monitoring identity's bearer token in a mode-0600 file. The tool
only makes a GET readiness request; it does not approve, resume or mutate workflows. Membership and
token lifecycle must be managed by the chosen organizational process. Run from an allowed operator
IP with visibility of the actual durable storage and backup directory:

```bash
python3 scripts/deployment.py probe \
  --api-url https://YOUR-DEDICATED-HOSTNAME \
  --tenant-id YOUR-TENANT-UUID \
  --token-file /etc/mediaos/monitor_token \
  --storage /srv/mediaos/state \
  --storage /srv/mediaos/tls \
  --backup-manifest /srv/mediaos/backups/REVIEWED-LATEST-BACKUP/manifest.json \
  --max-backup-age-hours 26 --format prometheus
```

Select and configure a scheduler/monitor explicitly; none is installed by the scaffold. Alert on a
failed readiness check, missing/stale backup, less than 10% free space or less than 1 GiB free,
repeated container restarts, host memory pressure, certificate expiry and absent probe results.
The manifest-age check does not read every backup file or prove restorability. Run the separate
full backup verifier and isolated restore exercise. A successful probe does not establish that
there are no blocked workflows, unresolved social writes, source conflicts or budget exhaustion;
use persisted run/attempt/audit/cost records for those operator queues. Agree response owners and
objectives before enabling any live integrations.

## Consistent backup and isolated restore

Follow [OPERATIONS.md](OPERATIONS.md), including private/encrypted off-site copies and independently
retained checksums. Stop every application writer during the combined database/file capture. Keep
PostgreSQL running. A volume/disk snapshot alone does not establish consistent database-and-asset
recovery. Do not automatically retry uncertain social/media/handoff side effects during recovery.

The existing backup tool deliberately accepts loopback databases only. On the dedicated Linux
Compose host, a trusted administrator may run the host tool in **only** the PostgreSQL container's
network namespace. This keeps database ports private and retains the host filesystem for asset
capture. Install compatible PostgreSQL client binaries and Python on the host. Inspect the PID
from this exact Compose project and verify the container identity before entering its namespace:

```bash
postgres_id=$(docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml ps -q postgres)
postgres_pid=$(docker inspect --format '{{.State.Pid}}' "$postgres_id")
# Confirm this is the intended standalone container. Stop all application writers first.
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml stop ingress console api
read -rsp 'Standalone loopback maintenance URL: ' MEDIAOS_BACKUP_DATABASE_URL
export MEDIAOS_BACKUP_DATABASE_URL
# URL targets mediaos_admin at 127.0.0.1:5432/mediaos inside the inspected namespace.
sudo --preserve-env=MEDIAOS_BACKUP_DATABASE_URL nsenter --target "$postgres_pid" --net -- \
  python3 scripts/backup.py create --workspace /srv/mediaos \
  --renders /srv/mediaos/state/renders --media /srv/mediaos/state/media \
  --social /srv/mediaos/state/social --backup /srv/mediaos/backups/NEW-RELEASE-LABEL \
  --confirm-standalone mediaos --confirm-quiesced
unset MEDIAOS_BACKUP_DATABASE_URL
```

Use a **new** backup directory, run `verify`, and upload only through the selected encrypted,
authorized off-site backup route. The tool does not upload anything. Back up TLS state and secret
recovery material separately under the selected policy; the application backup intentionally
excludes keys, service tokens and cluster-role credentials. Protect the whole state directory,
including seed credential files that are not mounted by the API.

Restore into an absent `mediaos_restore_<label>` database and new storage directory, never over the
source. Use the analogous inspected namespace and explicit `MEDIAOS_RESTORE_DATABASE_URL` for the
trusted maintenance role. Follow the exact dry-run and restore commands in OPERATIONS.md. Preserve
ACLs, RLS and function ownership. A separately started restored API must keep all integrations off,
have no provider keys, and bind only to loopback. Rehearse authenticated artifact/audit reads and
private previews. Do not repoint ingress or resume side effects from a restored database without
reconciling the external world and explicitly accepting the recovery point. Namespace-based
Compose backup/restore remains unverified until exercised on the chosen Linux host.

## Release, rollback and rotation

For each release, record reviewed source commit, API/console image digests, schema head, test/CI
results and backup identity. Quiesce writers, take and verify a consistent backup, then run the
new image's maintenance migration before starting it. Retain source/configuration lineage and
check current content/visual/media approvals; a deployment never transfers an approval to a changed
asset. If migration or readiness fails, keep ingress closed and investigate; don't retry public
dispatch while recovering.

Rollback an app image only if it is explicitly compatible with the current schema. Readiness pins
the expected schema, so an older image after a migration may intentionally refuse service. Do not
issue a blind Alembic downgrade or restore a live database in place. Restore a consistent database
and storage pair into a new isolated environment, verify it, reconcile provider-side operations,
then plan a controlled cutover. Preserve the failed environment for investigation.

For runtime database password rotation, stop writers, update the protected runtime and maintenance
files coherently, then run the existing `migrate` maintenance command: its final step updates the
restricted login password. Restart the API and verify readiness. Changing PostgreSQL's bootstrap
password file on an existing volume **does not** rotate its database role. A trusted database
administrator must explicitly rotate the admin role using a secret-safe maintenance channel and
update both maintenance/bootstrap secrets; preserve tested emergency access first.

For human/INGESTOR/SOCIAL credentials, use the reviewed administrative identity lifecycle, revoke the
old principal/token, inject the replacement and test tenant/role rejection. Preserve immutable
approval history. Do not rerun seed with missing credential files as an informal rotation tool.
Provider-key changes require a controlled restart and feature gates staying off until validated.
An Instagram vault-key change requires deliberate decrypt/re-encrypt or reconnect recovery; merely
replacing the key strands encrypted tokens. Follow SOCIAL_INTEGRATION.md, disable dispatch, retain
unknown-outcome receipts and never erase attempts to make a retry possible.

Before multi-host or unattended production, add durable private object storage, coordinated
database/object recovery, managed off-site/PITR backups, distributed rate-limit slots and durable
orchestration, organizational identity, TLS/secret automation, tested external alerting and
documented recovery objectives. This single-host scaffold does not claim those are implemented.

## Verification recorded

On 2026-09-23, the offline deployment tests passed on Windows: **49 passed, one POSIX permission
test skipped**. Ruff checks and formatting passed. The tests do not contact PostgreSQL, Docker or
providers. The POSIX permission case still needs the Linux CI run.

The official [Caddy 2.11.4 release](https://github.com/caddyserver/caddy/releases/tag/v2.11.4)
Windows verifier was downloaded outside the repository and checked against the release's archive
SHA-256 `1708333f79e274c7697285afe6d592ab39314e0b131e9ec6bea08ad27df62ebf`.
`caddy validate --config deploy/Caddyfile --adapter caddyfile` passed with a synthetic `.invalid`
hostname, example operator CIDRs and task-local state paths. Standard Caddy formatting was applied
and validation repeated successfully. No server was started and no certificate was requested.
This establishes configuration validity, not Docker startup, actual network restrictions, DNS/TLS
issuance or production deployment acceptance.

References: [Compose service configuration](https://docs.docker.com/reference/compose-file/services/),
[Caddy request matching](https://caddyserver.com/docs/caddyfile/matchers),
[Caddy request limits](https://caddyserver.com/docs/caddyfile/directives/request_body).
