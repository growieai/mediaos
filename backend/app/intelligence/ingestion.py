import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.config import REPO_ROOT, get_settings
from app.db.repository import Repository, canonical_hash, engine, transaction
from app.intelligence.connectors import CONNECTORS
from app.intelligence.http import FetchResult, OfficialHTTP, SourceCoolingDown, SourceUnavailable
from app.intelligence.policy import changes, duplicate_similarity, opportunity_status
from app.intelligence.schemas import DiscoveryPage, DiscoveryRequest, NormalizedOpportunity
from app.observability import request_id
from app.services.workflows import ConflictError, typed

log = logging.getLogger("mediaos")


def service_token(tenant_id):
    settings = get_settings()
    if settings.intelligence_tokens:
        data = json.loads(settings.intelligence_tokens.get_secret_value())
    elif settings.app_env == "development":
        path = REPO_ROOT / ".local/ingestion-credentials.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    else:
        data = {}
    token = data.get(str(tenant_id))
    if not token:
        raise PermissionError("No trusted ingestion identity configured for this tenant")
    return token


def create_ingestion(tenant, token, request: DiscoveryRequest, correlation_id):
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    digest = canonical_hash(payload)
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        repo.one("source_definitions", id=request.source_definition_id)
        table = repo.table("ingestion_runs")
        row = (
            repo.connection.execute(
                insert(table)
                .values(
                    tenant_id=tenant,
                    source_definition_id=request.source_definition_id,
                    created_by=repo.principal_id,
                    correlation_id=correlation_id,
                    idempotency_key=request.idempotency_key,
                    input_hash=digest,
                    request=request.model_dump(mode="json"),
                    mode=request.mode,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "idempotency_key"])
                .returning(table)
            )
            .mappings()
            .first()
        )
        if row:
            return dict(row)
        existing = repo.one("ingestion_runs", idempotency_key=request.idempotency_key)
        if existing["input_hash"] != digest:
            raise ConflictError("Ingestion idempotency key has a different payload")
        return existing


def update_run(repo, run_id, **values):
    table = repo.table("ingestion_runs")
    repo.connection.execute(
        table.update()
        .where(table.c.tenant_id == repo.tenant_id, table.c.id == run_id)
        .values(**values)
    )


def increment(counters, **values):
    result = dict(counters)
    for key, value in values.items():
        result[key] = result.get(key, 0) + value
    return result


def save_raw(repo, run, definition, response: FetchResult, document_key):
    table = repo.table("raw_source_documents")
    row = (
        repo.connection.execute(
            insert(table)
            .values(
                tenant_id=repo.tenant_id,
                source_definition_id=definition["id"],
                ingestion_run_id=run["id"],
                document_key=document_key,
                url=response.url,
                body=response.body,
                checksum=response.checksum,
                media_type=response.media_type,
                captured_at=response.captured_at,
                is_fixture=response.is_fixture,
                etag=response.etag,
                environment=get_settings().app_env,
                parser_version=definition["parser_version"],
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "source_definition_id", "document_key", "checksum"]
            )
            .returning(table)
        )
        .mappings()
        .first()
    )
    saved = (
        dict(row)
        if row
        else repo.one(
            "raw_source_documents",
            source_definition_id=definition["id"],
            document_key=document_key,
            checksum=response.checksum,
        )
    )
    observations = repo.table("source_observations")
    repo.connection.execute(
        insert(observations)
        .values(
            tenant_id=repo.tenant_id,
            raw_document_id=saved["id"],
            ingestion_run_id=run["id"],
            fetched_at=response.captured_at,
            is_fixture=response.is_fixture,
        )
        .on_conflict_do_nothing()
    )
    return saved


CONFLICT_FIELDS = (
    "application_start",
    "application_deadline",
    "maximum_amount",
    "minimum_amount",
    "currency",
    "eligible_cost_percentage",
    "sme",
    "autonomo",
    "microenterprise",
)


def material_value(value):
    return value not in (None, "", [], "UNKNOWN")


def capture_raw_snapshot(repo, run, definition, response, document_key):
    raw = save_raw(repo, run, definition, response, document_key)
    existing = repo.all(
        "source_snapshots", raw_document_id=raw["id"], workflow_run_id=None, representation="RAW"
    )
    if existing:
        return raw, existing[0]
    snapshot = repo.insert(
        "source_snapshots",
        workflow_run_id=None,
        raw_document_id=raw["id"],
        source_definition_id=definition["id"],
        representation="RAW",
        source_type="OFFICIAL",
        origin=raw["url"],
        canonical_url=raw["url"],
        title=definition["source_key"] + ":" + document_key,
        publisher=definition["source_key"],
        raw_content=raw["body"],
        checksum=raw["checksum"],
        captured_at=raw["captured_at"],
        classification="PRIMARY",
        is_fixture=raw["is_fixture"],
        metadata={"parser_version": definition["parser_version"]},
        evidence_input=[],
        environment=raw["environment"],
        created_by=repo.principal_id,
    )
    if not raw["is_fixture"]:
        repo.connection.execute(
            text("SELECT attest_official_snapshot(:id)"), {"id": snapshot["id"]}
        )
    return raw, snapshot


def persist_document(
    repo, run, definition, response: FetchResult, reference, normalized: NormalizedOpportunity
):
    raw, original = capture_raw_snapshot(repo, run, definition, response, reference.external_id)
    snapshots = repo.all(
        "source_snapshots",
        raw_document_id=raw["id"],
        workflow_run_id=None,
        representation="NORMALIZED",
    )
    if snapshots:
        snapshot = snapshots[0]
    else:
        evidence = [
            {
                "start": f.start,
                "end": f.end,
                "statement": f.statement,
                "fact_type": "GENERAL",
                "grant": None,
            }
            for f in normalized.fields[:10]
        ]
        snapshot = repo.insert(
            "source_snapshots",
            workflow_run_id=None,
            raw_document_id=raw["id"],
            source_definition_id=definition["id"],
            representation="NORMALIZED",
            parent_snapshot_id=original["id"],
            source_type="OFFICIAL",
            origin=response.url,
            canonical_url=response.url,
            title=normalized.title,
            publisher=normalized.issuing_body,
            raw_content=normalized.source_text,
            checksum=hashlib.sha256(normalized.source_text.encode()).hexdigest(),
            captured_at=raw["captured_at"],
            classification="PRIMARY",
            is_fixture=raw["is_fixture"],
            metadata={
                "parser_version": definition["parser_version"],
                "raw_response_sha256": raw["checksum"],
                "canonical_external_id": normalized.canonical_external_id,
            },
            evidence_input=evidence,
            environment=get_settings().app_env,
            created_by=repo.principal_id,
        )
        if not raw["is_fixture"]:
            repo.connection.execute(
                text("SELECT attest_official_snapshot(:id)"), {"id": snapshot["id"]}
            )
            snapshot = repo.one("source_snapshots", id=snapshot["id"])
    # Canonical IDs have a tenant-scoped unique constraint; no title similarity merge.
    table = repo.table("opportunities")
    repo.connection.execute(
        insert(table)
        .values(
            tenant_id=repo.tenant_id,
            canonical_external_id=normalized.canonical_external_id,
            country=normalized.country,
            opportunity_type=normalized.opportunity_type,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "canonical_external_id"])
    )
    opportunity = repo.one("opportunities", canonical_external_id=normalized.canonical_external_id)
    repo.connection.execute(text("SELECT touch_opportunity(:id)"), {"id": opportunity["id"]})
    opportunity = repo.one("opportunities", id=opportunity["id"])
    previous = (
        repo.one("opportunity_versions", id=opportunity["current_version_id"])
        if opportunity["current_version_id"]
        else None
    )
    existing = repo.all(
        "source_links", opportunity_id=opportunity["id"], source_snapshot_id=snapshot["id"]
    )
    repo.connection.execute(text("SELECT touch_opportunity(:id)"), {"id": opportunity["id"]})
    if (
        existing
        and previous
        and str(snapshot["id"]) in previous["payload"]["source_hashes"]
        and previous["status"]
        == opportunity_status(
            previous["payload"]["profile"],
            datetime.now(UTC).date(),
            previous["status"] == "CONFLICT",
        )
    ):
        return {
            "opportunity_id": opportunity["id"],
            "outcome": "unchanged_opportunities",
            "conflicts": 0,
            "duplicates": 0,
        }
    if not existing:
        repo.insert(
            "source_links",
            opportunity_id=opportunity["id"],
            source_snapshot_id=snapshot["id"],
            source_definition_id=definition["id"],
            document_key=reference.external_id,
            external_ids=normalized.external_ids,
            normalized_payload=normalized.model_dump(mode="json"),
            linkage_method="AUTHORITATIVE_REFERENCE"
            if len(normalized.external_ids) > 1
            else "CANONICAL_ID",
        )
        for f in normalized.fields:
            repo.insert(
                "opportunity_facts",
                id=uuid5(snapshot["id"], f"{f.field}:{f.start}:{f.end}"),
                opportunity_id=opportunity["id"],
                source_snapshot_id=snapshot["id"],
                fact_type=f.field,
                normalized_value=f.value,
                unit=f.unit,
                statement=f.statement,
                span_start=f.start,
                span_end=f.end,
                raw_locator=f.locator,
                extractor_version=definition["parser_version"],
                version=1,
                verification_status=snapshot["verification_status"],
                confidence=f.confidence if snapshot["verification_status"] == "VERIFIED" else 0.0,
            )
    # Latest observation per source document; previous observations remain immutable history.
    latest = {}
    observation_times: dict[tuple[UUID, str], datetime] = {}
    for link in repo.all("source_links", opportunity_id=opportunity["id"]):
        linked_snapshot = repo.one("source_snapshots", id=link["source_snapshot_id"])
        observations = repo.all(
            "source_observations", raw_document_id=linked_snapshot["raw_document_id"]
        )
        observed = max([o["fetched_at"] for o in observations] or [linked_snapshot["captured_at"]])
        key = (link["source_definition_id"], link["document_key"])
        if key not in latest or observed > observation_times[key]:
            latest[key] = link
            observation_times[key] = observed
    links = list(latest.values())
    conflicts = 0
    for other in links:
        if (
            other["source_snapshot_id"] == snapshot["id"]
            or other["source_definition_id"] == definition["id"]
        ):
            continue
        for field in CONFLICT_FIELDS:
            left = other["normalized_payload"]["profile"].get(field)
            right = normalized.profile.model_dump(mode="json").get(field)
            if material_value(left) and material_value(right) and left != right:
                table = repo.table("verification_conflicts")
                row = repo.connection.execute(
                    insert(table)
                    .values(
                        tenant_id=repo.tenant_id,
                        opportunity_id=opportunity["id"],
                        field=field,
                        left_snapshot_id=other["source_snapshot_id"],
                        right_snapshot_id=snapshot["id"],
                        left_value=left,
                        right_value=right,
                    )
                    .on_conflict_do_nothing()
                    .returning(table.c.id)
                ).first()
                conflicts += bool(row)
    unresolved = repo.all("verification_conflicts", opportunity_id=opportunity["id"])
    # Stable ordering is for filling UNKNOWN fields only. Conflicting values are NEVER silently accepted.
    links.sort(
        key=lambda link: (
            repo.one("source_definitions", id=link["source_definition_id"])[
                "connector_configuration"
            ].get("normalization_priority", 100),
            str(link["source_definition_id"]),
            link["document_key"],
        )
    )
    primary = links[0]
    merged = dict(primary["normalized_payload"])
    profile = dict(merged["profile"])
    for link in links[1:]:
        for key, value in link["normalized_payload"]["profile"].items():
            if not material_value(profile.get(key)) and material_value(value):
                profile[key] = value
    merged["profile"] = profile
    merged["source_hashes"] = {
        str(link["source_snapshot_id"]): repo.one(
            "source_snapshots", id=link["source_snapshot_id"]
        )["checksum"]
        for link in links
    }
    merged["conflict_ids"] = [str(c["id"]) for c in unresolved]
    merged["uncertainty"] = sorted(
        {u for link in links for u in link["normalized_payload"]["uncertainty"]}
    )
    status = opportunity_status(profile, datetime.now(UTC).date(), bool(unresolved))
    values = dict(
        title=merged["title"],
        issuing_body=merged["issuing_body"],
        geography=profile["geography"],
        business_types=profile["applicant_types"],
        industries=profile["industries"],
        max_amount=profile["maximum_amount"],
        currency=profile["currency"],
        funding_percentage=profile["eligible_cost_percentage"],
        opening_date=profile["application_start"],
        closing_date=profile["application_deadline"],
        application_url=merged["application_url"],
        status=status,
        source_confidence=0.0 if unresolved or snapshot["is_fixture"] else 1.0,
        payload=merged,
    )
    version = repo.insert(
        "opportunity_versions",
        opportunity_id=opportunity["id"],
        version=previous["version"] + 1 if previous else 1,
        schema_version=1,
        content_hash=canonical_hash({**merged, "status": status}),
        source_snapshot_id=snapshot["id"],
        **values,
    )
    for change in changes(previous, values):
        repo.insert(
            "change_events",
            opportunity_id=opportunity["id"],
            old_version_id=previous["id"] if previous else None,
            new_version_id=version["id"],
            source_snapshot_id=snapshot["id"],
            event_type=change.type,
            materiality=change.materiality,
            payload=change.model_dump(mode="json"),
        )
    candidates = 0
    if previous is None:
        for other in repo.all("opportunities"):
            if other["id"] == opportunity["id"] or not other["current_version_id"]:
                continue
            other_version = repo.one("opportunity_versions", id=other["current_version_id"])
            similarity = duplicate_similarity(version["title"], other_version["title"])
            if similarity >= 0.85:
                repo.insert(
                    "duplicate_candidates",
                    opportunity_id=opportunity["id"],
                    candidate_id=other["id"],
                    score=similarity,
                    status="POSSIBLE_DUPLICATE",
                    algorithm="normalized-title-sequence-v1",
                )
                candidates += 1
    return {
        "opportunity_id": opportunity["id"],
        "outcome": "changed_opportunities" if previous else "new_opportunities",
        "conflicts": conflicts,
        "duplicates": candidates,
    }


class IngestionRunner:
    def __init__(self, tenant, token=None, http=None):
        self.tenant = UUID(str(tenant))
        self.token = token or service_token(tenant)
        self.http = http

    def execute(self, ingestion_id):
        with engine().connect() as lock_connection:
            with lock_connection.begin():
                repo = Repository(lock_connection, self.tenant, self.token)
                repo.require("INGESTOR")
                run = repo.one("ingestion_runs", id=ingestion_id)
                definition = repo.one("source_definitions", id=run["source_definition_id"])
                lock_key = f"ingestion:{self.tenant}:{definition['id']}"
                acquired = lock_connection.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:key,0))"), {"key": lock_key}
                ).scalar_one()
            if not acquired:
                raise ConflictError("Source ingestion is already executing")
            try:
                correlation_context = request_id.set(str(run["correlation_id"]))
                return self._execute(run, definition)
            finally:
                request_id.reset(correlation_context)
                with lock_connection.begin():
                    lock_connection.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                        {"key": lock_key},
                    )

    def _execute(self, initial, definition):
        if initial["status"] == "SUCCEEDED":
            return initial
        if initial["retry_at"] and initial["retry_at"] > datetime.now(UTC):
            raise ConflictError("Source backoff has not elapsed")
        if not definition["enabled"] or definition["access_policy"] != "PUBLIC_API":
            raise PermissionError("Source access policy forbids automated fetching")
        connector = CONNECTORS[definition["connector"]]
        if connector.version != definition["parser_version"]:
            raise ConflictError("Source parser version differs from deployed connector")

        def cooldown(host):
            with transaction(self.tenant, self.token) as repo:
                return repo.connection.execute(
                    text("SELECT source_backoff(:host)"), {"host": host}
                ).scalar_one_or_none()

        for host in connector.hosts:
            retry_at = cooldown(host)
            if retry_at and retry_at > datetime.now(UTC):
                raise ConflictError("Official source backoff has not elapsed")
        request = typed(DiscoveryRequest, initial["request"])
        ingestion_id = initial["id"]

        def record(**event):
            with transaction(self.tenant, self.token) as repo:
                raw = (
                    save_raw(repo, initial, definition, event["result"], "HTTP:" + event["url"])
                    if event["result"]
                    else None
                )
                delay = event["retry_delay"]
                if delay:
                    repo.connection.execute(
                        text("SELECT record_source_backoff(:host,:until_time)"),
                        {
                            "host": urlsplit(event["url"]).hostname,
                            "until_time": datetime.now(UTC) + timedelta(seconds=delay),
                        },
                    )
                repo.insert(
                    "ingestion_attempts",
                    ingestion_run_id=ingestion_id,
                    url=event["url"],
                    attempt=event["attempt"],
                    status_code=event["status_code"],
                    latency_ms=event["latency_ms"],
                    error_category=event["error"],
                    retry_at=datetime.now(UTC) + timedelta(seconds=delay) if delay else None,
                    raw_document_id=raw["id"] if raw else None,
                )

        def cache(url):
            with transaction(self.tenant, self.token) as repo:
                matches = repo.all(
                    "raw_source_documents",
                    source_definition_id=definition["id"],
                    document_key="HTTP:" + url,
                    is_fixture=False,
                )
                observed = []
                for raw in matches:
                    observations = repo.all(
                        "source_observations", raw_document_id=raw["id"], is_fixture=False
                    )
                    last_fetch = max(
                        [o["fetched_at"] for o in observations] or [raw["captured_at"]]
                    )
                    if last_fetch > datetime.now(UTC) - timedelta(minutes=15):
                        observed.append((last_fetch, raw))
            if not observed:
                return None
            fetched_at, raw = max(observed, key=lambda value: value[0])
            return FetchResult(
                url=raw["url"],
                body=raw["body"],
                media_type=raw["media_type"],
                captured_at=fetched_at,
                checksum=raw["checksum"],
                etag=raw["etag"],
            )

        http = self.http or OfficialHTTP(
            connector.hosts,
            interval=definition["polling_policy"].get("minimum_interval_seconds", 1.5),
            recorder=record,
            cache=cache,
            cooldown=cooldown,
        )
        with transaction(self.tenant, self.token) as repo:
            update_run(
                repo,
                ingestion_id,
                status="RUNNING",
                started_at=initial["started_at"] or datetime.now(UTC),
                ended_at=None,
                error_category=None,
                retry_at=None,
            )
        try:
            while True:
                with transaction(self.tenant, self.token) as repo:
                    run = repo.one("ingestion_runs", id=ingestion_id)
                if run["pending_page"] is None:
                    if run["counters"].get("pages_fetched", 0) >= request.max_pages:
                        break
                    page = connector.discover(http, request, run["cursor"])
                    with transaction(self.tenant, self.token) as repo:
                        update_run(
                            repo,
                            ingestion_id,
                            pending_page=page.model_dump(mode="json"),
                            document_offset=0,
                            counters=increment(run["counters"], pages_fetched=1),
                        )
                    continue
                page = typed(DiscoveryPage, run["pending_page"])
                if run["document_offset"] >= len(page.documents):
                    if page.next_cursor is None:
                        break
                    with transaction(self.tenant, self.token) as repo:
                        update_run(
                            repo,
                            ingestion_id,
                            pending_page=None,
                            cursor=page.next_cursor,
                            document_offset=0,
                        )
                    continue
                reference = page.documents[run["document_offset"]]
                response = http.get(reference.url)
                # Raw bytes are committed even if subsequent parsing fails.
                with transaction(self.tenant, self.token) as repo:
                    capture_raw_snapshot(repo, run, definition, response, reference.external_id)
                try:
                    normalized = connector.normalize(response, reference)
                except (ValueError, KeyError, TypeError):
                    with transaction(self.tenant, self.token) as repo:
                        update_run(
                            repo,
                            ingestion_id,
                            document_offset=run["document_offset"] + 1,
                            counters=increment(
                                run["counters"], documents_fetched=1, extraction_failures=1
                            ),
                        )
                    continue
                with transaction(self.tenant, self.token) as repo:
                    result = persist_document(
                        repo, run, definition, response, reference, normalized
                    )
                    update_run(
                        repo,
                        ingestion_id,
                        document_offset=run["document_offset"] + 1,
                        counters=increment(
                            run["counters"],
                            documents_fetched=1,
                            **{result["outcome"]: 1},
                            verification_conflicts=result["conflicts"],
                            duplicate_candidates=result["duplicates"],
                        ),
                    )
            with transaction(self.tenant, self.token) as repo:
                run = repo.one("ingestion_runs", id=ingestion_id)
                attempts = repo.all("ingestion_attempts", ingestion_run_id=ingestion_id)
                counters = dict(run["counters"])
                for key in (
                    "pages_fetched",
                    "documents_fetched",
                    "new_opportunities",
                    "changed_opportunities",
                    "unchanged_opportunities",
                    "duplicate_candidates",
                    "extraction_failures",
                    "verification_conflicts",
                    "model_calls",
                    "input_tokens",
                    "output_tokens",
                    "cost",
                ):
                    counters.setdefault(key, 0)
                counters["retries"] = sum(a["attempt"] > 1 for a in attempts)
                counters["latency_ms"] = (
                    datetime.now(UTC) - run["started_at"]
                ).total_seconds() * 1000
                update_run(
                    repo,
                    ingestion_id,
                    status="SUCCEEDED",
                    ended_at=datetime.now(UTC),
                    counters=counters,
                )
                result = repo.one("ingestion_runs", id=ingestion_id)
            log.info(
                "ingestion_finished",
                extra={
                    "tenant_id": str(self.tenant),
                    "ingestion_run_id": str(ingestion_id),
                    "state": "SUCCEEDED",
                    "correlation_id": str(initial["correlation_id"]),
                },
            )
            return result
        except Exception as exc:
            with transaction(self.tenant, self.token) as repo:
                attempts = repo.all("ingestion_attempts", ingestion_run_id=ingestion_id)
                backoff = max(
                    [a["retry_at"] for a in attempts if a["retry_at"]] or [datetime.now(UTC)]
                )
                update_run(
                    repo,
                    ingestion_id,
                    status="FAILED",
                    ended_at=datetime.now(UTC),
                    error_category=type(exc).__name__,
                    retry_at=exc.retry_at
                    if isinstance(exc, SourceCoolingDown)
                    else (backoff if isinstance(exc, SourceUnavailable) else None),
                )
            raise
        finally:
            if self.http is None:
                http.close()
