"""Connected-account lifecycle and exact manual dispatch; no automatic publishing."""

import hashlib
import json
import logging
import secrets
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial, wraps
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import REPO_ROOT, get_settings
from app.db.repository import Repository, canonical_hash, engine, transaction
from app.media.service import _receipt
from app.rendering.schemas import RenderManifest
from app.rendering.service import render_directory
from app.services.workflows import ConflictError, typed
from app.social import files
from app.social.schemas import (
    ConnectInput,
    PlatformObservation,
    PublishDecision,
    PublishInput,
    PublishPlan,
    ReconcileInput,
)


def _value(secret):
    return secret.get_secret_value() if secret else ""


def service_tokens():
    settings = get_settings()
    if settings.social_service_tokens:
        data = json.loads(settings.social_service_tokens.get_secret_value())
    elif settings.app_env == "development":
        path = REPO_ROOT / ".local/social-credentials.json"
        data = json.loads(path.read_text()) if path.is_file() else {}
    else:
        data = {}
    if not isinstance(data, dict) or len(data) > 100:
        raise ConflictError("Invalid social service configuration")
    return data


def service_token(tenant):
    value = service_tokens().get(str(UUID(str(tenant))))
    if not isinstance(value, str) or len(value) < 32:
        raise ConflictError("Tenant connector identity is not configured")
    return value


def vault_key():
    value = _value(get_settings().social_vault_key)
    if len(value) < 32:
        raise ConflictError("Social encryption key is not configured")
    return value


def dependencies():
    s = get_settings()
    return {
        "connect_enabled": s.social_connect_enabled,
        "manual_publish_enabled": s.social_publish_enabled,
        "reply_dispatch_enabled": s.social_reply_enabled,
        "app_configured": bool(
            s.social_app_id
            and s.social_app_secret
            and s.social_api_version
            and s.social_redirect_uri
        ),
        "vault_configured": len(_value(s.social_vault_key)) >= 32,
        "public_media_configured": bool(s.social_public_base_url),
        "automatic_publishing": False,
    }


def provider(token=None):
    from app.social_provider import InstagramProvider, InstagramSettings

    s = get_settings()
    if not (
        s.social_app_id and s.social_app_secret and s.social_api_version and s.social_redirect_uri
    ):
        raise ConflictError("Instagram application is not configured")
    host = urlsplit(s.social_public_base_url or "").hostname
    return InstagramProvider(
        InstagramSettings(
            api_version=s.social_api_version,
            app_id=s.social_app_id,
            app_secret=s.social_app_secret,
            redirect_uri=s.social_redirect_uri,
            media_host_allowlist=(host,) if host else (),
        ),
        token=SecretStr(token) if token else None,
    )


def connected_provider(tenant, connection_id):
    token = service_token(tenant)
    with transaction(tenant, token) as repo:
        connection = repo.one("social_connections", id=connection_id)
        secret = repo.connection.execute(
            text("SELECT social_token(:id,:key)"),
            {"id": connection_id, "key": vault_key()},
        ).scalar_one()
    client = provider(secret)
    if client.settings.api_version != connection["api_version"]:
        raise ConflictError("Reconnect account after changing the pinned API version")
    return client, connection


def read_job_provider(tenant, job):
    """Use only the current compatible credential pinned by a reserved read."""
    with transaction(tenant, service_token(tenant)) as repo:
        cid = repo.connection.execute(
            text("SELECT social_read_job_connection(:id)"), {"id": job["id"]}
        ).scalar_one()
    return connected_provider(tenant, cid)


def begin_connect(tenant, token, request: ConnectInput):
    if not get_settings().social_connect_enabled:
        raise ConflictError("Instagram connection is disabled")
    state = files.sign(
        vault_key(),
        "oauth",
        {
            "tenant": str(tenant),
            "nonce": secrets.token_urlsafe(32),
            "expires": int(time.time()) + 600,
        },
    )
    with transaction(tenant, token) as repo:
        repo.require("ADMIN")
        repo.one("influencers", id=request.influencer_id)
        repo.connection.execute(
            text("SELECT social_begin_oauth(:id,:hash)"),
            {
                "id": request.influencer_id,
                "hash": hashlib.sha256(state.encode()).hexdigest(),
            },
        )
    return {
        "authorization_url": provider().authorization_url(
            state,
            (
                "instagram_business_basic",
                "instagram_business_content_publish",
                "instagram_business_manage_insights",
                "instagram_business_manage_comments",
            ),
        )
    }


def finish_connect(state: str, code: str):
    if not get_settings().social_connect_enabled:
        raise ConflictError("Instagram connection is disabled")
    if not code or len(code) > 4096:
        raise PermissionError("Invalid authorization code")
    data = files.verify(vault_key(), "oauth", state)
    tenant = UUID(data["tenant"])
    token = service_token(tenant)
    with transaction(tenant, token) as repo:
        sid = repo.connection.execute(
            text("SELECT social_consume_oauth(:hash)"),
            {
                "hash": hashlib.sha256(state.encode()).hexdigest(),
            },
        ).scalar_one()
    client = provider()
    short = client.exchange_code(SecretStr(code))
    grant = client.exchange_long_lived(short.access_token)
    if grant.expires_in is None or grant.expires_in <= 0:
        raise ConflictError("Provider did not supply token expiry")
    profile = provider(grant.access_token.get_secret_value()).profile().payload
    if short.user_id and profile.id != short.user_id:
        raise ConflictError("Provider account identity mismatch")
    scopes = short.permissions
    with transaction(tenant, token) as repo:
        cid = repo.connection.execute(
            text(
                "SELECT social_finish_oauth(:state,:account,:username,:version,CAST(:scopes AS jsonb),:token,:expiry,:key)"
            ),
            {
                "state": sid,
                "account": profile.id,
                "username": profile.username,
                "version": get_settings().social_api_version,
                "scopes": json.dumps(scopes),
                "token": grant.access_token.get_secret_value(),
                "expiry": datetime.now(UTC) + timedelta(seconds=grant.expires_in),
                "key": vault_key(),
            },
        ).scalar_one()
    return {"status": "CONNECTED", "connection_id": str(cid)}


def refresh_connection(tenant, token, cid):
    if not get_settings().social_connect_enabled:
        raise ConflictError("Instagram connection is disabled")
    with transaction(tenant, token) as repo:
        repo.require("ADMIN")
        repo.one("social_connections", id=cid)
    client, _ = connected_provider(tenant, cid)
    grant = client.refresh_token(client.token)
    if grant.expires_in is None:
        raise ConflictError("Provider did not supply refreshed expiry")
    with transaction(tenant, service_token(tenant)) as repo:
        repo.connection.execute(
            text("SELECT social_rotate_token(:id,:token,:expiry,:key)"),
            {
                "id": cid,
                "token": grant.access_token.get_secret_value(),
                "expiry": datetime.now(UTC) + timedelta(seconds=grant.expires_in),
                "key": vault_key(),
            },
        )
    return {"status": "REFRESHED"}


def details(repo, pid):
    result = repo.one("social_publish_runs", id=pid)
    result["jobs"] = repo.all("social_publish_jobs", publish_run_id=pid)
    result["decisions"] = repo.all("social_publish_decisions", publish_run_id=pid)
    result["insights"] = repo.all("social_insight_snapshots", publish_run_id=pid)
    result["reconciliation_requests"] = repo.all("social_reconcile_requests", publish_run_id=pid)
    return result


COMMENT_RELINK_BATCH = 25


def _saved_comment_ids(repo, published, limit):
    """Filter non-linkable evidence before LIMIT so it cannot starve later comments."""
    return (
        repo.connection.execute(
            text(
                r"""
            SELECT h.id FROM social_webhook_events h
            JOIN social_connections c ON c.tenant_id=h.tenant_id AND c.id=h.connection_id
            WHERE h.tenant_id=:tenant AND c.account_id=:account
            AND h.payload->>'account_id'=:account AND h.payload->>'media_id'=:post
            AND h.payload ?& ARRAY['schema_version','account_id','comment_id','media_id','text',
              'sender_id','username','occurred_at','event_hash']
            AND h.payload-ARRAY['schema_version','account_id','comment_id','media_id','text',
              'sender_id','username','occurred_at','event_hash']='{}'::jsonb
            AND h.payload->>'schema_version'='1'
            AND jsonb_typeof(h.payload->'schema_version')='number'
            AND jsonb_typeof(h.payload->'account_id')='string'
            AND jsonb_typeof(h.payload->'media_id')='string'
            AND jsonb_typeof(h.payload->'comment_id')='string'
            AND h.payload->>'comment_id' ~ '^[0-9]{1,64}$'
            AND jsonb_typeof(h.payload->'text')='string'
            AND length(h.payload->>'text') BETWEEN 1 AND 4000
            AND btrim(h.payload->>'text',:whitespace)<>''
            AND (h.payload->'sender_id'='null'::jsonb OR
              (jsonb_typeof(h.payload->'sender_id')='string' AND h.payload->>'sender_id' ~ '^[0-9]{1,64}$'))
            AND (h.payload->'username'='null'::jsonb OR
              (jsonb_typeof(h.payload->'username')='string' AND length(h.payload->>'username')<=64))
            AND jsonb_typeof(h.payload->'event_hash')='string'
            AND h.payload->>'event_hash' ~ '^[0-9a-f]{64}$'
            AND jsonb_typeof(h.payload->'occurred_at')='string'
            AND h.payload->>'occurred_at' ~ '^\d{4}-\d{2}-\d{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.\d{1,6})?(Z|\+00:00)$'
            AND pg_input_is_valid(h.payload->>'occurred_at','timestamp with time zone')
            AND NOT EXISTS(SELECT 1 FROM social_comment_links l
              WHERE l.tenant_id=h.tenant_id AND l.webhook_event_id=h.id)
            ORDER BY h.captured_at,h.id LIMIT :maximum
            """
            ),
            {
                "tenant": repo.tenant_id,
                "account": published["account_id"],
                "post": published["post_id"],
                "maximum": limit,
                # Match Python str.strip, including Unicode-only blank observations.
                "whitespace": "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
                "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
                "\u2028\u2029\u202f\u205f\u3000",
            },
        )
        .scalars()
        .all()
    )


def _relink_saved_comments(tenant, pid):
    from app.social.replies import ingest_webhook

    token = service_token(tenant)
    with transaction(tenant, token) as repo:
        repo.require("SOCIAL")
        published = repo.one("social_publish_runs", id=pid)
        if published["status"] != "PUBLISHED":
            raise ConflictError("A confirmed platform post is required to link saved comments")
        candidates = _saved_comment_ids(repo, published, COMMENT_RELINK_BATCH)
    linked = 0
    for hid in candidates:
        # Each successful link/audit event is its own durable checkpoint. This
        # guarded bridge takes workflow locks first; the SELECT-only runtime
        # role must not acquire row-update locks directly. No provider I/O occurs.
        with transaction(tenant, token) as repo:
            event = ingest_webhook(repo, hid)
            # Count links confirmed by this batch, including one concurrently
            # completed after selection. The guarded bridge prevents duplicates.
            linked += int(event is not None)
    with transaction(tenant, token) as repo:
        has_more = bool(_saved_comment_ids(repo, published, 1))
    return {
        "publish_run_id": pid,
        "processed": len(candidates),
        "linked": linked,
        "has_more": has_more,
        "network_performed": False,
    }


def relink_comments(tenant, caller, pid):
    with transaction(tenant, caller) as repo:
        repo.require("OPERATOR")
        published = repo.one("social_publish_runs", id=pid)
        if published["status"] != "PUBLISHED":
            raise ConflictError("A confirmed platform post is required to link saved comments")
    return _relink_saved_comments(tenant, pid)


def _relink_after_confirmation(operation):
    @wraps(operation)
    def complete(tenant, caller, pid, *args, **kwargs):
        result = operation(tenant, caller, pid, *args, **kwargs)
        if result["status"] == "PUBLISHED":
            # The original function has now committed confirmation and released
            # all transaction/session locks, including on receipt recovery paths.
            try:
                result["comment_relink"] = _relink_saved_comments(tenant, pid)
            except Exception:
                # Ancillary local repair cannot undo a confirmed external post.
                # The explicit endpoint safely resumes from committed links.
                result["comment_relink"] = {
                    "status": "RETRY_REQUIRED",
                    "network_performed": False,
                }
                logging.getLogger("mediaos").warning(
                    "social_comment_relink_pending",
                    extra={
                        "tenant_id": str(tenant),
                        "workflow_run_id": str(result["workflow_run_id"]),
                        "state": "PUBLISHED",
                        "error_category": "COMMENT_RELINK_PENDING",
                    },
                )
        return result

    return complete


def create(tenant, token, rid, request: PublishInput):
    digest = canonical_hash({"render_run_id": rid, "connection_id": request.connection_id})
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        existing = repo.all("social_publish_runs", idempotency_key=request.idempotency_key)
        if existing:
            if existing[0]["input_hash"] != digest:
                raise ConflictError("Publish idempotency conflict")
            return details(repo, existing[0]["id"])
        repo.connection.execute(text("SELECT check_render_export(:id)"), {"id": rid})
        render = repo.one("render_runs", id=rid)
        plan, images = files.prepare(
            render_directory(tenant, rid) / "files",
            typed(RenderManifest, render["manifest"]),
            request.connection_id,
            rid,
        )
        pid = repo.connection.execute(
            text("SELECT social_start_publish(:rid,:cid,CAST(:p AS jsonb),:key,:hash)"),
            {
                "rid": rid,
                "cid": request.connection_id,
                "p": plan.model_dump_json(),
                "key": request.idempotency_key,
                "hash": digest,
            },
        ).scalar_one()
        files.save_images(get_settings().social_storage_path, tenant, pid, images)
        return details(repo, pid)


def preview(repo, pid, index):
    row = repo.one("social_publish_runs", id=pid)
    return files.image_bytes(
        get_settings().social_storage_path,
        repo.tenant_id,
        pid,
        typed(PublishPlan, row["plan"]),
        index,
    )


def review(tenant, token, pid, request: PublishDecision):
    with transaction(tenant, token) as repo:
        row = repo.one("social_publish_runs", id=pid)
        if request.decision == "AUTHORIZE_PUBLISH":
            for index in range(1, len(row["plan"]["slides"]) + 1):
                preview(repo, pid, index)
        repo.connection.execute(
            text("SELECT social_decide_publish(:id,CAST(:p AS jsonb))"),
            {"id": pid, "p": request.model_dump_json()},
        )
        return details(repo, pid)


def image_url(tenant, row, index):
    base = get_settings().social_public_base_url
    parsed = urlsplit(base or "")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or parsed.port not in (None, 443)
    ):
        raise ConflictError("A public HTTPS origin is required for Instagram media")
    capability = files.sign(
        vault_key(),
        "media",
        {
            "tenant": str(tenant),
            "run": str(row["id"]),
            "index": index,
            "plan_hash": row["plan_hash"],
            "expires": int(time.time()) + 1800,
        },
    )
    assert base is not None
    return base.rstrip("/") + "/v1/social/media/" + capability


def public_image(capability):
    data = files.verify(vault_key(), "media", capability)
    tenant, pid, index = UUID(data["tenant"]), UUID(data["run"]), data["index"]
    if type(index) is not int:
        raise PermissionError("Invalid media capability")
    with transaction(tenant, service_token(tenant)) as repo:
        row = repo.one("social_publish_runs", id=pid)
        if row["plan_hash"] != data["plan_hash"] or row["status"] not in {
            "AUTHORIZED",
            "PREPARING",
            "READY",
            "PUBLISHING",
            "PUBLISHED",
        }:
            raise PermissionError("Media capability is no longer available")
        repo.connection.execute(
            text("SELECT social_token(:id,:key)"), {"id": row["connection_id"], "key": vault_key()}
        )
        return preview(repo, pid, index)


def _reserve(conn, tenant, token, pid, stage, step, inputs):
    with conn.begin():
        repo = Repository(conn, tenant, token)
        jid = conn.execute(
            text("SELECT social_reserve_job(:id,:stage,:step,:hash)"),
            {"id": pid, "stage": stage, "step": step, "hash": canonical_hash(inputs)},
        ).scalar_one()
        return repo.one("social_publish_jobs", id=jid)


def _finish(conn, tenant, token, job, result):
    conn.execute(
        text("SELECT social_finish_job(:id,CAST(:p AS jsonb))"),
        {"id": job["id"], "p": json.dumps(result)},
    )


def _fail(conn, tenant, token, job, category, retry=False, unknown=False, delay=0):
    with conn.begin():
        Repository(conn, tenant, token)
        conn.execute(
            text("SELECT social_fail_job(:id,:category,:retry,:unknown,:delay)"),
            {
                "id": job["id"],
                "category": category,
                "retry": retry,
                "unknown": unknown,
                "delay": delay or 0,
            },
        )


def _result(observation, poll=False):
    return {
        "status_code" if poll else "id": observation.payload.status_code
        if poll
        else observation.payload.id,
        "response_hash": observation.response_hash,
        "captured_at": observation.captured_at.isoformat(),
    }


@_relink_after_confirmation
def execute(tenant, caller, pid):
    from app.social_provider import SocialProviderError

    if not get_settings().social_publish_enabled:
        raise ConflictError("Manual Instagram publishing is disabled")
    with transaction(tenant, caller) as repo:
        repo.require("OPERATOR")
        row = repo.one("social_publish_runs", id=pid)
    token = service_token(tenant)
    client, connection = None, None
    folder = files.directory(get_settings().social_storage_path, tenant, pid)
    with engine().connect() as conn:
        with conn.begin():
            Repository(conn, tenant, token)
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
                {"key": "social:" + str(pid)},
            ).scalar_one()
        if not locked:
            raise ConflictError("Publish run is already executing")
        try:
            for _ in range(15):
                with conn.begin():
                    repo = Repository(conn, tenant, token)
                    row = details(repo, pid)
                    orphan = next((j for j in row["jobs"] if j["status"] == "RUNNING"), None)
                if orphan:
                    try:
                        receipt = _receipt(folder, orphan)
                    except (ValueError, ConflictError):
                        receipt = None
                    if receipt is not None:
                        with conn.begin():
                            Repository(conn, tenant, token)
                            _finish(conn, tenant, token, orphan, receipt)
                        continue
                    unknown = orphan["stage"] in {"CHILD", "CONTAINER", "PUBLISH"}
                    _fail(
                        conn,
                        tenant,
                        token,
                        orphan,
                        "PROCESS_INTERRUPTED",
                        retry=not unknown,
                        unknown=unknown,
                    )
                    break
                if row["status"] not in {"AUTHORIZED", "PREPARING", "READY"}:
                    break
                jobs = row["jobs"]
                # A persisted cooldown is returned to the operator without a busy wait.
                if any(
                    j["retryable"] and j["retry_at"] and j["retry_at"] > datetime.now(UTC)
                    for j in jobs
                ):
                    break
                if client is None:
                    client, connection = connected_provider(tenant, row["connection_id"])
                assert connection is not None
                completed = {j["step_key"]: j for j in jobs if j["status"] == "SUCCEEDED"}
                plan = typed(PublishPlan, row["plan"])
                account = connection["account_id"]
                stage, step, inputs, operation = "", "", {}, None
                for slide in plan.slides:
                    if f"child:{slide.index}" not in completed:
                        files.image_bytes(
                            get_settings().social_storage_path, tenant, pid, plan, slide.index
                        )
                        stage, step = "CHILD", f"child:{slide.index}"
                        inputs = {"plan_hash": row["plan_hash"], "index": slide.index}
                        operation = partial(
                            client.create_image,
                            account,
                            image_url(tenant, row, slide.index),
                            caption=plan.caption if len(plan.slides) == 1 else None,
                            is_carousel_item=len(plan.slides) > 1,
                        )
                        break
                if operation is None and len(plan.slides) > 1 and "container" not in completed:
                    children = [completed[f"child:{s.index}"]["result"]["id"] for s in plan.slides]
                    stage, step, inputs = (
                        "CONTAINER",
                        "container",
                        {"children": children, "plan_hash": row["plan_hash"]},
                    )
                    operation = partial(client.create_carousel, account, children, plan.caption)
                if operation is None:
                    container = completed["container" if len(plan.slides) > 1 else "child:1"][
                        "result"
                    ]["id"]
                    if row["status"] == "READY":
                        stage, step, inputs = (
                            "PUBLISH",
                            "publish",
                            {"container_id": container, "plan_hash": row["plan_hash"]},
                        )
                        operation = partial(client.publish, account, container)
                    else:
                        polls = [j for j in jobs if j["stage"] == "POLL"]
                        if (
                            polls
                            and (datetime.now(UTC) - polls[-1]["started_at"]).total_seconds() < 2
                        ):
                            break
                        pending = next(
                            (
                                j
                                for j in reversed(polls)
                                if j["status"] == "FAILED"
                                and j["retryable"]
                                and j["step_key"] not in completed
                                and j["attempt"]
                                == max(
                                    other["attempt"]
                                    for other in polls
                                    if other["step_key"] == j["step_key"]
                                )
                            ),
                            None,
                        )
                        step = pending["step_key"] if pending else f"poll:{len(polls) + 1}"
                        stage, inputs = "POLL", {"container_id": container}
                        operation = partial(client.container_status, container)
                assert operation is not None
                job = _reserve(conn, tenant, token, pid, stage, step, inputs)
                invoked = False
                try:
                    if stage == "PUBLISH":
                        with conn.begin():
                            repo = Repository(conn, tenant, token)
                            conn.execute(
                                text("SELECT social_guard_dispatch(:id)"), {"id": job["id"]}
                            )
                            for slide in plan.slides:
                                preview(repo, pid, slide.index)
                            invoked = True
                            result = _result(operation())
                            _receipt(folder, job, result)
                            _finish(conn, tenant, token, job, result)
                    else:
                        invoked = True
                        result = _result(operation(), stage == "POLL")
                        _receipt(folder, job, result)
                        with conn.begin():
                            Repository(conn, tenant, token)
                            _finish(conn, tenant, token, job, result)
                except SocialProviderError as exc:
                    _fail(
                        conn,
                        tenant,
                        token,
                        job,
                        exc.category,
                        exc.retryable,
                        exc.category == "UNKNOWN_OUTCOME",
                        exc.retry_after_seconds,
                    )
                    break
                except (ValueError, PermissionError, ConflictError, OSError):
                    uncertain = invoked and stage in {"CHILD", "CONTAINER", "PUBLISH"}
                    _fail(
                        conn,
                        tenant,
                        token,
                        job,
                        "UNKNOWN_OUTCOME" if uncertain else "POLICY_BLOCKED",
                        unknown=uncertain,
                    )
                    break
                except DBAPIError as exc:
                    if getattr(exc.orig, "sqlstate", None) != "23514":
                        # A durable receipt can recover a database outage without replay.
                        raise
                    uncertain = invoked and stage in {"CHILD", "CONTAINER", "PUBLISH"}
                    _fail(
                        conn,
                        tenant,
                        token,
                        job,
                        "UNKNOWN_OUTCOME" if uncertain else "POLICY_BLOCKED",
                        unknown=uncertain,
                    )
                    break
                if stage == "POLL" and result["status_code"] != "FINISHED":
                    break
            with conn.begin():
                return details(Repository(conn, tenant, token), pid)
        finally:
            if conn.in_transaction():
                conn.rollback()
            with conn.begin():
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                    {"key": "social:" + str(pid)},
                )


@contextmanager
def serialized(tenant, token, pid):
    with engine().connect() as conn:
        with conn.begin():
            Repository(conn, tenant, token)
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"),
                {"key": "social:" + str(pid)},
            ).scalar_one()
        if not locked:
            raise ConflictError("Social operation is already running")
        try:
            yield conn
        finally:
            if conn.in_transaction():
                conn.rollback()
            with conn.begin():
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                    {"key": "social:" + str(pid)},
                )


def observe(tenant, caller, pid, key):
    from app.social_provider import SocialProviderError

    if not get_settings().social_connect_enabled:
        raise ConflictError("Instagram connection is disabled")
    with transaction(tenant, caller) as repo:
        repo.require("OPERATOR")
        row = repo.one("social_publish_runs", id=pid)
        if row["status"] != "PUBLISHED":
            raise ConflictError("A confirmed platform post is required")
    token = service_token(tenant)
    step = "insights:" + hashlib.sha256(key.encode()).hexdigest()
    folder = files.directory(get_settings().social_storage_path, tenant, pid)
    with serialized(tenant, token, pid) as conn:
        with conn.begin():
            repo = Repository(conn, tenant, token)
            previous = repo.all("social_publish_jobs", publish_run_id=pid, step_key=step)
            last = previous[-1] if previous else None
            if last and last["status"] == "SUCCEEDED":
                return details(repo, pid)
        if last and last["status"] == "RUNNING":
            try:
                receipt = _receipt(folder, last)
            except (ValueError, ConflictError, OSError):
                receipt = None
            if receipt is None:
                _fail(conn, tenant, token, last, "PROCESS_INTERRUPTED", retry=True)
            else:
                with conn.begin():
                    Repository(conn, tenant, token)
                    _finish(conn, tenant, token, last, receipt)
            with conn.begin():
                return details(Repository(conn, tenant, token), pid)
        if last and last["retry_at"] and last["retry_at"] > datetime.now(UTC):
            with conn.begin():
                return details(Repository(conn, tenant, token), pid)
        job = _reserve(
            conn, tenant, token, pid, "INSIGHTS", step, {"post_id": row["post_id"], "key": key}
        )
        try:
            client, connection = read_job_provider(tenant, job)
            observation = client.insights(row["post_id"])
            output = PlatformObservation(
                media_id=row["post_id"],
                captured_at=observation.captured_at,
                api_version=connection["api_version"],
                raw=observation.raw,
                raw_hash=observation.raw_hash,
                metrics=observation.payload.metrics,
                definitions=observation.payload.definitions,
            ).model_dump(mode="json")
            _receipt(folder, job, output)
            with conn.begin():
                Repository(conn, tenant, token)
                _finish(conn, tenant, token, job, output)
        except SocialProviderError as exc:
            _fail(
                conn,
                tenant,
                token,
                job,
                exc.category,
                exc.retryable,
                False,
                exc.retry_after_seconds,
            )
        except (ValueError, ConflictError, OSError):
            _fail(conn, tenant, token, job, "INVALID_OUTPUT")
        with conn.begin():
            return details(Repository(conn, tenant, token), pid)


@_relink_after_confirmation
def reconcile(tenant, caller, pid, request: ReconcileInput):
    from app.social_provider import SocialProviderError

    if not get_settings().social_connect_enabled:
        raise ConflictError("Instagram connection is disabled")
    payload = request.model_dump(mode="json")
    with transaction(tenant, caller) as repo:
        repo.require("APPROVER")
        row = repo.one("social_publish_runs", id=pid)
        if row["status"] == "PUBLISHED" and row["post_id"] == request.candidate_media_id:
            return details(repo, pid)
        repo.connection.execute(
            text("SELECT social_request_reconciliation(:id,CAST(:p AS jsonb))"),
            {"id": pid, "p": json.dumps(payload)},
        )
    token = service_token(tenant)
    step = "reconcile:" + canonical_hash(payload)
    folder = files.directory(get_settings().social_storage_path, tenant, pid)
    with serialized(tenant, token, pid) as conn:
        with conn.begin():
            repo = Repository(conn, tenant, token)
            previous = repo.all("social_publish_jobs", publish_run_id=pid, step_key=step)
            last = previous[-1] if previous else None
        if last and last["status"] == "RUNNING":
            try:
                receipt = _receipt(folder, last)
            except (ValueError, ConflictError, OSError):
                receipt = None
            if receipt is None:
                _fail(conn, tenant, token, last, "PROCESS_INTERRUPTED", retry=True)
            else:
                with conn.begin():
                    Repository(conn, tenant, token)
                    _finish(conn, tenant, token, last, receipt)
            with conn.begin():
                return details(Repository(conn, tenant, token), pid)
        if last and last["retry_at"] and last["retry_at"] > datetime.now(UTC):
            with conn.begin():
                return details(Repository(conn, tenant, token), pid)
        # Persist both the human assertion and the attempt before the read.
        job = _reserve(conn, tenant, token, pid, "RECONCILE", step, payload)
        try:
            client, connection = read_job_provider(tenant, job)
            observed = client.read_media(request.candidate_media_id)
            media = observed.payload
            expected_type = "IMAGE" if len(row["plan"]["slides"]) == 1 else "CAROUSEL_ALBUM"
            if (
                media.id != request.candidate_media_id
                or media.owner is None
                or media.owner.id != connection["account_id"]
                or media.caption != row["plan"]["caption"]
                or media.media_type != expected_type
                or datetime.fromisoformat(media.timestamp.replace("Z", "+00:00"))
                < row["created_at"]
            ):
                raise ConflictError("Candidate does not match the approved account/content")
            result = _result(observed)
            _receipt(folder, job, result)
            with conn.begin():
                Repository(conn, tenant, token)
                _finish(conn, tenant, token, job, result)
        except SocialProviderError as exc:
            _fail(
                conn,
                tenant,
                token,
                job,
                exc.category,
                exc.retryable,
                False,
                exc.retry_after_seconds,
            )
        except (ValueError, ConflictError, OSError):
            _fail(conn, tenant, token, job, "CANDIDATE_MISMATCH")
        with conn.begin():
            return details(Repository(conn, tenant, token), pid)


def receive_webhook(body, signature):
    from app.social_provider import normalize_comment_webhook, verify_webhook_signature

    settings = get_settings()
    if not settings.social_connect_enabled or not settings.social_app_secret:
        raise PermissionError("Webhook intake is disabled")
    if not verify_webhook_signature(body, signature, settings.social_app_secret):
        raise PermissionError("Invalid webhook signature")
    batch = normalize_comment_webhook(body)
    accepted = 0
    for tenant_text, token in service_tokens().items():
        tenant = UUID(tenant_text)
        with transaction(tenant, token) as repo:
            connections = repo.all("social_connections")
            revoked = {item["connection_id"] for item in repo.all("social_revocations")}
        for event in batch.events:
            matches = [c for c in connections if c["account_id"] == event.account_id]
            if not matches:
                continue
            connection = max(matches, key=lambda c: c["version"])
            if connection["id"] in revoked:
                continue
            payload = event.model_dump(mode="json")
            with transaction(tenant, token) as repo:
                eventid = repo.connection.execute(
                    text("SELECT social_record_webhook(:id,:key,CAST(:p AS jsonb))"),
                    {
                        "id": connection["id"],
                        "key": event.event_hash,
                        "p": json.dumps(payload),
                    },
                ).scalar_one()
            # Commit account-locked intake before acquiring workflow locks for
            # linkage. A failed linkage remains resumable through webhook replay.
            with transaction(tenant, token) as repo:
                from app.social.replies import ingest_webhook

                ingest_webhook(repo, eventid)
                accepted += 1
    return {"accepted": accepted}
