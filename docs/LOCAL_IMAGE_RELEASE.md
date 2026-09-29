# Local application image release

For a single-host pilot, `API_IMAGE` and `CONSOLE_IMAGE` may contain a full immutable
Docker image ID, `sha256:` followed by 64 lowercase hexadecimal characters, instead
of a registry manifest reference. `POSTGRES_IMAGE` and `CADDY_IMAGE` still require
verified registry references ending in `@sha256:<64 lowercase hexadecimal characters>`.
Mutable tags, shortened IDs and bare hexadecimal IDs are rejected by preflight.
This option requires no application-image registry publication.

An image ID identifies locally stored image content. It does not establish source
trust, testing, build reproducibility or availability on another Docker host.
Preflight validates configuration syntax only; it does not contact Docker. Before
using this release path, verify that the target Docker Engine and Compose accept
the full IDs, find the exact images locally, and start the intended containers.

## Build and record lineage

Export a reviewed, tested commit with `git archive` and retain the exact commit ID
and archive checksum. Extract into a new dedicated release directory on the target
host. Use that directory as the build context, with `backend/Dockerfile` for the
API and `apps/console/Dockerfile` for the console. Keep deployment secrets and
private state outside the source directory. Do not substitute the working tree or
copy local credentials into the build context.

Build with the checked-in dependency lockfiles. Record the resolved base-image
digests, architecture, build logs, available scan results, test evidence and build
time with the source commit. The current Dockerfiles use versioned base tags and
the backend installs operating-system packages; rebuilding the same source commit
can therefore produce different image IDs. Retain the actual deployed images.

Use separate `docker build --iidfile` outputs for API and console, then verify each
value against `docker image inspect --format '{{.Id}}' <full-image-ID>`. Copy those
exact full IDs into the protected deployment configuration. Temporary build tags
may help operators identify images, but the deployment configuration must use the
immutable IDs. Record the expected schema revision and exact nonsecret deployment
configuration alongside the release evidence.

## Start without application-image pulls

Follow [the standalone runbook](STANDALONE_DEPLOYMENT.md) for storage, credentials,
preflight, network isolation, migration, seed, authentication and feature flags.
Replace its unrestricted Compose pull with a pull of only the infrastructure
services. From the release directory, after setting the protected configuration:

```bash
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml config --quiet
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml pull postgres ingress
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml run --rm --no-deps --pull never ingress caddy validate --config /etc/caddy/Caddyfile
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml up -d --wait --pull never postgres
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml run --rm --pull never migrate
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml run --rm --pull never seed
```

After completing the runbook's ingestion-credential step and repeating preflight:

```bash
docker compose --env-file /etc/mediaos/deployment.env -f deploy/compose.standalone.yml up -d --wait --pull never api console ingress
```

Do not run an unqualified `docker compose pull`: local application IDs have no
registry retrieval path. Use `--pull never` consistently for starts and maintenance
runs. Missing images must stop the release; restore the recorded images instead of
silently switching to a tag or rebuilding an unverified substitute. Inspect each
started application's `.Image` value and compare it with the recorded ID, then
perform authenticated readiness, role and tenant-isolation checks. A successful
Compose syntax check alone does not prove runtime support or readiness.

## Preserve and recover the images

Before depending on a local release for recovery, use `docker image save --output`
to write both exact application image IDs into a dedicated release archive. Store
the archive outside the checkout, record its SHA-256 checksum and the two image
IDs, and retain a protected off-host copy through the selected backup route. Keep
the source archive and build evidence with it. Do not prune these images while a
release or rollback depends on them.

On a compatible recovery host, verify the archive checksum, run `docker image load
--input <release-archive>`, and inspect both full IDs before Compose starts. Recheck
the restored image IDs against the release record; load must recover those exact
IDs. Infrastructure images must also be locally available before `--pull never`
starts, either from their verified registry references or separately retained
archives. Image archives do not contain the database, mounted assets, TLS state or
host-secret files. Preserve and rehearse those separately according to
[the operations runbook](OPERATIONS.md). Image rollback remains subject to schema
compatibility; it never authorizes a blind database downgrade or volume deletion.
