"""Checkpoint before external calls; uncertain submissions are never automatically replayed."""

import hashlib
import io
import json
import logging
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.repository import Repository, canonical_hash, engine, transaction
from app.media import files
from app.media.schemas import MediaDecision, MediaInput, ProfileInput, SpendPolicy
from app.media_providers.elevenlabs import ElevenLabs
from app.media_providers.heygen import HeyGen
from app.media_providers.http import ProviderError
from app.rendering.service import visual_config
from app.services.workflows import ConflictError

log = logging.getLogger("mediaos")
TERMINAL = {"AWAITING_APPROVAL", "APPROVED", "REJECTED", "BLOCKED", "FAILED", "UNKNOWN_OUTCOME"}
STAGES = {
    "CREATED": "SPEECH",
    "SPEECH_READY": "IMAGE_UPLOAD",
    "IMAGE_READY": "AUDIO_UPLOAD",
    "ASSETS_READY": "AVATAR_SUBMIT",
    "AVATAR_PENDING": "AVATAR_POLL",
    "AVATAR_READY": "COMPOSE",
}


def dependencies():
    settings = get_settings()

    def present(value):
        return bool(value and value.get_secret_value())

    return {
        "live_enabled": settings.media_live_enabled,
        "elevenlabs_configured": present(settings.elevenlabs_api_key),
        "heygen_configured": present(settings.heygen_api_key),
        "higgsfield_configured": present(settings.hf_api_key_id)
        and present(settings.hf_api_key_secret),
        "ffmpeg_available": bool(shutil.which("ffmpeg")),
        "ffprobe_available": bool(shutil.which("ffprobe")),
        "text_mode": "mock" if settings.ai_mock_mode else "model-opt-in",
        "automatic_publishing": False,
        "required": [
            "approved source content",
            "versioned voice and price profile",
            "enabled unexpired spend policy",
            "human media review",
        ],
    }


def details(repo: Repository, run_id: UUID):
    row = repo.one("media_runs", id=run_id)
    jobs = repo.all("media_jobs", media_run_id=run_id)
    # Signed provider URLs and filesystem locations never reach the console/logs.
    for job in jobs:
        if job["result"]:
            job["result"] = {k: v for k, v in job["result"].items() if k != "output_url"}
    row["jobs"] = jobs
    row["approvals"] = repo.all("media_approval_records", media_run_id=run_id)
    return row


def workflow_media(repo: Repository, workflow_id: UUID):
    workflow = repo.one("workflow_runs", id=workflow_id)
    return {
        "profiles": repo.all("media_profiles", influencer_id=workflow["influencer_id"]),
        "spend_policies": repo.all("media_spend_policies"),
        "dependencies": dependencies(),
        "runs": [
            details(repo, row["id"]) for row in repo.all("media_runs", workflow_run_id=workflow_id)
        ],
    }


def create_profile(tenant: UUID, token: str, influencer: UUID, data: ProfileInput):
    with transaction(tenant, token) as repo:
        repo.require("ADMIN")
        visual = repo.one("visual_config_versions", id=data.visual_config_version_id)
        _, reference = visual_config(visual)
        _portrait(reference, visual["reference_sha256"])
        result = repo.connection.execute(
            text("SELECT create_media_profile(:i,:v,CAST(:p AS jsonb))"),
            {
                "i": influencer,
                "v": data.visual_config_version_id,
                "p": data.payload.model_dump_json(),
            },
        ).scalar_one()
        return repo.one("media_profiles", id=result)


def spend_policy(tenant: UUID, token: str, data: SpendPolicy):
    with transaction(tenant, token) as repo:
        result = repo.connection.execute(
            text("SELECT set_media_spend_policy(CAST(:p AS jsonb))"), {"p": data.model_dump_json()}
        ).scalar_one()
        return repo.one("media_spend_policies", id=result)


def create(tenant: UUID, token: str, workflow_id: UUID, data: MediaInput):
    digest = canonical_hash(
        {
            "workflow_run_id": workflow_id,
            "profile_id": data.profile_id,
            "selected_paths": data.selected_paths,
        }
    )
    with transaction(tenant, token) as repo:
        run_id = repo.connection.execute(
            text("SELECT start_media_run(:w,:p,CAST(:paths AS jsonb),:k,:h)"),
            {
                "w": workflow_id,
                "p": data.profile_id,
                "paths": json.dumps(data.selected_paths),
                "k": data.idempotency_key,
                "h": digest,
            },
        ).scalar_one()
        return details(repo, run_id)


def _receipt(directory: Path, job: dict, result: dict | None = None):
    path = directory / f"receipt-{UUID(str(job['id']))}.json"
    if result is not None:
        envelope = {"job_id": str(job["id"]), "input_hash": job["input_hash"], "result": result}
        raw = json.dumps(
            envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        if len(raw) > 200_000 or path.is_symlink():
            raise ConflictError("Invalid media receipt")
        # Publish only a complete, fsynced receipt. A crash may orphan a temporary
        # file, but cannot expose a partial committed receipt or overwrite history.
        temporary = directory / f"receipt-{uuid4()}.partial"
        try:
            with temporary.open("xb") as handle:
                if os.name != "nt":
                    temporary.chmod(0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return result
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 200_000:
        raise ConflictError("Invalid media receipt")
    try:
        envelope = json.loads(path.read_bytes())
        if (
            set(envelope) != {"job_id", "input_hash", "result"}
            or envelope["job_id"] != str(job["id"])
            or envelope["input_hash"] != job["input_hash"]
            or not isinstance(envelope["result"], dict)
        ):
            raise ValueError
        return envelope["result"]
    except (ValueError, TypeError):
        raise ConflictError("Invalid media receipt") from None


def _finish(conn, tenant, token, job, result):
    with conn.begin():
        Repository(conn, tenant, token)
        conn.execute(
            text("SELECT finish_media_job(:j,CAST(:r AS jsonb))"),
            {"j": job["id"], "r": json.dumps(result)},
        )


def _safe_failure_request_id(value: str | None) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", value):
        return None
    settings = get_settings()
    for name in ("elevenlabs_api_key", "heygen_api_key", "hf_api_key_id", "hf_api_key_secret"):
        secret = getattr(settings, name, None)
        credential_value = secret.get_secret_value() if secret else None
        if credential_value and (credential_value in value or value in credential_value):
            return None
    return value


def _fail(
    conn,
    tenant,
    token,
    job,
    category,
    retryable=False,
    unknown=False,
    retry_after=None,
    provider_request_id=None,
):
    with conn.begin():
        Repository(conn, tenant, token)
        conn.execute(
            text("SELECT fail_media_job(:j,:c,:r,:u,:delay,:provider_request_id)"),
            {
                "j": job["id"],
                "c": category,
                "r": retryable,
                "u": unknown,
                "delay": retry_after,
                "provider_request_id": _safe_failure_request_id(provider_request_id),
            },
        )
    log.warning(
        "media_job_failed",
        extra={
            "tenant_id": str(tenant),
            "workflow_run_id": str(job["workflow_run_id"]),
            "skill_run_id": str(job["skill_run_id"]),
            "attempt": job["attempt"],
            "error_category": category,
        },
    )


def _portrait(reference: Path | None, expected: str) -> tuple[bytes, str]:
    try:
        if (
            reference is None
            or reference.is_symlink()
            or not 0 < reference.stat().st_size <= 32_000_000
        ):
            raise ValueError
        raw = reference.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError
        with Image.open(io.BytesIO(raw)) as portrait:
            if portrait.format not in {"PNG", "JPEG"} or not all(
                0 < v <= 8192 for v in portrait.size
            ):
                raise ValueError
            mime = "image/png" if portrait.format == "PNG" else "image/jpeg"
            portrait.verify()
        return raw, mime
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ConflictError(
            "Speaking media requires an intact pinned PNG or JPEG portrait"
        ) from None


def _invoke(directory, row, job, profile, cfg, reference, successes):
    settings = get_settings()
    stage = job["stage"]
    assert settings.elevenlabs_api_key is not None and settings.heygen_api_key is not None
    if stage == "SPEECH":
        result = ElevenLabs(settings.elevenlabs_api_key).speech(
            row["script"]["text"], profile["voice_id"], profile["tts_model"]
        )
        return files.store_speech(directory, result, row["script"]["text"], profile["tts_model"])
    avatar = HeyGen(settings.heygen_api_key)
    if stage in {"IMAGE_UPLOAD", "AUDIO_UPLOAD"}:
        if stage == "IMAGE_UPLOAD":
            expected = job["input"]["visual_reference_sha256"]
            raw, mime = _portrait(reference, expected)
        else:
            expected = successes["SPEECH"]["audio_sha256"]
            raw = files.load_blob(directory, "speech.mp3", expected, 12_000_000)
            mime = "audio/mpeg"
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ConflictError("Media input bytes changed")
        uploaded = avatar.upload(raw, mime, str(job["request_id"]))
        return {
            "schema_version": 1,
            "provider": "heygen",
            **uploaded.model_dump(mode="json"),
            "sha256": expected,
        }
    if stage == "AVATAR_SUBMIT":
        submitted = avatar.create(
            successes["IMAGE_UPLOAD"]["asset_id"], successes["AUDIO_UPLOAD"]["asset_id"]
        )
        return {"schema_version": 1, "provider": "heygen", **submitted.model_dump(mode="json")}
    if stage == "AVATAR_POLL":
        polled = avatar.poll(successes["AVATAR_SUBMIT"]["request_id"])
        return {"schema_version": 1, "provider": "heygen", **polled.model_dump(mode="json")}
    return files.compose(
        directory,
        successes["AVATAR_POLL"]["output_url"],
        row["script"],
        successes["SPEECH"],
        job["input"]["visual_reference_sha256"],
        Path(cfg.regular_font_path),
        Path(cfg.bold_font_path),
    )


def execute(tenant: UUID, token: str, run_id: UUID):
    with engine().connect() as conn:
        with conn.begin():
            repo = Repository(conn, tenant, token)
            repo.require("OPERATOR")
            row = repo.one("media_runs", id=run_id)
            if row["status"] in TERMINAL:
                return details(repo, run_id)
            available = dependencies()
            if not all(
                available[k]
                for k in (
                    "live_enabled",
                    "elevenlabs_configured",
                    "heygen_configured",
                    "ffmpeg_available",
                    "ffprobe_available",
                )
            ):
                raise ConflictError(
                    "Enable media and configure ElevenLabs, HeyGen and FFmpeg before execution"
                )
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
                {"key": f"media:{tenant}:{run_id}"},
            ).scalar_one()
        if not locked:
            raise ConflictError("Media run is already executing")
        try:
            directory = files.media_directory(get_settings().media_storage_path, tenant, run_id)
            # One call advances local checkpoints, submits once, or performs one safe poll.
            for _ in range(7):
                with conn.begin():
                    repo = Repository(conn, tenant, token)
                    row = repo.one("media_runs", id=run_id)
                    if row["status"] in TERMINAL:
                        return details(repo, run_id)
                    if row["next_poll_at"] and row["next_poll_at"] > datetime.now(UTC):
                        return details(repo, run_id)
                    jobs = repo.all("media_jobs", media_run_id=run_id)
                    active = next((j for j in jobs if j["status"] == "RUNNING"), None)
                    successes = {
                        j["stage"]: j["result"]
                        for j in sorted(jobs, key=lambda j: j["attempt"])
                        if j["status"] == "SUCCEEDED"
                    }
                    profile = repo.one("media_profiles", id=row["profile_id"])["payload"]
                    visual = repo.one("visual_config_versions", id=row["visual_config_version_id"])
                    cfg, reference = visual_config(visual)
                    _portrait(reference, visual["reference_sha256"])
                if active:
                    # The advisory lock proves the prior executor ended. Recover a receipt,
                    # otherwise hold any submission which could have reached a provider.
                    try:
                        saved = _receipt(directory, active)
                    except (ConflictError, OSError):
                        saved = None
                    if saved is not None:
                        try:
                            _finish(conn, tenant, token, active, saved)
                        except DBAPIError as exc:
                            if getattr(exc.orig, "sqlstate", None) != "23514":
                                # Keep the saved receipt recoverable after an operational
                                # database failure; do not mislabel it as a policy block.
                                raise
                            _fail(conn, tenant, token, active, "POLICY_BLOCKED")
                    else:
                        safe = active["stage"] in {"AVATAR_POLL", "COMPOSE"}
                        _fail(
                            conn,
                            tenant,
                            token,
                            active,
                            "PROCESS_INTERRUPTED",
                            retryable=safe,
                            unknown=not safe,
                        )
                    with conn.begin():
                        return details(Repository(conn, tenant, token), run_id)
                with conn.begin():
                    repo = Repository(conn, tenant, token)
                    jid = conn.execute(
                        text("SELECT reserve_media_job(:r,:s,CAST(:p AS jsonb))"),
                        {
                            "r": run_id,
                            "s": STAGES[row["status"]],
                            "p": json.dumps(
                                {"schema_version": 1, "script_hash": row["script_hash"]}
                            ),
                        },
                    ).scalar_one()
                    job = repo.one("media_jobs", id=jid)
                try:
                    result = _invoke(directory, row, job, profile, cfg, reference, successes)
                    _receipt(directory, job, result)
                    try:
                        _finish(conn, tenant, token, job, result)
                    except DBAPIError as exc:
                        if getattr(exc.orig, "sqlstate", None) != "23514":
                            raise
                        _fail(conn, tenant, token, job, "POLICY_BLOCKED")
                except ProviderError as exc:
                    _fail(
                        conn,
                        tenant,
                        token,
                        job,
                        exc.category,
                        exc.retryable,
                        exc.category == "UNKNOWN_OUTCOME",
                        exc.retry_after_seconds,
                        exc.request_id,
                    )
                except files.MediaRetrievalError as exc:
                    _fail(
                        conn,
                        tenant,
                        token,
                        job,
                        "MEDIA_RETRIEVAL_UNAVAILABLE",
                        retryable=True,
                        retry_after=exc.retry_after_seconds,
                    )
                except (ConflictError, ValueError):
                    _fail(conn, tenant, token, job, "INVALID_MEDIA_OUTPUT")
                except (OSError, RuntimeError):
                    # A provider may have completed before a local write/process failed.
                    paid = job["stage"] in {
                        "SPEECH",
                        "AVATAR_SUBMIT",
                        "IMAGE_UPLOAD",
                        "AUDIO_UPLOAD",
                    }
                    _fail(conn, tenant, token, job, "LOCAL_EXECUTION_FAILED", unknown=paid)
                with conn.begin():
                    repo = Repository(conn, tenant, token)
                    finished_job = repo.one("media_jobs", id=job["id"])
                    if finished_job["status"] != "SUCCEEDED" or job["stage"] == "AVATAR_POLL":
                        return details(repo, run_id)
            with conn.begin():
                return details(Repository(conn, tenant, token), run_id)
        finally:
            if conn.in_transaction():
                conn.rollback()
            with conn.begin():
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                    {"key": f"media:{tenant}:{run_id}"},
                )


def video(repo: Repository, run_id: UUID, export: bool = False):
    row = repo.one("media_runs", id=run_id)
    repo.connection.execute(
        text("SELECT check_media_access(:id,:approved)"), {"id": run_id, "approved": export}
    )
    return files.validate_final(
        files.media_directory(get_settings().media_storage_path, repo.tenant_id, run_id),
        row["manifest"],
    )


def review(tenant: UUID, token: str, run_id: UUID, decision: MediaDecision):
    with transaction(tenant, token) as repo:
        repo.require("APPROVER")
        if decision.decision == "APPROVE":
            video(repo, run_id)
        repo.connection.execute(
            text("SELECT approve_media_run(:r,:h,:d,CAST(:c AS jsonb),:comment)"),
            {
                "r": run_id,
                "h": decision.manifest_hash,
                "d": decision.decision,
                "c": decision.checks.model_dump_json(),
                "comment": decision.comment,
            },
        )
        return details(repo, run_id)
