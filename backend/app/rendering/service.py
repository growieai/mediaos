"""Authenticated rendering with immutable bytes and exact-revision visual approval.

Local storage is intentionally private. An object-store adapter can implement the same
read/write boundary later; no URL supplied by a caller is ever fetched here.
"""

import hashlib
import io
import json
import logging
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image
from pydantic import Field
from sqlalchemy import text

from app.config import REPO_ROOT, get_settings
from app.db.repository import Repository, canonical_hash, engine, transaction
from app.models.schemas import CarouselDraft, StrictModel
from app.rendering.renderer import render_carousel
from app.rendering.schemas import RenderManifest, VisualConfig
from app.services.workflows import ConflictError, typed

log = logging.getLogger("mediaos")


class RenderInput(StrictModel):
    asset_version_id: UUID
    visual_config_version_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)


class VisualDecision(StrictModel):
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: str | None = Field(default=None, max_length=2000)


def catalog_path(value: str) -> Path:
    relative = Path(value)
    path = (REPO_ROOT / relative).resolve()
    roots = (REPO_ROOT / "backend/assets", REPO_ROOT / "characters")
    if relative.is_absolute() or not any(path.is_relative_to(root.resolve()) for root in roots):
        raise ConflictError("Visual configuration references an invalid asset path")
    if not path.is_file():
        raise ConflictError("A configured visual asset is missing")
    return path


def visual_config(row):
    config = typed(VisualConfig, row["payload"])
    for style, field in (("regular", "regular_font_path"), ("bold", "bold_font_path")):
        path = catalog_path(getattr(config, field))
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["font_hashes"][style]:
            raise ConflictError("A configured font has changed; seed a new visual version")
        setattr(config, field, str(path))
    reference = catalog_path(row["reference_path"]) if row["reference_path"] else None
    if reference and hashlib.sha256(reference.read_bytes()).hexdigest() != row["reference_sha256"]:
        raise ConflictError("The character reference has changed; seed a new visual version")
    return config, reference


def render_directory(tenant: UUID, render_id: UUID) -> Path:
    return get_settings().asset_storage_path.resolve() / str(UUID(str(tenant))) / str(render_id)


def checked_bytes(path: Path, digest: str, maximum: int = 20_000_000) -> bytes:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > maximum:
        raise ConflictError("Rendered file is missing or invalid")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ConflictError("Rendered file checksum no longer matches the reviewed manifest")
    return content


def validate_files(directory: Path, manifest: RenderManifest) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    if manifest.status != "PASS":
        return files
    for index, slide in enumerate(manifest.slides, 1):
        filename = f"slide-{index:02d}.png"
        if slide.index != index or slide.filename != filename or not slide.sha256:
            raise ConflictError("Render manifest has invalid slide identifiers")
        data = checked_bytes(directory / filename, slide.sha256)
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (1080, 1350):
                raise ConflictError("Rendered image dimensions or format are invalid")
            image.verify()
        files[filename] = data
    return files


def render_details(repo: Repository, render_id: UUID):
    row = repo.one("render_runs", id=render_id)
    row["approvals"] = repo.all("visual_approval_records", render_run_id=render_id)
    return row


def workflow_renders(repo: Repository, run_id: UUID):
    run = repo.one("workflow_runs", id=run_id)
    return {
        "configurations": repo.all("visual_config_versions", influencer_id=run["influencer_id"]),
        "renders": [
            render_details(repo, row["id"])
            for row in repo.all("render_runs", workflow_run_id=run_id)
        ],
    }


def create_render(tenant: UUID, token: str, run_id: UUID, request: RenderInput):
    if not 1 <= len(request.idempotency_key) <= 128:
        raise ConflictError("Render idempotency key must have 1–128 characters")
    digest = canonical_hash(
        {
            "workflow_run_id": run_id,
            "asset_version_id": request.asset_version_id,
            "visual_config_version_id": request.visual_config_version_id,
            "renderer_version": "pillow-editorial-v1",
        }
    )
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        repo.one("workflow_runs", id=run_id)
        render_id = repo.connection.execute(
            text("SELECT start_render(:rid,:aid,:cid,:key,:hash)"),
            {
                "rid": run_id,
                "aid": request.asset_version_id,
                "cid": request.visual_config_version_id,
                "key": request.idempotency_key,
                "hash": digest,
            },
        ).scalar_one()
    return execute_render(tenant, token, render_id)


def execute_render(tenant: UUID, token: str, render_id: UUID):
    with engine().connect() as conn:
        with conn.begin():
            repo = Repository(conn, tenant, token)
            repo.require("OPERATOR")
            row = repo.one("render_runs", id=render_id)
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
                {"key": f"render:{tenant}:{render_id}"},
            ).scalar_one()
        if not locked:
            raise ConflictError("Render is already executing")
        try:
            if row["status"] not in {"CREATED", "RENDERING"}:
                with conn.begin():
                    return render_details(Repository(conn, tenant, token), render_id)
            return _execute(conn, tenant, token, row)
        except Exception as exc:
            # Initialization can fail before a skill starts (for example stale evidence).
            # Persist a terminal render outcome instead of leaving a misleading active row.
            if conn.in_transaction():
                conn.rollback()
            with conn.begin():
                repo = Repository(conn, tenant, token)
                current = repo.one("render_runs", id=render_id)
                if current["status"] in {"CREATED", "RENDERING"}:
                    repo.connection.execute(
                        text("SELECT fail_render(:id,:category)"),
                        {"id": render_id, "category": type(exc).__name__.upper()[:80]},
                    )
            raise
        finally:
            if conn.in_transaction():
                conn.rollback()
            with conn.begin():
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                    {"key": f"render:{tenant}:{render_id}"},
                )


def _execute(conn, tenant, token, row):
    render_id, run_id = row["id"], row["workflow_run_id"]
    started = time.monotonic()
    with conn.begin():
        repo = Repository(conn, tenant, token)
        repo.connection.execute(text("SELECT claim_render(:id)"), {"id": render_id})
        previous = repo.all(
            "skill_runs", workflow_run_id=run_id, step_key=f"visual.render:{render_id}"
        )
        for old in previous:
            if old["status"] == "RUNNING":
                repo.update_skill(
                    old["id"],
                    status="INTERRUPTED",
                    ended_at=datetime.now(UTC),
                    latency_ms=max(
                        0, (datetime.now(UTC) - old["started_at"]).total_seconds() * 1000
                    ),
                    error_category="PROCESS_INTERRUPTED",
                    retryable=True,
                )
        if len(previous) >= get_settings().max_skill_attempts:
            repo.connection.execute(
                text("SELECT fail_render(:id,:category)"),
                {"id": render_id, "category": "ATTEMPTS_EXHAUSTED"},
            )
            return render_details(repo, render_id)
        attempt = repo.insert(
            "skill_runs",
            workflow_run_id=run_id,
            step_key=f"visual.render:{render_id}",
            skill_identifier="visual.render",
            skill_version="1.0.0",
            input_schema_version=1,
            output_schema_version=1,
            provider="deterministic",
            model="none",
            adapter="pillow-editorial-v1",
            attempt=len(previous) + 1,
            input_hash=row["input_hash"],
            status="RUNNING",
            is_mock=False,
        )
        repo.insert(
            "cost_events",
            workflow_run_id=run_id,
            skill_run_id=attempt["id"],
            provider="deterministic",
            model="none",
            input_tokens=0,
            output_tokens=0,
            cost=0,
            currency="USD",
            price_version="deterministic-zero-v1",
        )
        draft = typed(
            CarouselDraft, repo.one("content_asset_versions", id=row["asset_version_id"])["payload"]
        )
        profile = repo.one("visual_config_versions", id=row["visual_config_version_id"])
    try:
        config, reference = visual_config(profile)
        root = render_directory(tenant, render_id)
        final = root / "files"
        if final.exists():
            # A crash after the atomic filesystem rename can resume the DB checkpoint.
            manifest = RenderManifest.model_validate_json(
                (final / "manifest.json").read_bytes(), strict=True
            )
            if manifest.draft_sha256 != canonical_hash(draft.model_dump(mode="json")):
                raise ConflictError("Interrupted render does not match the current input")
        else:
            temporary = root / f"attempt-{uuid4()}"
            manifest = render_carousel(draft, config, temporary, reference_image_path=reference)
            validate_files(temporary, manifest)
            temporary.rename(final)
        validate_files(final, manifest)
        payload = manifest.model_dump(mode="json")
        with conn.begin():
            repo = Repository(conn, tenant, token)
            repo.connection.execute(
                text("SELECT complete_render(:id,CAST(:manifest AS jsonb),:hash)"),
                {
                    "id": render_id,
                    "manifest": json.dumps(payload, ensure_ascii=False),
                    "hash": canonical_hash(payload),
                },
            )
            repo.update_skill(
                attempt["id"],
                status="SUCCEEDED",
                ended_at=datetime.now(UTC),
                latency_ms=(time.monotonic() - started) * 1000,
                output=payload,
                asset_version_id=row["asset_version_id"],
            )
            result = render_details(repo, render_id)
    except Exception as exc:
        with conn.begin():
            repo = Repository(conn, tenant, token)
            repo.update_skill(
                attempt["id"],
                status="FAILED",
                ended_at=datetime.now(UTC),
                latency_ms=(time.monotonic() - started) * 1000,
                error_category=type(exc).__name__,
                retryable=False,
            )
            repo.connection.execute(
                text("SELECT fail_render(:id,:category)"),
                {"id": render_id, "category": type(exc).__name__.upper()[:80]},
            )
        raise ConflictError(
            "Render failed; inspect its persisted attempt and visual findings"
        ) from None
    log.info(
        "render_finished",
        extra={
            "tenant_id": str(tenant),
            "workflow_run_id": str(run_id),
            "skill_run_id": str(attempt["id"]),
            "attempt": attempt["attempt"],
            "state": result["status"],
        },
    )
    return result


def preview_slide(repo: Repository, render_id: UUID, index: int) -> bytes:
    row = repo.one("render_runs", id=render_id)
    if row["status"] != "PASS":
        raise ConflictError("Only a completed visual QA PASS has preview images")
    manifest = typed(RenderManifest, row["manifest"])
    if not 1 <= index <= len(manifest.slides):
        raise LookupError("Slide not found")
    directory = render_directory(repo.tenant_id, render_id) / "files"
    files = validate_files(directory, manifest)
    return files[f"slide-{index:02d}.png"]


def export_render(repo: Repository, render_id: UUID) -> bytes:
    # Guard holds workflow/source locks until all checked bytes are read into the response.
    repo.connection.execute(text("SELECT check_render_export(:id)"), {"id": render_id})
    row = repo.one("render_runs", id=render_id)
    manifest = typed(RenderManifest, row["manifest"])
    payload = manifest.model_dump(mode="json")
    if canonical_hash(payload) != row["manifest_hash"]:
        raise ConflictError("Render manifest checksum mismatch")
    files = validate_files(render_directory(repo.tenant_id, render_id) / "files", manifest)
    files["manifest.json"] = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    files["caption.txt"] = manifest.caption.text.encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, data in files.items():
            info = zipfile.ZipInfo(filename, date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, data)
    return buffer.getvalue()


def decide_visual(tenant: UUID, token: str, render_id: UUID, decision: str, data: VisualDecision):
    with transaction(tenant, token) as repo:
        approval = repo.connection.execute(
            text("SELECT decide_render(:id,:hash,:decision,:comment)"),
            {
                "id": render_id,
                "hash": data.manifest_hash,
                "decision": decision,
                "comment": data.comment,
            },
        ).scalar_one()
        row = render_details(repo, render_id)
        if decision == "APPROVE":
            # SQL holds exact-revision/source locks; missing/tampered bytes roll back
            # its approval insert in the same transaction before anything is committed.
            manifest = typed(RenderManifest, row["manifest"])
            validate_files(render_directory(tenant, render_id) / "files", manifest)
        return {"visual_approval_record_id": approval, "render": row}
