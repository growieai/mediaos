"""Offline deployment contract checks. Never contacts Docker, PostgreSQL or a provider."""

import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("mediaos_deployment", ROOT / "scripts/deployment.py")
assert SPEC and SPEC.loader
deployment = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = deployment
SPEC.loader.exec_module(deployment)
TOKEN = "test-credential-never-display-0123456789"
TENANT = UUID("5ab3a4bc-b909-43ad-a1f5-303fb09a65a4")


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Offline deployment tests must not use the network")

    monkeypatch.setattr(deployment.urllib.request, "build_opener", fail)


def write_private(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def config(tmp_path, monkeypatch):
    if hasattr(os, "getuid"):
        monkeypatch.setattr(deployment, "APP_UID", os.getuid())
    state = tmp_path / "state"
    tls = tmp_path / "tls"
    for path in [
        state,
        *(state / key for key in ("renders", "media", "social")),
        tls,
        tls / "data",
        tls / "config",
    ]:
        path.mkdir(mode=0o700)
    runtime_password = "runtime-only-0123456789abcdefghijklm"
    admin_password = "admin-only-0123456789abcdefghijklmnop"
    runtime = write_private(
        tmp_path / "runtime.env",
        f"DATABASE_URL=postgresql+psycopg://mediaos_runtime:{runtime_password}@postgres:5432/mediaos\n",
    )
    maintenance = write_private(
        tmp_path / "maintenance.env",
        f"MIGRATION_DATABASE_URL=postgresql+psycopg://mediaos_admin:{admin_password}@postgres:5432/mediaos\n"
        f"POSTGRES_RUNTIME_PASSWORD={runtime_password}\n",
    )
    password = write_private(tmp_path / "password", admin_password + "\n")
    values = {
        "API_IMAGE": "registry.acme.org/api@sha256:" + "a" * 64,
        "CONSOLE_IMAGE": "registry.acme.org/console@sha256:" + "b" * 64,
        "POSTGRES_IMAGE": "postgres@sha256:" + "c" * 64,
        "CADDY_IMAGE": "caddy@sha256:" + "d" * 64,
        "SITE_DOMAIN": "mediaos.acme.org",
        "ACME_EMAIL": "operations@acme.org",
        "OPS_ALLOWED_CIDRS": "192.0.2.10/32 2001:db8::1/128",
        "MEDIAOS_STATE_DIR": str(state),
        "MEDIAOS_TLS_DIR": str(tls),
        "POSTGRES_VOLUME": "mediaos_isolated_pg",
        "RUNTIME_ENV_FILE": str(runtime),
        "MAINTENANCE_ENV_FILE": str(maintenance),
        "POSTGRES_PASSWORD_FILE": str(password),
    }
    path = tmp_path / "deployment.env"
    write_private(path, "".join(f"{key}={value}\n" for key, value in values.items()))
    return path, values


def save_config(path, values):
    write_private(path, "".join(f"{key}={value}\n" for key, value in values.items()))


def test_preflight_validates_separated_secrets_and_storage(config):
    path, _ = config
    result = deployment.preflight(path)
    assert result["configuration_valid"] and result["credentials_separated"]
    assert result["private_storage_present"]
    assert not result["ingestion_identity_configured"]
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize(
    "key,value",
    [
        ("API_IMAGE", "mediaos:latest"),
        ("API_IMAGE", "mediaos@sha256:1234"),
        ("POSTGRES_IMAGE", "postgres:18.4"),
        ("OPS_ALLOWED_CIDRS", "0.0.0.0/0"),
        ("OPS_ALLOWED_CIDRS", "::/0"),
        ("OPS_ALLOWED_CIDRS", "192.0.2.1/24"),
        ("OPS_ALLOWED_CIDRS", "192.0.2.1/32 { respond hacked }"),
        ("SITE_DOMAIN", "mediaos.example.invalid"),
        ("SITE_DOMAIN", "host.acme.org { respond hacked }"),
        ("SITE_DOMAIN", "https://mediaos.acme.org"),
        ("SITE_DOMAIN", "127.0.0.1"),
        ("SITE_DOMAIN", "a..org"),
        ("ACME_EMAIL", "operator@example.invalid"),
        ("POSTGRES_VOLUME", "existing_business_production"),
    ],
)
def test_preflight_rejects_unsafe_or_placeholder_values(config, key, value):
    path, values = config
    values[key] = value
    save_config(path, values)
    with pytest.raises(deployment.CheckError):
        deployment.preflight(path)


@pytest.mark.parametrize(
    "extra", ["MIGRATION_DATABASE_URL", "OPENAI_API_KEY", "SOCIAL_PUBLISH_ENABLED"]
)
def test_runtime_file_rejects_admin_credentials_or_live_mode(config, extra):
    path, values = config
    runtime = Path(values["RUNTIME_ENV_FILE"])
    runtime.write_text(runtime.read_text() + f"{extra}={TOKEN}\n")
    with pytest.raises(deployment.CheckError):
        deployment.preflight(path)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://mediaos_admin:strong-value-000000000000000000000@postgres:5432/mediaos",
        "postgresql+psycopg://mediaos_runtime:strong-value-000000000000000000000@existing-growie:5432/mediaos",
        "postgresql+psycopg://mediaos_runtime:strong-value-000000000000000000000@postgres:5432/growie",
        "postgresql+psycopg://mediaos_runtime:short@postgres:5432/mediaos",
        "postgresql+psycopg://mediaos_runtime:strong-value-000000000000000000000@postgres:5432/mediaos?sslmode=disable",
    ],
)
def test_only_restricted_standalone_database_url_is_accepted(url):
    with pytest.raises(deployment.CheckError):
        deployment.db_password(url, "mediaos_runtime")


def test_database_passwords_must_match_without_sharing_identity(config):
    path, values = config
    write_private(Path(values["POSTGRES_PASSWORD_FILE"]), TOKEN)
    with pytest.raises(deployment.CheckError, match="credential files must agree"):
        deployment.preflight(path)


def test_secret_files_cannot_be_exposed_through_asset_mount(config):
    path, values = config
    inside = Path(values["MEDIAOS_STATE_DIR"]) / "runtime.env"
    write_private(inside, Path(values["RUNTIME_ENV_FILE"]).read_text())
    values["RUNTIME_ENV_FILE"] = str(inside)
    save_config(path, values)
    with pytest.raises(deployment.CheckError, match="mounts must not contain"):
        deployment.preflight(path)


@pytest.mark.parametrize("content", ["TOKEN=a\nTOKEN=b", 'TOKEN="value"', "TOKEN=", "TOKEN=a\x00b"])
def test_ambiguous_env_syntax_rejected(tmp_path, content):
    path = write_private(tmp_path / "env", content)
    with pytest.raises(deployment.CheckError):
        deployment.raw_env(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_world_readable_secret_rejected(tmp_path):
    path = write_private(tmp_path / "secret", TOKEN)
    path.chmod(0o644)
    with pytest.raises(deployment.CheckError, match="group or world"):
        deployment.private_file(path)


def test_preflight_failure_output_is_secret_safe(config, capsys):
    path, values = config
    write_private(Path(values["RUNTIME_ENV_FILE"]), f"{TOKEN}\n")
    assert deployment.main(["preflight", "--config", str(path)]) == 1
    output = capsys.readouterr().out
    assert TOKEN not in output
    assert str(path) not in output
    assert json.loads(output)["checks"] == {"configuration": False}


@pytest.mark.parametrize(
    "url",
    [
        "http://external.acme.org",
        "https://user:password@acme.org",
        "https://acme.org/?token=private",
        "https://acme.org/#secret",
        "https://acme.org:8443",
        "file:///etc/passwd",
    ],
)
def test_probe_rejects_credential_leak_targets(url):
    with pytest.raises(deployment.CheckError):
        deployment.api_base(url)


def test_probe_does_not_follow_redirects():
    with pytest.raises(deployment.CheckError):
        deployment.NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "https://attacker.org"
        )


def fake_response(raw, *, status=200, content_type="application/json"):
    headers = Message()
    headers["Content-Type"] = content_type
    response = SimpleNamespace(status=status, headers=headers, read=lambda maximum: raw[:maximum])

    class Context:
        def __enter__(self):
            return response

        def __exit__(self, *args):
            return False

    return Context()


def test_readiness_uses_explicit_headers_no_proxies_and_validates_contract(monkeypatch):
    seen = []

    def build(*handlers):
        assert isinstance(handlers[0], deployment.urllib.request.ProxyHandler)
        assert handlers[0].proxies == {}
        assert isinstance(handlers[1], deployment.NoRedirect)

        def open_request(request, timeout):
            seen.append((request, timeout))
            return fake_response(b'{"status":"ready","database":"ready","schema":"0015"}')

        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(deployment.urllib.request, "build_opener", build)
    assert deployment.readiness("https://mediaos.acme.org", TENANT, TOKEN)
    request, timeout = seen[0]
    assert request.full_url == "https://mediaos.acme.org/v1/readiness"
    assert request.get_header("Authorization") == "Bearer " + TOKEN
    assert request.get_header("X-tenant-id") == str(TENANT)
    assert timeout == 5


@pytest.mark.parametrize(
    "response",
    [
        b"not-json",
        b"[]",
        b'{"status":"ok"}',
        b"a" * 65537,
        b'{"status":"ready","database":"broken","schema":"0015"}',
    ],
    ids=["not-json", "not-object", "missing-fields", "oversized", "not-ready"],
)
def test_readiness_fails_closed_on_invalid_response(monkeypatch, response):
    monkeypatch.setattr(
        deployment.urllib.request,
        "build_opener",
        lambda *args: SimpleNamespace(open=lambda *args, **kwargs: fake_response(response)),
    )
    assert not deployment.readiness("http://127.0.0.1:8000", TENANT, TOKEN)


def write_manifest(tmp_path, created):
    path = tmp_path / "manifest.json"
    raw = json.dumps(
        {
            "schema_version": 2,
            "created_at": created.isoformat(),
            "consistency": "QUIESCED",
            "storage_roots": ["renders", "media", "social"],
        }
    )
    write_private(path, raw)
    write_private(tmp_path / "manifest.sha256", hashlib.sha256(raw.encode()).hexdigest())
    return path


def test_backup_monitor_requires_checksum_and_current_manifest(tmp_path):
    now = datetime.now(UTC)
    path = write_manifest(tmp_path, now - timedelta(hours=2))
    assert deployment.backup_age(path, now=now) == 7200
    path.write_text(path.read_text() + " ")
    with pytest.raises(deployment.CheckError, match="checksum"):
        deployment.backup_age(path, now=now)


def test_future_backup_timestamp_rejected(tmp_path):
    now = datetime.now(UTC)
    path = write_manifest(tmp_path, now + timedelta(hours=2))
    with pytest.raises(deployment.CheckError):
        deployment.backup_age(path, now=now)


def test_backup_checksum_covers_exact_line_endings(tmp_path):
    now = datetime.now(UTC)
    path = write_manifest(tmp_path, now)
    raw = path.read_bytes() + b"\r\n"
    path.write_bytes(raw)
    write_private(tmp_path / "manifest.sha256", hashlib.sha256(raw).hexdigest())
    assert deployment.backup_age(path, now=now) == 0


def test_probe_alerts_on_stale_backup_or_full_disk(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    path = write_manifest(tmp_path, now - timedelta(days=3))
    monkeypatch.setattr(deployment, "readiness", lambda *args: True)
    monkeypatch.setattr(
        deployment.shutil, "disk_usage", lambda path: SimpleNamespace(total=1000, free=50)
    )
    args = SimpleNamespace(
        token_file=write_private(tmp_path / "token", TOKEN),
        api_url="unused",
        tenant_id=TENANT,
        storage=[tmp_path],
        min_free_bytes=1,
        backup_manifest=path,
        max_backup_age_hours=26,
    )
    result = deployment.probe(args)
    assert not result["ok"]
    assert result["checks"] == {"readiness": True, "storage_0": False, "backup_fresh": False}
    assert TOKEN not in json.dumps(result)


def test_compose_contract_has_no_runtime_admin_or_public_dependency_ports():
    spec = yaml.safe_load((ROOT / "deploy/compose.standalone.yml").read_text())
    services = spec["services"]
    assert {name for name, value in services.items() if "ports" in value} == {"ingress"}
    assert services["ingress"]["ports"] == ["80:80", "443:443"]
    assert services["postgres"]["networks"] == ["private"]
    assert spec["networks"]["private"]["internal"] is True
    for name in ("api", "console", "ingress"):
        assert services[name]["user"] == "1000:1000"
        assert services[name]["read_only"] is True
        assert services[name]["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in services[name]["security_opt"]
    for name in ("migrate", "seed"):
        assert services[name]["profiles"] == ["maintenance"]
        assert services[name]["env_file"][0]["format"] == "raw"
    assert "RUNTIME_ENV_FILE" in services["api"]["env_file"][0]["path"]
    assert "MAINTENANCE_ENV_FILE" not in json.dumps(services["api"])
    assert "secrets" not in services["api"]
    assert spec["volumes"]["postgres_data"]["external"] is True
    for service in services.values():
        assert "build" not in service
        for volume in service.get("volumes", []):
            if isinstance(volume, dict) and volume.get("type") == "bind":
                assert volume["bind"]["create_host_path"] is False
    flags = services["api"]["environment"]
    for key in (
        "MEDIA_LIVE_ENABLED",
        "SOCIAL_CONNECT_ENABLED",
        "SOCIAL_PUBLISH_ENABLED",
        "SOCIAL_REPLY_ENABLED",
        "CONVERSION_DELIVERY_ENABLED",
        "ENABLE_AUTO_PUBLISH",
        "ENABLE_AUTO_REPLIES",
        "ENABLE_EXTERNAL_CREATORS",
    ):
        assert flags[key] == "false"
    assert flags["AI_MOCK_MODE"] == "true"


def test_ingress_never_serves_assets_or_trusts_forwarded_ip():
    caddy = (ROOT / "deploy/Caddyfile").read_text()
    assert "@operator remote_ip {$OPS_ALLOWED_CIDRS}" in caddy
    assert "file_server" not in caddy
    assert "trusted_proxies" not in caddy
    assert "output discard" in caddy
    assert "request_body" in caddy and "max_size 256KB" in caddy
    assert 'respond "Not found" 404' in caddy
