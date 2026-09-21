"""Explicit, offline-safe backup and disposable restore for standalone Media OS.

Uses only the standard library. Credentials are read from dedicated environment
variables and passed to PostgreSQL tools through their environment, never argv.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


class BackupError(Exception):
    """A deliberately secret-free operational error."""


@dataclass(frozen=True)
class Database:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False, compare=False)

    def public(self) -> dict:
        return {
            key: getattr(self, key) for key in ("host", "port", "database", "username")
        }

    def environment(self, maintenance: bool = False) -> dict[str, str]:
        # Ignore inherited PGOPTIONS/PGSERVICE/PGHOSTADDR/etc. A service file or
        # hostile search_path must not redirect this explicitly selected target.
        allowed = {
            "PATH",
            "SYSTEMROOT",
            "SYSTEMDRIVE",
            "WINDIR",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "LANG",
            "LC_ALL",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        env.update(
            PGHOST=self.host,
            PGPORT=str(self.port),
            PGDATABASE="postgres" if maintenance else self.database,
            PGUSER=self.username,
            PGPASSWORD=self.password,
            PGCONNECT_TIMEOUT="10",
            PGSSLMODE="disable",  # loopback only; this tool is not a remote backup client
            PGAPPNAME="mediaos-disposable-backup",
        )
        return env


def database_from_env(name: str) -> Database:
    try:
        value = os.environ[name]
        url = urlsplit(value)
        username, password = unquote(url.username or ""), unquote(url.password or "")
        database = unquote(url.path.removeprefix("/"))
        if (
            url.scheme not in {"postgresql", "postgresql+psycopg"}
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.port is None
            or not 1024 <= url.port <= 65535
            or username != "mediaos_admin"
            or not password
            or any(c in password for c in "\x00\r\n")
            or not re.fullmatch(r"mediaos(?:_[a-z0-9_]{1,45})?", database)
            or url.query
            or url.fragment
        ):
            raise ValueError
        return Database(url.hostname, url.port, database, username, password)
    except (KeyError, ValueError, TypeError):
        raise BackupError(
            f"{name} must name an explicit loopback standalone Media OS database, "
            "port and mediaos_admin credential; URL query options are forbidden"
        ) from None


def no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise BackupError("Symlinks and directory junctions are forbidden")


def workspace_path(workspace: Path, path: Path, *, existing: bool = False) -> Path:
    if not workspace.is_absolute() or not path.is_absolute():
        raise BackupError("Workspace and storage paths must be absolute")
    no_links(workspace)
    no_links(path)
    root, target = workspace.resolve(strict=True), path.resolve(strict=False)
    if not root.is_dir() or target == root or not target.is_relative_to(root):
        raise BackupError("Storage must be strictly inside the chosen workspace")
    if existing and not target.is_dir():
        raise BackupError("Required private storage directory does not exist")
    return target


def disjoint(*paths: Path) -> None:
    for n, left in enumerate(paths):
        for right in paths[n + 1 :]:
            if (
                left == right
                or left.is_relative_to(right)
                or right.is_relative_to(left)
            ):
                raise BackupError(
                    "Backup, render and media directories must not overlap"
                )


def safe_relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or any(c in value for c in '\\:<>"|?*'):
        raise BackupError("Invalid manifest file path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(
            part in {"", ".", ".."} or part.rstrip(". ") != part for part in path.parts
        )
        or any(
            re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p)
            for p in path.parts
        )
        or any(ord(c) < 32 for c in value)
    ):
        raise BackupError("Invalid manifest file path")
    return path


def files_under(root: Path) -> list[Path]:
    no_links(root)
    result = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in (*dirs, *names):
            path = Path(directory) / name
            no_links(path)
            mode = path.stat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise BackupError("Only regular files and directories can be backed up")
            if stat.S_ISREG(mode):
                safe_relative(path.relative_to(root).as_posix())
                result.append(path)
    return sorted(result)


def digest(path: Path) -> tuple[int, str]:
    no_links(path)
    if not path.is_file():
        raise BackupError("Required backup file is missing")
    sha, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
            size += len(chunk)
    return size, sha.hexdigest()


def private_directory(path: Path) -> None:
    no_links(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=False)


def write_new(path: Path, content: bytes) -> None:
    no_links(path)
    with path.open("xb") as handle:
        if os.name != "nt":
            path.chmod(0o600)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def copy_new(source: Path, destination: Path) -> None:
    no_links(source)
    no_links(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        if os.name != "nt":
            destination.chmod(0o600)
        shutil.copyfileobj(src, dst, length=1024 * 1024)


def inventory(root: Path, label: str) -> list[dict]:
    result = []
    for path in files_under(root):
        size, sha = digest(path)
        result.append(
            {
                "path": f"{label}/{path.relative_to(root).as_posix()}",
                "size": size,
                "sha256": sha,
            }
        )
    return result


def tools(pg_bin: Path | None) -> dict[str, str]:
    if pg_bin is not None and not pg_bin.is_absolute():
        raise BackupError("--pg-bin must be an absolute PostgreSQL binary directory")
    result = {}
    for name in ("pg_dump", "pg_restore", "psql"):
        candidate = (
            (pg_bin / (name + (".exe" if os.name == "nt" else ""))) if pg_bin else None
        )
        location = shutil.which(str(candidate) if candidate else name)
        if not location:
            raise BackupError(f"Required PostgreSQL executable is unavailable: {name}")
        result[name] = location
    return result


def invoke(
    executable: str, args: list[str], db: Database, *, maintenance: bool = False
) -> str:
    try:
        result = subprocess.run(
            [executable, *args],
            env=db.environment(maintenance),
            check=False,
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BackupError(
            "PostgreSQL tool did not complete; inspect local service availability"
        ) from None
    if result.returncode:
        # pg_dump/psql stderr can contain SQL, row data and connection details.
        raise BackupError(
            "PostgreSQL tool failed; partial output is retained and must not be reused"
        )
    return result.stdout.strip()


METADATA_SQL = """
SELECT json_build_object(
 'server_version', current_setting('server_version_num')::integer,
 'migrations', (SELECT json_agg(version_num ORDER BY version_num) FROM public.alembic_version),
 'counts', json_build_object(
   'tenants', (SELECT count(*) FROM public.tenants),
   'workflow_runs', (SELECT count(*) FROM public.workflow_runs),
   'content_asset_versions', (SELECT count(*) FROM public.content_asset_versions),
   'approval_records', (SELECT count(*) FROM public.approval_records),
   'audit_events', (SELECT count(*) FROM public.audit_events)),
 'runtime_restricted', (SELECT NOT rolsuper AND NOT rolbypassrls AND NOT rolcreatedb
   AND NOT rolcreaterole AND NOT rolinherit FROM pg_roles WHERE rolname='mediaos_runtime'),
 'forced_rls', (SELECT bool_and(c.relrowsecurity AND c.relforcerowsecurity)
   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relkind='r' AND EXISTS
     (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='tenant_id')),
 'guard_owners', (SELECT bool_and(pg_get_userbyid(p.proowner)='mediaos_admin')
   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
   WHERE n.nspname IN ('public','private') AND p.prosecdef))
"""
COUNTS = {
    "tenants",
    "workflow_runs",
    "content_asset_versions",
    "approval_records",
    "audit_events",
}


def validate_metadata(value: dict) -> None:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "server_version",
            "migrations",
            "counts",
            "runtime_restricted",
            "forced_rls",
            "guard_owners",
        }
        or type(value["server_version"]) is not int
        or value["server_version"] < 180000
        or not isinstance(value["migrations"], list)
        or not value["migrations"]
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,32}", v)
            for v in value["migrations"]
        )
        or not isinstance(value["counts"], dict)
        or set(value["counts"]) != COUNTS
        or any(type(v) is not int or v < 0 for v in value["counts"].values())
        or any(
            value[k] is not True
            for k in ("runtime_restricted", "forced_rls", "guard_owners")
        )
    ):
        raise BackupError(
            "Database identity, restricted runtime role or RLS/guard validation failed"
        )


def metadata(db: Database, executables: dict) -> dict:
    raw = invoke(
        executables["psql"],
        ["-X", "--no-password", "-v", "ON_ERROR_STOP=1", "-At", "-c", METADATA_SQL],
        db,
    )
    try:
        result = json.loads(raw)
    except (ValueError, TypeError):
        raise BackupError("Database metadata response was invalid") from None
    validate_metadata(result)
    return result


def create_backup(
    db: Database,
    workspace: Path,
    output: Path,
    renders: Path,
    media: Path,
    executables: dict,
    *,
    confirmed: str,
    quiesced: bool,
    dry_run: bool = False,
    social: Path | None = None,
) -> dict:
    if confirmed != db.database or not quiesced:
        raise BackupError(
            "Confirm the standalone source database and stopped application writers"
        )
    output = workspace_path(workspace, output)
    roots = {
        label: workspace_path(workspace, path, existing=True)
        for label, path in (
            ("renders", renders),
            ("media", media),
            ("social", social or workspace / ".local/social"),
        )
    }
    disjoint(output, *roots.values())
    if output.exists():
        raise BackupError("Backup destination already exists; overwriting is forbidden")
    before = [
        entry for label, root in roots.items() for entry in inventory(root, label)
    ]
    if dry_run:
        return {
            "action": "backup",
            "dry_run": True,
            "source": db.public(),
            "storage_files": len(before),
            "output": str(output),
        }
    original = metadata(db, executables)
    private_directory(output)
    for label in roots:
        private_directory(output / label)
    dump = output / "database.dump"
    invoke(
        executables["pg_dump"],
        ["--no-password", "--format=custom", "--file", str(dump)],
        db,
    )
    if os.name != "nt":
        dump.chmod(0o600)
    for entry in before:
        part = PurePosixPath(entry["path"])
        origin = roots[part.parts[0]]
        copy_new(origin.joinpath(*part.parts[1:]), output.joinpath(*part.parts))
    after = [entry for label, root in roots.items() for entry in inventory(root, label)]
    copied = [entry for label in roots for entry in inventory(output / label, label)]
    if before != after or copied != before or metadata(db, executables) != original:
        raise BackupError(
            "Source changed during backup; stop all writers and create a fresh backup"
        )
    size, sha = digest(dump)
    if size == 0:
        raise BackupError("Database dump is empty")
    manifest: dict = {
        "schema_version": 2,
        "storage_roots": list(roots),
        "created_at": datetime.now(UTC).isoformat(),
        "source": db.public(),
        "consistency": "QUIESCED",
        "database_metadata": original,
        "files": [{"path": "database.dump", "size": size, "sha256": sha}, *copied],
    }
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    write_new(output / "manifest.json", raw)
    write_new(
        output / "manifest.sha256", hashlib.sha256(raw).hexdigest().encode() + b"\n"
    )
    verify_backup(workspace, output)
    return {
        "action": "backup",
        "dry_run": False,
        "output": str(output),
        "files": len(manifest["files"]),
    }


def verify_backup(workspace: Path, backup: Path) -> dict:
    backup = workspace_path(workspace, backup, existing=True)
    manifest_path = backup / "manifest.json"
    no_links(manifest_path)
    no_links(backup / "manifest.sha256")
    if not manifest_path.is_file() or manifest_path.stat().st_size > 20_000_000:
        raise BackupError("Backup manifest is missing or too large")
    raw = manifest_path.read_bytes()
    checksum_path = backup / "manifest.sha256"
    if checksum_path.stat().st_size > 65:
        raise BackupError("Manifest checksum file is invalid")
    if checksum_path.read_text().strip() != hashlib.sha256(raw).hexdigest():
        raise BackupError("Manifest checksum does not match")
    try:
        manifest = json.loads(raw)
        if not isinstance(manifest, dict):
            raise ValueError
        roots = ["renders", "media"]
        keys = {
            "schema_version",
            "created_at",
            "source",
            "consistency",
            "database_metadata",
            "files",
        }
        if manifest.get("schema_version") == 2:
            keys.add("storage_roots")
            roots.append("social")
            if manifest.get("storage_roots") != roots:
                raise ValueError
        if set(manifest) != keys:
            raise ValueError
        if (
            type(manifest["schema_version"]) is not int
            or manifest["schema_version"] not in {1, 2}
            or manifest["consistency"] != "QUIESCED"
        ):
            raise ValueError
        created = datetime.fromisoformat(manifest["created_at"])
        offset = created.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError
        source = manifest["source"]
        if not isinstance(source, dict) or set(source) != {
            "host",
            "port",
            "database",
            "username",
        }:
            raise ValueError
        if (
            source["host"] not in {"localhost", "127.0.0.1", "::1"}
            or type(source["port"]) is not int
            or not 1024 <= source["port"] <= 65535
            or source["username"] != "mediaos_admin"
            or not re.fullmatch(r"mediaos(?:_[a-z0-9_]{1,45})?", source["database"])
        ):
            raise ValueError
        entries = manifest["files"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100_000:
            raise ValueError
        expected = {"manifest.json", "manifest.sha256"}
        folded = {p.casefold() for p in expected}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
                raise ValueError
            relative = safe_relative(entry["path"])
            if relative != PurePosixPath("database.dump") and (
                len(relative.parts) < 2 or relative.parts[0] not in roots
            ):
                raise ValueError
            if (
                str(relative).casefold() in folded
                or type(entry["size"]) is not int
                or entry["size"] < 0
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            ):
                raise ValueError
            expected.add(str(relative))
            folded.add(str(relative).casefold())
            if digest(backup.joinpath(*relative.parts)) != (
                entry["size"],
                entry["sha256"],
            ):
                raise BackupError("Backup file checksum or size does not match")
        if (
            "database.dump" not in expected
            or (backup / "database.dump").stat().st_size == 0
        ):
            raise ValueError
        actual = {p.relative_to(backup).as_posix() for p in files_under(backup)}
        if actual != expected:
            raise BackupError("Backup contains missing or unlisted files")
        for label in roots:
            if not (backup / label).is_dir():
                raise ValueError
        validate_metadata(manifest["database_metadata"])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise BackupError("Backup manifest schema is invalid") from None
    return manifest


def restore_backup(
    db: Database,
    workspace: Path,
    backup: Path,
    destination: Path,
    executables: dict,
    *,
    confirmed: str,
    dry_run: bool = False,
) -> dict:
    manifest = verify_backup(workspace, backup)
    if (
        not re.fullmatch(r"mediaos_restore_[a-z0-9_]{1,30}", db.database)
        or confirmed != db.database
        or db.database == manifest["source"]["database"]
    ):
        raise BackupError(
            "Restore requires a different, explicitly confirmed mediaos_restore_* database"
        )
    destination = workspace_path(workspace, destination)
    backup = workspace_path(workspace, backup, existing=True)
    disjoint(backup, destination)
    if destination.exists():
        raise BackupError(
            "Restore storage destination already exists; overwriting is forbidden"
        )
    plan = {
        "action": "restore",
        "dry_run": dry_run,
        "destination": db.public(),
        "storage": str(destination),
        "files": len(manifest["files"]),
    }
    if dry_run:
        return plan
    # The restricted name grammar makes these identifiers/literals safe. No
    # caller SQL, connection URL, password or shell interpolation is used.
    exists = invoke(
        executables["psql"],
        [
            "-X",
            "--no-password",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            f"SELECT 1 FROM pg_database WHERE datname='{db.database}'",
        ],
        db,
        maintenance=True,
    )
    if exists:
        raise BackupError("Restore database already exists; overwriting is forbidden")
    invoke(executables["pg_restore"], ["--list", str(backup / "database.dump")], db)
    private_directory(destination)
    for label in manifest.get("storage_roots", ["renders", "media"]):
        private_directory(destination / label)
    for entry in manifest["files"]:
        if entry["path"] != "database.dump":
            relative = PurePosixPath(entry["path"])
            source, target = (
                backup.joinpath(*relative.parts),
                destination.joinpath(*relative.parts),
            )
            copy_new(source, target)
            if digest(target) != (entry["size"], entry["sha256"]):
                raise BackupError("Restore storage integrity check failed")
    # Recheck the dump after staging files, before any database mutation.
    verify_backup(workspace, backup)
    invoke(
        executables["psql"],
        [
            "-X",
            "--no-password",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f'CREATE DATABASE "{db.database}" OWNER mediaos_admin TEMPLATE template0',
        ],
        db,
        maintenance=True,
    )
    invoke(
        executables["pg_restore"],
        [
            "--no-password",
            "--exit-on-error",
            "--single-transaction",
            "--dbname",
            db.database,
            str(backup / "database.dump"),
        ],
        db,
    )
    restored = metadata(db, executables)
    original = manifest["database_metadata"]
    if (
        restored["migrations"] != original["migrations"]
        or restored["counts"] != original["counts"]
    ):
        raise BackupError(
            "Restore database verification failed; keep destination isolated"
        )
    write_new(
        destination / "restore-report.json",
        (
            json.dumps(
                {
                    **plan,
                    "verified_at": datetime.now(UTC).isoformat(),
                    "database_metadata": restored,
                },
                indent=2,
            )
            + "\n"
        ).encode(),
    )
    return {**plan, "verified": True}


def readiness(
    workspace: Path,
    renders: Path,
    media: Path,
    pg_bin: Path | None,
    social: Path | None = None,
) -> dict:
    checks = []
    for name, operation in (
        (
            "source_configuration",
            lambda: database_from_env("MEDIAOS_BACKUP_DATABASE_URL"),
        ),
        ("postgresql_executables", lambda: tools(pg_bin)),
        (
            "render_storage",
            lambda: inventory(
                workspace_path(workspace, renders, existing=True), "renders"
            ),
        ),
        (
            "media_storage",
            lambda: inventory(workspace_path(workspace, media, existing=True), "media"),
        ),
        (
            "social_storage",
            lambda: inventory(
                workspace_path(
                    workspace, social or workspace / ".local/social", existing=True
                ),
                "social",
            ),
        ),
    ):
        try:
            operation()
            checks.append({"check": name, "ready": True})
        except (BackupError, OSError):
            checks.append({"check": name, "ready": False})
    return {
        "ready": all(v["ready"] for v in checks),
        "database_contacted": False,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "create", "verify", "restore"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--pg-bin", type=Path)
    parser.add_argument("--renders", type=Path)
    parser.add_argument("--media", type=Path)
    parser.add_argument("--social", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--confirm-standalone")
    parser.add_argument("--confirm-quiesced", action="store_true")
    parser.add_argument("--confirm-disposable")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    renders, media = (
        args.renders or args.workspace / ".local/renders",
        args.media or args.workspace / ".local/media",
    )
    try:
        if args.action == "check":
            result = readiness(args.workspace, renders, media, args.pg_bin, args.social)
        elif not args.backup:
            raise BackupError("--backup is required")
        elif args.action == "verify":
            manifest = verify_backup(args.workspace, args.backup)
            result = {
                "verified": True,
                "files": len(manifest["files"]),
                "database_contacted": False,
            }
        elif args.action == "create":
            db = database_from_env("MEDIAOS_BACKUP_DATABASE_URL")
            result = create_backup(
                db,
                args.workspace,
                args.backup,
                renders,
                media,
                {} if args.dry_run else tools(args.pg_bin),
                confirmed=args.confirm_standalone,
                quiesced=args.confirm_quiesced,
                dry_run=args.dry_run,
                social=args.social,
            )
        else:
            if not args.destination:
                raise BackupError("--destination is required for restore storage")
            db = database_from_env("MEDIAOS_RESTORE_DATABASE_URL")
            result = restore_backup(
                db,
                args.workspace,
                args.backup,
                args.destination,
                {} if args.dry_run else tools(args.pg_bin),
                confirmed=args.confirm_disposable,
                dry_run=args.dry_run,
            )
        print(json.dumps(result, indent=2))
        return 0 if result.get("ready", True) else 1
    except BackupError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except OSError:
        print(
            json.dumps(
                {"error": "Filesystem operation failed; partial output is retained"}
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
