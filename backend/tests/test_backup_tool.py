"""Pure operations-tool tests: no PostgreSQL process or network may be invoked."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "mediaos_backup_tool", Path(__file__).resolve().parents[2] / "scripts/backup.py"
)
assert SPEC and SPEC.loader
backup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backup
SPEC.loader.exec_module(backup)

SECRET = "private-test-secret-do-not-display"
SOURCE = backup.Database("127.0.0.1", 55432, "mediaos_dev", "mediaos_admin", SECRET)
TARGET = backup.Database("127.0.0.1", 55432, "mediaos_restore_rehearsal", "mediaos_admin", SECRET)
TOOLS = {name: name for name in ("pg_dump", "pg_restore", "psql")}
METADATA = {
    "server_version": 180004,
    "migrations": ["0008"],
    "counts": dict.fromkeys(backup.COUNTS, 3),
    "runtime_restricted": True,
    "forced_rls": True,
    "guard_owners": True,
}


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


@pytest.fixture(autouse=True)
def forbid_real_process(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure operations tests cannot launch external processes")

    monkeypatch.setattr(backup.subprocess, "run", forbidden)


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    for name in ("renders", "media", "social"):
        (root / ".local" / name).mkdir(parents=True)
    (root / ".local/renders/slide.png").write_bytes(b"private-render-bytes")
    (root / ".local/media/video.mp4").write_bytes(b"private-media-bytes")
    (root / ".local/social/image-1.jpg").write_bytes(b"private-social-bytes")
    (root / ".env").write_text("SECRET=not-backed-up")
    (root / ".local/credentials.json").write_text('{"token":"not-backed-up"}')
    return root


@pytest.fixture
def pg(monkeypatch):
    calls = []

    def invoke(executable, args, db, *, maintenance=False):
        calls.append((executable, args, db, maintenance))
        if executable == "pg_dump":
            Path(args[args.index("--file") + 1]).write_bytes(b"PGDMP-fake-pure-test")
        if executable == "psql" and args[-1] == backup.METADATA_SQL:
            return json.dumps(METADATA)
        return ""

    monkeypatch.setattr(backup, "invoke", invoke)
    return calls


def create(workspace, *, dry_run=False, **kwargs):
    return backup.create_backup(
        SOURCE,
        workspace,
        workspace / ".local/backups/one",
        workspace / ".local/renders",
        workspace / ".local/media",
        TOOLS,
        confirmed=kwargs.pop("confirmed", SOURCE.database),
        quiesced=kwargs.pop("quiesced", True),
        dry_run=dry_run,
        **kwargs,
    )


def restore(workspace, db=TARGET, *, dry_run=False, **kwargs):
    return backup.restore_backup(
        db,
        workspace,
        workspace / ".local/backups/one",
        kwargs.pop("destination", workspace / ".local/restored/one"),
        TOOLS,
        confirmed=kwargs.pop("confirmed", db.database),
        dry_run=dry_run,
    )


def replace_manifest(directory, mutation):
    path = directory / "manifest.json"
    data = json.loads(path.read_text())
    mutation(data)
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    (directory / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://mediaos_admin:secret@production.example:5432/mediaos",
        "postgresql://mediaos_admin:secret@127.0.0.1/mediaos",
        "postgresql://mediaos_admin:secret@127.0.0.1:55432/growie",
        "postgresql://mediaos_runtime:secret@127.0.0.1:55432/mediaos",
        "postgresql://mediaos_admin:secret@127.0.0.1:55432/mediaos?host=production.example",
        "postgresql://mediaos_admin:secret@127.0.0.1:55432/mediaos#fragment",
        "postgresql://mediaos_admin:secret@127.0.0.1:55432/mediaos/other",
        "postgresql://mediaos_admin:%00@127.0.0.1:55432/mediaos",
        "not-a-url-private-credential",
    ],
)
def test_reject_ambiguous_or_nonstandalone_database(monkeypatch, value):
    monkeypatch.setenv("MEDIAOS_BACKUP_DATABASE_URL", value)
    with pytest.raises(backup.BackupError) as error:
        backup.database_from_env("MEDIAOS_BACKUP_DATABASE_URL")
    assert value not in str(error.value)


def test_credentials_are_never_repr_argv_or_output(monkeypatch):
    monkeypatch.setenv(
        "MEDIAOS_BACKUP_DATABASE_URL",
        f"postgresql+psycopg://mediaos_admin:{SECRET}@localhost:55432/mediaos_dev",
    )
    db = backup.database_from_env("MEDIAOS_BACKUP_DATABASE_URL")
    assert SECRET not in repr(db) and SECRET not in str(db.public())
    monkeypatch.setenv("PGSERVICE", "dangerous-remote-service")
    monkeypatch.setenv("PGHOSTADDR", "10.1.2.3")
    monkeypatch.setenv("PGOPTIONS", "-c role=postgres")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-provider-secret")
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=1, stdout="", stderr=SECRET)

    monkeypatch.setattr(backup.subprocess, "run", run)
    with pytest.raises(backup.BackupError) as error:
        backup.invoke("psql", ["--no-password"], db)
    assert SECRET not in str(error.value) and SECRET not in str(seen[0][0])
    assert seen[0][1]["env"]["PGPASSWORD"] == SECRET
    assert "MEDIAOS_BACKUP_DATABASE_URL" not in seen[0][1]["env"]
    assert "OPENAI_API_KEY" not in seen[0][1]["env"]
    assert all(k not in seen[0][1]["env"] for k in ("PGSERVICE", "PGHOSTADDR", "PGOPTIONS"))
    assert seen[0][1]["capture_output"] and not seen[0][1].get("shell")


def test_process_timeout_never_exposes_command_or_credentials(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.TimeoutExpired([SECRET], 10, stderr=SECRET)

    monkeypatch.setattr(backup.subprocess, "run", failed)
    with pytest.raises(backup.BackupError) as error:
        backup.invoke("pg_dump", [], SOURCE)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("confirmed,quiesced", [("other", True), ("mediaos_dev", False)])
def test_backup_requires_explicit_standalone_and_quiescence(workspace, pg, confirmed, quiesced):
    with pytest.raises(backup.BackupError):
        create(workspace, confirmed=confirmed, quiesced=quiesced)
    assert pg == []


def test_backup_dry_run_does_not_connect_or_create(workspace, pg):
    result = create(workspace, dry_run=True)
    assert result["dry_run"] and result["storage_files"] == 3
    assert pg == [] and not (workspace / ".local/backups").exists()


def test_backup_contains_only_explicit_storage_and_preserves_database_guards(workspace, pg):
    create(workspace)
    manifest = backup.verify_backup(workspace, workspace / ".local/backups/one")
    assert {v["path"] for v in manifest["files"]} == {
        "database.dump",
        "renders/slide.png",
        "media/video.mp4",
        "social/image-1.jpg",
    }
    assert manifest["database_metadata"] == METADATA
    assert manifest["schema_version"] == 2
    assert manifest["storage_roots"] == ["renders", "media", "social"]
    assert SECRET not in json.dumps(manifest)
    dump = next(args for tool, args, _, _ in pg if tool == "pg_dump")
    assert "--format=custom" in dump
    assert all(flag not in dump for flag in ("--no-owner", "--no-acl", "--enable-row-security"))


def test_social_storage_path_override_is_checked_and_copied(workspace, pg):
    custom = workspace / ".local/custom-social"
    custom.mkdir()
    (custom / "receipt.json").write_bytes(b'{"receipt":"private"}')
    create(workspace, social=custom)
    manifest = backup.verify_backup(workspace, workspace / ".local/backups/one")
    names = {entry["path"] for entry in manifest["files"]}
    assert "social/receipt.json" in names and "social/image-1.jpg" not in names


def test_version_two_cannot_omit_social_root(workspace, pg):
    create(workspace)
    directory = workspace / ".local/backups/one"
    replace_manifest(directory, lambda m: m.update(storage_roots=["renders", "media"]))
    with pytest.raises(backup.BackupError):
        backup.verify_backup(workspace, directory)


def test_legacy_backup_remains_restorable_without_claiming_social_coverage(workspace, pg):
    create(workspace)
    directory = workspace / ".local/backups/one"
    (directory / "social/image-1.jpg").unlink()
    (directory / "social").rmdir()

    def legacy(manifest):
        manifest["schema_version"] = 1
        manifest.pop("storage_roots")
        manifest["files"] = [
            entry for entry in manifest["files"] if not entry["path"].startswith("social/")
        ]

    replace_manifest(directory, legacy)
    assert backup.verify_backup(workspace, directory)["schema_version"] == 1
    assert restore(workspace)["verified"]
    assert not (workspace / ".local/restored/one/social").exists()


def test_existing_backup_never_overwritten(workspace, pg):
    create(workspace)
    saved = (workspace / ".local/backups/one/database.dump").read_bytes()
    pg.clear()
    with pytest.raises(backup.BackupError, match="already exists"):
        create(workspace)
    assert pg == [] and (workspace / ".local/backups/one/database.dump").read_bytes() == saved


def test_change_during_backup_leaves_unverifiable_partial_output(workspace, pg, monkeypatch):
    original = backup.copy_new

    def mutate(source, destination):
        original(source, destination)
        if source.name == "slide.png":
            source.write_bytes(b"changed during capture")

    monkeypatch.setattr(backup, "copy_new", mutate)
    with pytest.raises(backup.BackupError, match="Source changed"):
        create(workspace)
    assert not (workspace / ".local/backups/one/manifest.json").exists()


@pytest.mark.parametrize(
    "target",
    [
        "renders/slide.png",
        "media/video.mp4",
        "social/image-1.jpg",
        "database.dump",
        "manifest.json",
    ],
)
def test_corrupt_backup_rejected_before_restore_connection(workspace, pg, target):
    create(workspace)
    pg.clear()
    path = workspace / ".local/backups/one" / target
    path.write_bytes(path.read_bytes() + b"corrupted")
    with pytest.raises(backup.BackupError):
        restore(workspace)
    assert pg == []


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/absolute",
        "media/../../escape",
        "media\\escape",
        "media/x:stream",
        "media/con.txt",
        "media/name.",
    ],
)
def test_manifest_traversal_rejected_even_with_new_manifest_checksum(workspace, pg, path):
    create(workspace)
    directory = workspace / ".local/backups/one"
    replace_manifest(directory, lambda m: m["files"][1].update(path=path))
    pg.clear()
    with pytest.raises(backup.BackupError):
        restore(workspace)
    assert pg == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(schema_version=True),
        lambda m: m.update(created_at="2026-01-01T00:00:00"),
        lambda m: m["files"].append(m["files"][0].copy()),
        lambda m: m["files"][0].update(size=True),
        lambda m: m.update(extra="unrecognized"),
        lambda m: m["database_metadata"].update(forced_rls=False),
        lambda m: m["source"].update(host="production.example"),
    ],
)
def test_invalid_manifest_schema_fail_closed(workspace, pg, mutation):
    create(workspace)
    directory = workspace / ".local/backups/one"
    replace_manifest(directory, mutation)
    with pytest.raises(backup.BackupError):
        backup.verify_backup(workspace, directory)


def test_extra_file_is_not_silently_restored(workspace, pg):
    create(workspace)
    (workspace / ".local/backups/one/unlisted-secret").write_bytes(b"extra")
    with pytest.raises(backup.BackupError, match="unlisted"):
        restore(workspace)


def test_missing_empty_storage_root_is_invalid(workspace, pg):
    (workspace / ".local/media/video.mp4").unlink()
    create(workspace)
    (workspace / ".local/backups/one/media").rmdir()
    with pytest.raises(backup.BackupError):
        restore(workspace)


def test_reject_path_outside_workspace_and_overlapping_storage(workspace, pg):
    with pytest.raises(backup.BackupError, match="inside"):
        backup.workspace_path(workspace, workspace.parent / "outside")
    with pytest.raises(backup.BackupError, match="absolute"):
        backup.workspace_path(workspace, Path("relative"))
    with pytest.raises(backup.BackupError, match="overlap"):
        backup.disjoint(workspace / ".local", workspace / ".local/media")
    assert pg == []


def test_symlink_storage_rejected(workspace, pg):
    link = workspace / ".local/renders/link"
    try:
        link.symlink_to(workspace / ".env")
    except OSError:
        pytest.skip("Host does not permit test symlinks")
    with pytest.raises(backup.BackupError, match="Symlinks"):
        create(workspace)
    assert pg == []


def test_directory_junction_rejected_without_following_target(workspace, monkeypatch):
    target = workspace / ".local/renders"
    monkeypatch.setattr(Path, "is_junction", lambda p: p == target, raising=False)
    with pytest.raises(backup.BackupError, match="junctions"):
        backup.workspace_path(workspace, target, existing=True)


def test_oversized_manifest_checksum_rejected(workspace, pg):
    create(workspace)
    (workspace / ".local/backups/one/manifest.sha256").write_text("0" * 1000)
    pg.clear()
    with pytest.raises(backup.BackupError, match="checksum file"):
        restore(workspace)
    assert pg == []


@pytest.mark.parametrize(
    "name",
    ["mediaos_dev", "mediaos", "mediaos_test", "other", "mediaos_restore_x;DROP DATABASE mediaos"],
)
def test_only_explicit_disposable_restore_database_allowed(workspace, pg, name):
    create(workspace)
    pg.clear()
    db = backup.Database("127.0.0.1", 55432, name, "mediaos_admin", SECRET)
    with pytest.raises(backup.BackupError, match="different"):
        restore(workspace, db)
    assert pg == []


def test_restore_same_source_name_rejected_even_on_another_port(workspace, pg):
    create(workspace)
    replace_manifest(
        workspace / ".local/backups/one",
        lambda m: m["source"].update(database=TARGET.database, port=55433),
    )
    pg.clear()
    with pytest.raises(backup.BackupError, match="different"):
        restore(workspace)
    assert pg == []


def test_restore_confirmation_must_match_exactly(workspace, pg):
    create(workspace)
    pg.clear()
    with pytest.raises(backup.BackupError):
        restore(workspace, confirmed="yes")
    assert pg == []


def test_restore_dry_run_validates_every_file_without_mutation(workspace, pg):
    create(workspace)
    pg.clear()
    result = restore(workspace, dry_run=True)
    assert result["dry_run"] and result["destination"]["database"] == TARGET.database
    assert pg == [] and not (workspace / ".local/restored").exists()


def test_restore_refuses_existing_storage_and_database(workspace, pg, monkeypatch):
    create(workspace)
    destination = workspace / ".local/restored/one"
    destination.mkdir(parents=True)
    pg.clear()
    with pytest.raises(backup.BackupError, match="storage destination already"):
        restore(workspace)
    assert pg == []
    monkeypatch.setattr(backup, "invoke", lambda *a, **k: "1")
    with pytest.raises(backup.BackupError, match="database already"):
        restore(workspace, destination=workspace / ".local/restored/two")
    assert not (workspace / ".local/restored/two").exists()


def test_restore_preserves_ownership_acl_and_transaction_then_validates(workspace, pg):
    create(workspace)
    pg.clear()
    result = restore(workspace)
    assert result["verified"]
    restore_args = next(
        args for tool, args, _, _ in pg if tool == "pg_restore" and "--list" not in args
    )
    assert "--single-transaction" in restore_args and "--exit-on-error" in restore_args
    assert all(
        flag not in restore_args for flag in ("--clean", "--create", "--no-owner", "--no-acl")
    )
    assert not any("DROP" in str(args) for _, args, _, _ in pg)
    destination = workspace / ".local/restored/one"
    assert (destination / "renders/slide.png").read_bytes() == b"private-render-bytes"
    assert (destination / "social/image-1.jpg").read_bytes() == b"private-social-bytes"
    report = json.loads((destination / "restore-report.json").read_text())
    assert report["database_metadata"] == METADATA and SECRET not in json.dumps(report)


def test_failed_restore_retains_isolated_storage_without_retry_or_delete(
    workspace, pg, monkeypatch
):
    create(workspace)
    original = backup.invoke

    def fail(executable, args, db, **kwargs):
        if executable == "pg_restore" and "--list" not in args:
            raise backup.BackupError("PostgreSQL tool failed")
        return original(executable, args, db, **kwargs)

    monkeypatch.setattr(backup, "invoke", fail)
    with pytest.raises(backup.BackupError):
        restore(workspace)
    assert (workspace / ".local/restored/one/media/video.mp4").exists()
    assert not (workspace / ".local/restored/one/restore-report.json").exists()
    assert not any("DROP" in str(args) for _, args, _, _ in pg)


def test_readiness_reports_missing_dependencies_without_database_or_secrets(workspace, monkeypatch):
    monkeypatch.setenv(
        "MEDIAOS_BACKUP_DATABASE_URL",
        f"postgresql://mediaos_admin:{SECRET}@127.0.0.1:55432/mediaos_dev",
    )
    monkeypatch.setattr(backup.shutil, "which", lambda _: None)
    report = backup.readiness(
        workspace, workspace / ".local/renders", workspace / ".local/media", None
    )
    assert not report["ready"] and not report["database_contacted"]
    assert SECRET not in json.dumps(report)
    assert (
        next(v for v in report["checks"] if v["check"] == "postgresql_executables")["ready"]
        is False
    )


def test_cli_error_output_does_not_reveal_invalid_url_or_password(workspace, monkeypatch, capsys):
    monkeypatch.setenv(
        "MEDIAOS_BACKUP_DATABASE_URL", f"postgresql://{SECRET}@production.example/growie"
    )
    result = backup.main(
        ["create", "--workspace", str(workspace), "--backup", str(workspace / "new"), "--dry-run"]
    )
    captured = capsys.readouterr()
    assert result == 1 and SECRET not in captured.err and "production.example" not in captured.err
