# Standalone backup and restore rehearsal

`scripts/backup.py` is a local maintenance tool for this standalone Media OS database and its
private render/media/social files. It has no application API, scheduler or provider calls.
It backs up social JPEGs and receipts without contacting the platform or dispatching content.
A successful dry-run is a plan, not a completed backup or restore.

## Prerequisites and boundaries

- Python 3.12+; this script uses the standard library only.
- PostgreSQL 18+ client tools: `pg_dump`, `pg_restore` and `psql` on PATH, or an absolute
  `--pg-bin` directory. Use clients compatible with the server; no service is installed by the tool.
- An existing standalone PostgreSQL instance on `127.0.0.1`, `localhost` or `::1`, an explicit
  port, and the trusted `mediaos_admin` maintenance identity. Remote hosts, URL query options,
  runtime credentials and non-`mediaos` database names are rejected.
- The source application, ingestion jobs, render/media executors and every other writer must
  be stopped for the entire backup. A database dump alone cannot atomically capture local files.
  `--confirm-quiesced` records the operator's assertion. Before/after counts and file hashes
  detect some concurrent changes; they do not prove that every writer was stopped.
- Keep the PostgreSQL service running. Do not use `docker compose down -v`, delete data, or
  connect to Growie's existing production infrastructure.

The tool intentionally does not load `.env`. Supply dedicated `MEDIAOS_BACKUP_DATABASE_URL`
and `MEDIAOS_RESTORE_DATABASE_URL` variables from a protected environment or a masked prompt.
Copy the standalone maintenance URL from your local configuration, never an existing Growie
production URL. Database passwords are passed through the child process environment, not its
arguments; subprocess output is captured and failures report a fixed safe error. The manifest
contains host, port, database and username, never the password.

Native Windows here uses `127.0.0.1:55432/mediaos_dev`; the Docker host example uses
`127.0.0.1:55433/mediaos`. Host tools must not use the Compose-only `postgres:5432` address.
An administrator with equivalent OS access can inspect process environments, so run maintenance
under a trusted local account. Protect Windows inherited ACLs on the workspace and backup folder.
On POSIX the tool creates private files with mode `0600` and directories with mode `0700`.

## Inspect readiness and create a backup

PowerShell 7, from the repository root:

```powershell
$workspacePath = (Get-Location).Path
$pgBinPath = 'C:\Program Files\PostgreSQL\18\bin'
$backupPath = Join-Path $workspacePath '.local/backups/rehearsal-001'
$env:MEDIAOS_BACKUP_DATABASE_URL = Read-Host 'Standalone maintenance URL' -MaskInput

python scripts/backup.py check --workspace $workspacePath --pg-bin $pgBinPath
```

`check` examines configuration, executable availability and private storage readability. It
does not contact PostgreSQL and cannot establish database connectivity, current migrations,
credential validity, sufficient disk space or restore success. Missing checks return exit 1.
Set `--renders`, `--media` and `--social` to the application's actual absolute storage directories
when they differ from `<workspace>/.local/renders`, `<workspace>/.local/media` and
`<workspace>/.local/social`. The social root corresponds to `SOCIAL_STORAGE_PATH`; it contains
private exact JPEGs and dispatch receipts. All three roots must exist, even if currently empty.

After stopping all application writers, inspect the plan, then create it. The confirmation is
the exact source database name; substitute `mediaos` if using the Docker example.

```powershell
python scripts/backup.py create --workspace $workspacePath --pg-bin $pgBinPath --backup $backupPath --confirm-standalone mediaos_dev --confirm-quiesced --dry-run
python scripts/backup.py create --workspace $workspacePath --pg-bin $pgBinPath --backup $backupPath --confirm-standalone mediaos_dev --confirm-quiesced
python scripts/backup.py verify --workspace $workspacePath --backup $backupPath
```

Each backup destination must be new. Backups contain:

- `database.dump`, the complete custom-format database dump with ownership and ACLs intact;
- `renders/`, `media/` and `social/`, copies of the three explicitly selected private storage roots;
- `manifest.json`, schema version, UTC capture time, database migration identifiers, selected
  relational row counts, restricted-role/RLS/guard-owner checks, and every file's SHA-256/size;
- `manifest.sha256`, a checksum of the exact manifest bytes.

The script does not copy `.env`, API credentials, `.local/credentials.json`, source code,
PostgreSQL configuration or cluster roles. Database contents and private receipts can contain
sensitive business data, token hashes and signed provider URLs. Keep the entire backup private,
outside Git, and encrypt any off-host copy. A checksum detects damage, not malicious replacement:
retain the manifest checksum in a separately trusted location and restore only trusted archives.
PostgreSQL archives can execute SQL during restore.

Successful backup validation does not alter source rows, revisions, approval state or files.
On failure a partial directory is retained without a valid completed manifest; investigate and
choose a new destination for the next attempt. There is no automatic cleanup or overwrite.

## Validate and restore into a disposable database

Choose a new database named `mediaos_restore_<label>`, and a new absolute storage destination
inside the chosen workspace. The database must differ from the source name even if a different
server/port is selected. Both storage and database destinations must be absent.

The target standalone cluster must already have its trusted `mediaos_admin` owner and restricted
`mediaos_runtime` role. Rehearsing on the same standalone local cluster satisfies this prerequisite.
The tool never creates/alters cluster roles or copies passwords. For a new isolated cluster,
provision those roles using the project's reviewed maintenance process first. Restoring with
`--no-owner` or `--no-acl` is deliberately unsupported because it would change the security boundary.

```powershell
$restorePath = Join-Path $workspacePath '.local/restored/rehearsal-001'
$env:MEDIAOS_RESTORE_DATABASE_URL = Read-Host 'Disposable mediaos_restore_rehearsal_001 maintenance URL' -MaskInput

python scripts/backup.py restore --workspace $workspacePath --pg-bin $pgBinPath --backup $backupPath --destination $restorePath --confirm-disposable mediaos_restore_rehearsal_001 --dry-run
python scripts/backup.py restore --workspace $workspacePath --pg-bin $pgBinPath --backup $backupPath --destination $restorePath --confirm-disposable mediaos_restore_rehearsal_001
```

The dry-run verifies every manifest/file checksum and destination path without any database
connection or mutation. It cannot check whether a destination database exists; the actual restore
checks that separately before creating anything. An existing destination is always rejected.

The actual restore checks the archive's PostgreSQL format, copies/checks the files into private
new directories, revalidates the backup, creates the explicitly named database, and runs
`pg_restore --single-transaction --exit-on-error`. It then checks migration identifiers, selected
row counts, forced tenant RLS, restricted runtime role attributes and security-definer ownership.
Only after those checks pass does it write `<restorePath>/restore-report.json`. No `DROP`,
`--clean`, existing-database migration or automatic retry occurs. A failure leaves the new database
and/or storage isolated for explicit inspection; do not reuse the same destination.

No running API is repointed. A restored database contains real historical identities, approved
records and possibly enabled historical spend policies. Before a separate application smoke test,
use isolated local configuration, the restored `renders/` and `media/` directories, mock text mode,
`MEDIA_LIVE_ENABLED=false`, all external integration flags off, and no provider credentials.
Inspect tenant-scoped workflow/artifact/audit reads and authorized previews through a separately
started local API. Never interpret historical approval as new permission to publish or spend.
Checksum/count checks alone do not verify every application behavior or every approved asset's
current source freshness; retain the existing API QA/approval/export checks.

New backups use manifest schema version 2 and explicitly require all three storage roots.
Version 1 backups remain readable/restorable with only their historical render/media roots;
they do not contain social JPEGs or dispatch receipts. Do not use an old backup as evidence of
recoverability for platform operations added after it was captured. Disable social connection,
publishing and reply-dispatch flags and omit the social vault key from any restored test API.
The tool does not back up `.local/social-credentials.json` or the vault encryption key: store
those separately in the deployment's secret-management recovery process.

Unset the temporary credentials after maintenance:

```powershell
Remove-Item Env:MEDIAOS_BACKUP_DATABASE_URL
Remove-Item Env:MEDIAOS_RESTORE_DATABASE_URL
```

On WSL/Linux use absolute `/...` paths and `python3`. Prompt without echo instead of putting a
credential in shell history:

```bash
read -rsp 'Standalone maintenance URL: ' MEDIAOS_BACKUP_DATABASE_URL
export MEDIAOS_BACKUP_DATABASE_URL
read -rsp 'Disposable restore maintenance URL: ' MEDIAOS_RESTORE_DATABASE_URL
export MEDIAOS_RESTORE_DATABASE_URL
```

The same CLI flags and safety rules apply. Keep source, backup and restored storage directories
non-overlapping. Paths outside the workspace, symlinks/junctions, traversal, alternate data
streams, unlisted files, malformed manifests and checksum mismatches fail closed.

## Verification and remaining production work

Pure tests mock all PostgreSQL subprocesses and require no database or provider access:

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_backup_tool.py --noconftest -q
backend/.venv/Scripts/python.exe -m ruff check scripts/backup.py backend/tests/test_backup_tool.py
```

These tests cover secret-safe command construction, path confinement, corruption, quiescence
confirmation, no-overwrite behavior, dry-run, preservation of ownership/ACLs, guarded metadata
validation and failure retention. Pure tests do not establish actual restore success.

On **2026-09-21**, an actual standalone backup/restore rehearsal passed using the current
`milestone-completion` working tree through migration `0015`. The destination was the separate
database `mediaos_restore_connected_final_20260921`. The rehearsal preserved and verified **55 private
files**, schema `0015`, forced tenant RLS, restricted runtime-role attributes and guarded-function
ownership. Source database rows were not modified. The generated private backup manifest and
`restore-report.json` are the machine-readable evidence; no secrets are reproduced here.

A separate **read-only restored API smoke test passed** with all external flags disabled and no
provider keys: 38 tenant workflows were readable, exact artifact/audit reads and an authenticated
PNG preview passed, and unauthenticated/wrong-tenant requests returned 401. Evidence is saved in
`.local/restored-api-smoke-report.json`. The API checks performed no provider action. They do not
establish current source freshness for every historical asset or constitute production recovery
acceptance. See [MILESTONE_COMPLETION_REPORT.md](MILESTONE_COMPLETION_REPORT.md) for the aggregate
verification status and final source revision when committed.

Before multi-host production, implement durable private object storage, coordinated snapshots or
versioned object manifests, encrypted off-site backups, access/retention policies, credential
rotation, point-in-time recovery/WAL archiving, monitored automated backups, disk-capacity checks,
documented recovery objectives and regular isolated restore rehearsals. Establish external
alerting, runtime secret management, TLS, ingress controls and service runbooks separately. This
local tool and a passing application health endpoint do not establish production readiness.
