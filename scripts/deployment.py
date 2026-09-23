"""Secret-safe checks for the standalone deployment. No deployment or credential mutation."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

CONFIG_KEYS = {
    "API_IMAGE",
    "CONSOLE_IMAGE",
    "POSTGRES_IMAGE",
    "CADDY_IMAGE",
    "SITE_DOMAIN",
    "ACME_EMAIL",
    "OPS_ALLOWED_CIDRS",
    "MEDIAOS_STATE_DIR",
    "MEDIAOS_TLS_DIR",
    "POSTGRES_VOLUME",
    "RUNTIME_ENV_FILE",
    "MAINTENANCE_ENV_FILE",
    "POSTGRES_PASSWORD_FILE",
}
RUNTIME_KEYS = {"DATABASE_URL", "INTELLIGENCE_TOKENS"}
MAINTENANCE_KEYS = {"MIGRATION_DATABASE_URL", "POSTGRES_RUNTIME_PASSWORD"}
DIGEST = re.compile(r"[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}\Z")
HOSTNAME = re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
APP_UID = 1000


class CheckError(Exception):
    """Only fixed, non-sensitive failure text may cross the CLI boundary."""


def no_links(path: Path) -> None:
    if not path.is_absolute():
        raise CheckError("Use absolute paths")
    for current in (path, *path.parents):
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise CheckError("Linked paths are not allowed")


def private_file(path: Path, *, secret: bool = True, limit: int = 65536) -> str:
    no_links(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise CheckError("Configuration file missing or too large")
    if secret and os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise CheckError("Secret file must not be group or world accessible")
    # Preserve exact line endings when this reader is used for manifest hashing.
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def raw_env(path: Path, *, secret: bool = True) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in private_file(path, secret=secret).splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or not value
            or value != value.strip()
            or value.startswith(("'", '"'))
            or any(ord(char) < 32 for char in value)
        ):
            raise CheckError("Use unique raw KEY=value entries without quotes")
        values[key] = value
    return values


def check_hostname(value: str) -> None:
    if (
        not HOSTNAME.fullmatch(value)
        or "." not in value
        or ".." in value
        or any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
            for label in value.split(".")
        )
        or value.endswith((".invalid", ".localhost", ".example", ".test"))
        or value in {"example.com", "example.org", "example.net"}
    ):
        raise CheckError("A real dedicated DNS hostname is required")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return
    raise CheckError("Use a DNS hostname, not an IP address")


def check_values(values: dict[str, str]) -> None:
    if set(values) != CONFIG_KEYS:
        raise CheckError("Deployment configuration keys are missing or unexpected")
    for name in ("API_IMAGE", "CONSOLE_IMAGE", "POSTGRES_IMAGE", "CADDY_IMAGE"):
        if not DIGEST.fullmatch(values[name]) or "example.invalid" in values[name]:
            raise CheckError("Every image must use an independently verified sha256 digest")
    check_hostname(values["SITE_DOMAIN"])
    if not re.fullmatch(r"[a-zA-Z0-9._+%-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", values["ACME_EMAIL"]):
        raise CheckError("A certificate operator email is required")
    if values["ACME_EMAIL"].endswith((".invalid", "@example.com", "@example.org")):
        raise CheckError("Replace example certificate operator email")
    cidrs = values["OPS_ALLOWED_CIDRS"].split(" ")
    if not 1 <= len(cidrs) <= 16:
        raise CheckError("Supply between one and sixteen operator CIDRs")
    try:
        for cidr in cidrs:
            network = ipaddress.ip_network(cidr, strict=True)
            if network.prefixlen == 0 or network.is_unspecified or network.is_multicast:
                raise ValueError
    except ValueError:
        raise CheckError("Operator CIDRs must be explicit restricted networks") from None
    if not re.fullmatch(r"mediaos_[a-z0-9_]{1,64}", values["POSTGRES_VOLUME"]):
        raise CheckError("Use an explicitly named standalone Media OS volume")
    # Compose interpolation must not reinterpret $ from deployment values.
    if any("$" in value or "`" in value for value in values.values()):
        raise CheckError("Deployment interpolation values cannot contain shell expansions")


def db_password(value: str, username: str) -> str:
    try:
        url = urlsplit(value)
        password = unquote(url.password or "")
        if (
            url.scheme != "postgresql+psycopg"
            or url.username != username
            or url.hostname != "postgres"
            or url.port != 5432
            or url.path != "/mediaos"
            or url.query
            or url.fragment
            or len(password) < 32
            or any(ord(char) < 32 for char in password)
        ):
            raise ValueError
        return password
    except (ValueError, TypeError):
        raise CheckError("Use a strong standalone Compose database credential") from None


def preflight(path: Path) -> dict[str, bool]:
    values = raw_env(path, secret=False)
    check_values(values)
    runtime_path = Path(values["RUNTIME_ENV_FILE"])
    maintenance_path = Path(values["MAINTENANCE_ENV_FILE"])
    password_path = Path(values["POSTGRES_PASSWORD_FILE"])
    if len({runtime_path, maintenance_path, password_path}) != 3:
        raise CheckError("Runtime and maintenance secrets must be separate files")
    runtime = raw_env(runtime_path)
    maintenance = raw_env(maintenance_path)
    if not {"DATABASE_URL"} <= set(runtime) <= RUNTIME_KEYS:
        raise CheckError(
            "Runtime file may contain only restricted database and ingestion credentials"
        )
    if set(maintenance) != MAINTENANCE_KEYS:
        raise CheckError("Maintenance credentials must be separate and complete")
    runtime_password = db_password(runtime["DATABASE_URL"], "mediaos_runtime")
    admin_password = db_password(maintenance["MIGRATION_DATABASE_URL"], "mediaos_admin")
    supplied_admin = private_file(password_path).removesuffix("\n").removesuffix("\r")
    if (
        runtime_password != maintenance["POSTGRES_RUNTIME_PASSWORD"]
        or admin_password != supplied_admin
        or admin_password == runtime_password
    ):
        raise CheckError("Separate database credential files must agree")
    if "INTELLIGENCE_TOKENS" in runtime:
        try:
            tokens = json.loads(runtime["INTELLIGENCE_TOKENS"])
            if not isinstance(tokens, dict) or not 1 <= len(tokens) <= 100:
                raise ValueError
            for tenant, token in tokens.items():
                UUID(tenant)
                if not isinstance(token, str) or not 32 <= len(token) <= 512:
                    raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise CheckError("Invalid tenant ingestion credential map") from None
    state = Path(values["MEDIAOS_STATE_DIR"])
    tls = Path(values["MEDIAOS_TLS_DIR"])
    for directory in (state, tls):
        no_links(directory)
    if state == tls or state in tls.parents or tls in state.parents:
        raise CheckError("Asset and certificate storage must be separate")
    for secret in (runtime_path, maintenance_path, password_path):
        if state in secret.parents or tls in secret.parents:
            raise CheckError("Runtime asset mounts must not contain deployment secrets")
    for directory in (
        state,
        *(state / name for name in ("renders", "media", "social")),
        tls,
        tls / "data",
        tls / "config",
    ):
        no_links(directory)
        if not directory.is_dir():
            raise CheckError("Private persistent directories must already exist")
        if os.name == "posix" and (
            directory.stat().st_uid != APP_UID or stat.S_IMODE(directory.stat().st_mode) & 0o077
        ):
            raise CheckError(
                "Persistent directories must be private and owned by container UID 1000"
            )
    return {
        "configuration_valid": True,
        "credentials_separated": True,
        "private_storage_present": True,
        "ingestion_identity_configured": "INTELLIGENCE_TOKENS" in runtime,
        "linux_host": sys.platform.startswith("linux"),
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckError("Probe redirects are refused")


def api_base(value: str) -> str:
    try:
        url = urlsplit(value)
        if url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError
        if url.scheme == "https":
            check_hostname(url.hostname or "")
            if url.port not in {None, 443}:
                raise ValueError
        elif url.scheme == "http":
            if url.hostname not in {"localhost", "127.0.0.1", "::1"} or not url.port:
                raise ValueError
        else:
            raise ValueError
    except (ValueError, TypeError):
        raise CheckError("Probe requires HTTPS or an explicit loopback HTTP port") from None
    return value.rstrip("/")


def readiness(base: str, tenant: UUID, token: str, *, timeout: float = 5) -> bool:
    base = api_base(base)
    if not 32 <= len(token) <= 512 or any(char.isspace() or ord(char) < 32 for char in token):
        raise CheckError("Invalid monitoring credential")
    request = urllib.request.Request(
        base + "/v1/readiness",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": str(tenant)},
    )
    # Do not forward bearer credentials through environment-configured proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                return False
            raw = response.read(65537)
            if len(raw) > 65536:
                return False
            value = json.loads(raw)
            return (
                isinstance(value, dict)
                and value.get("status") == "ready"
                and value.get("database") == "ready"
                and isinstance(value.get("schema"), str)
            )
    except Exception:
        # No remote body, URL, header or exception text reaches logs.
        return False


def backup_age(manifest_path: Path, *, now: datetime) -> float:
    raw = private_file(manifest_path, limit=20_000_000).encode("utf-8")
    checksum = private_file(manifest_path.with_name("manifest.sha256"), limit=65).strip()
    if checksum != hashlib.sha256(raw).hexdigest():
        raise CheckError("Backup manifest checksum mismatch")
    try:
        value = json.loads(raw)
        created = datetime.fromisoformat(value["created_at"])
        if (
            value.get("schema_version") != 2
            or value.get("consistency") != "QUIESCED"
            or value.get("storage_roots") != ["renders", "media", "social"]
            or created.utcoffset() is None
            or created.utcoffset().total_seconds() != 0
        ):
            raise ValueError
        age = (now - created).total_seconds()
        if age < -300:
            raise ValueError
        return max(0.0, age)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise CheckError("Invalid completed backup manifest") from None


def probe(args) -> dict:
    token = private_file(args.token_file, limit=513).strip()
    checks = {"readiness": readiness(args.api_url, args.tenant_id, token)}
    metrics: dict[str, float] = {}
    for index, directory in enumerate(args.storage):
        try:
            no_links(directory)
            if not directory.is_dir():
                raise CheckError("Missing storage directory")
            disk = shutil.disk_usage(directory)
            ratio = disk.free / disk.total
            checks[f"storage_{index}"] = ratio >= 0.1 and disk.free >= args.min_free_bytes
            metrics[f"storage_{index}_free_bytes"] = disk.free
        except (OSError, CheckError, ZeroDivisionError):
            checks[f"storage_{index}"] = False
    try:
        age = backup_age(args.backup_manifest, now=datetime.now(UTC))
        checks["backup_fresh"] = age <= args.max_backup_age_hours * 3600
        metrics["backup_age_seconds"] = age
    except (OSError, CheckError, UnicodeError):
        checks["backup_fresh"] = False
    return {"ok": all(checks.values()), "checks": checks, "metrics": metrics}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("preflight")
    check.add_argument("--config", type=Path, required=True)
    monitor = commands.add_parser("probe")
    monitor.add_argument("--api-url", required=True)
    monitor.add_argument("--tenant-id", required=True, type=UUID)
    monitor.add_argument("--token-file", required=True, type=Path)
    monitor.add_argument("--storage", required=True, type=Path, action="append")
    monitor.add_argument("--backup-manifest", required=True, type=Path)
    monitor.add_argument("--max-backup-age-hours", type=int, default=26, choices=range(1, 169))
    monitor.add_argument("--min-free-bytes", type=int, default=1_073_741_824)
    monitor.add_argument("--format", choices=("json", "prometheus"), default="json")
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            checks = preflight(args.config)
            # Ingestion map is added after initial seed, not fabricated during bootstrap.
            required = {
                key: value
                for key, value in checks.items()
                if key != "ingestion_identity_configured"
            }
            result = {"ok": all(required.values()), "checks": checks, "metrics": {}}
        else:
            if args.min_free_bytes < 1:
                raise CheckError("Free disk threshold must be positive")
            result = probe(args)
    except (OSError, UnicodeError, CheckError):
        result = {"ok": False, "checks": {"configuration": False}, "metrics": {}}
    if getattr(args, "format", "json") == "prometheus":
        for key, value in result["checks"].items():
            print(f'mediaos_operation_check{{check="{key}"}} {int(value)}')
        for key, value in result["metrics"].items():
            print(f"mediaos_operation_{key} {value}")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
