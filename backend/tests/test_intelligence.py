import hashlib
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import httpx
import pytest
from conftest import headers
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.repository import transaction
from app.intelligence.connectors import BDNSConnector, BOEConnector, CamaraConnector, bdns_reference
from app.intelligence.editorial import evaluate
from app.intelligence.http import FetchResult, OfficialHTTP, SourcePolicyError, SourceUnavailable
from app.intelligence.ingestion import IngestionRunner, create_ingestion, persist_document
from app.intelligence.policy import (
    changes,
    duplicate_similarity,
    editorial_decision,
    opportunity_status,
    score_opportunity,
)
from app.intelligence.schemas import (
    Audience,
    DiscoveryPage,
    DiscoveryRequest,
    DocumentRef,
    EditorialPolicy,
    ScoreWeights,
)
from app.intelligence.seed import seed_intelligence
from app.services.workflows import ConflictError


def test_letter_sector_does_not_exclude_numeric_division():
    version = version_for_policy()
    version["payload"]["profile"]["nace"] = ["I"]
    audience = Audience(code="RESTAURANT", name="Restaurants", nace_prefixes=["56"])
    policy = EditorialPolicy()
    assert score_opportunity(version, audience, policy, date(2026, 9, 20)).eligibility == "UNKNOWN"
    version["closing_date"] = date(2025, 1, 1)
    assert (
        score_opportunity(version, audience, policy, date(2026, 9, 20)).eligibility == "INELIGIBLE"
    )


def test_source_cooldown_survives_new_key_and_tenant(intelligence, database, monkeypatch):
    import app.intelligence.ingestion as ingestion

    a, b = intelligence
    host = "www.infosubvenciones.es"
    calls = []

    def limited(request):
        calls.append(request.url)
        return httpx.Response(429, headers={"Retry-After": "120"})

    monkeypatch.setattr(
        ingestion,
        "OfficialHTTP",
        lambda *args, **kwargs: OfficialHTTP(
            *args, **kwargs, transport=httpx.MockTransport(limited)
        ),
    )
    try:
        run = create_ingestion(
            a["tenant"], a["tokens"]["OPERATOR"], request_for(a["bdns"]), uuid4()
        )
        with pytest.raises(SourceUnavailable):
            IngestionRunner(a["tenant"], a["ingestor"]).execute(run["id"])
        for identity in (a, b):
            fresh = create_ingestion(
                identity["tenant"],
                identity["tokens"]["OPERATOR"],
                request_for(identity["bdns"]),
                uuid4(),
            )
            with pytest.raises(ConflictError, match="backoff"):
                IngestionRunner(identity["tenant"], identity["ingestor"]).execute(fresh["id"])
        assert len(calls) == 1
        with pytest.raises(DBAPIError), transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("SELECT record_source_backoff(:host,:until_time)"),
                {"host": host, "until_time": datetime.now(UTC) + timedelta(seconds=120)},
            )
    finally:
        with database.begin() as conn:
            conn.execute(
                text("DELETE FROM private.source_host_cooldowns WHERE hostname=:host"),
                {"host": host},
            )


def test_malformed_boe_document_advances_checkpoint(intelligence, monkeypatch):
    a = intelligence[0]
    refs = [
        DocumentRef(
            external_id="BOE-B-2026-" + str(i),
            url="https://www.boe.es/diario_boe/xml.php?id=BOE-B-2026-" + str(i),
        )
        for i in (11111, 30165)
    ]
    monkeypatch.setattr(
        BOEConnector, "discover", lambda *args: DiscoveryPage(documents=refs, next_cursor=None)
    )

    class RecordedBOE:
        def get(self, url):
            return (
                response("<documento>", url) if "11111" in url else fixture("boe-notice.xml", url)
            )

    run = create_ingestion(a["tenant"], a["tokens"]["OPERATOR"], request_for(a["boe"]), uuid4())
    result = IngestionRunner(a["tenant"], a["ingestor"], RecordedBOE()).execute(run["id"])
    assert result["status"] == "SUCCEEDED"
    assert result["document_offset"] == 2
    assert result["counters"]["documents_fetched"] == 2
    assert result["counters"]["extraction_failures"] == 1
    with transaction(a["tenant"], a["ingestor"]) as repo:
        raw = repo.all("raw_source_documents", ingestion_run_id=run["id"])
        assert any(row["body"] == "<documento>" for row in raw)


FIXTURES = Path(__file__).parent / "fixtures/official"


def fixture(name, url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=929780"):
    body = (FIXTURES / name).read_text(encoding="utf-8")
    return response(body, url)


def response(body, url):
    return FetchResult(
        url=url,
        body=body,
        media_type="application/json",
        captured_at=datetime.now(UTC),
        checksum=hashlib.sha256(body.encode()).hexdigest(),
        is_fixture=True,
    )


def request_for(source, key=None, **kwargs):
    return DiscoveryRequest(
        source_definition_id=source,
        idempotency_key=key or str(uuid4()),
        since=date(2026, 9, 1),
        until=date(2026, 9, 20),
        **kwargs,
    )


@pytest.fixture(scope="session")
def intelligence(identities, database):
    result = []
    for identity in identities:
        token = secrets.token_urlsafe(32)
        seed_intelligence(database, identity["tenant_id"], identity["mission_id"], token)
        result.append(
            {
                **identity,
                "ingestor": token,
                "tenant": UUID(identity["tenant_id"]),
                "bdns": uuid5(UUID(identity["tenant_id"]), "source:BDNS"),
                "boe": uuid5(UUID(identity["tenant_id"]), "source:BOE"),
            }
        )
    return result


class RecordedHTTP:
    """Recorded responses always propagate is_fixture=True into persisted evidence."""

    def __init__(self, docs, fail_on=None):
        self.docs = docs
        self.fail_on = fail_on
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if self.fail_on and len(self.calls) == self.fail_on:
            raise KeyboardInterrupt("Simulated process interruption")
        if "busqueda?" in url:
            return response(
                json.dumps(
                    {"content": [{"numeroConvocatoria": code} for code in self.docs], "last": True}
                ),
                url,
            )
        code = url.split("numConv=")[-1]
        return response(json.dumps(self.docs[code], ensure_ascii=False), url)


def document(code=None, **changes):
    data = json.loads((FIXTURES / "bdns-929780.json").read_text(encoding="utf-8"))
    data["codigoBDNS"] = code or str(10**10 + uuid4().int % 10**10)
    data.update(changes)
    return data


def ingest(identity, docs, http=None):
    run = create_ingestion(
        identity["tenant"], identity["tokens"]["OPERATOR"], request_for(identity["bdns"]), uuid4()
    )
    result = IngestionRunner(
        identity["tenant"], identity["ingestor"], http or RecordedHTTP(docs)
    ).execute(run["id"])
    return result


def test_recorded_fixture_checksums():
    for entry in json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8")):
        assert (
            hashlib.sha256((FIXTURES / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]
        )
        assert entry["is_fixture"] is True


def test_bdns_structural_extraction_preserves_unknowns():
    result = BDNSConnector().normalize(
        fixture("bdns-929780.json"),
        DocumentRef(
            external_id="929780",
            url="https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=929780",
        ),
    )
    assert result.canonical_external_id == "ES:BDNS:929780"
    assert result.profile.application_deadline == date(2027, 2, 12)
    assert result.profile.application_start is None
    assert result.profile.maximum_amount is None
    assert result.profile.available_budget == "75000"
    assert result.profile.microenterprise == "UNKNOWN"
    assert result.profile.autonomo == "UNKNOWN"
    assert all(result.source_text[f.start : f.end] == f.statement for f in result.fields)


def test_relative_deadline_is_not_inferred():
    result = BDNSConnector().normalize(
        fixture("bdns-relative-deadline.json"), DocumentRef(external_id="930103", url="unused")
    )
    assert result.profile.application_deadline is None
    assert any(f.field == "closing_rule" for f in result.fields)


def test_bdns_pagination():
    class Pages:
        def get(self, url):
            return response(
                json.dumps({"content": [{"numeroConvocatoria": "929780"}], "last": False}), url
            )

    page = BDNSConnector().discover(Pages(), request_for(uuid4()), "2")
    assert page.next_cursor == "3"
    assert page.documents[0].external_id == "929780"


def test_boe_summary_and_notice():
    class Summary:
        def get(self, url):
            return fixture("boe-summary.json", url)

    page = BOEConnector().discover(Summary(), request_for(uuid4()), "2026-09-18")
    ref = next(r for r in page.documents if r.external_id == "BOE-B-2026-30165")
    result = BOEConnector().normalize(fixture("boe-notice.xml", ref.url), ref)
    assert result.external_ids["BOE"] == ref.external_id
    assert "BDNS" in result.external_ids
    assert result.canonical_external_id.startswith("ES:BDNS:")
    assert result.publication_date == date(2026, 9, 18)
    assert any("pdf" in url for url in result.linked_material)


def test_ambiguous_cross_reference_does_not_merge():
    assert bdns_reference("BDNS (Identif.): 123456. BDNS: 654321") is None
    assert bdns_reference("BDNS (Identif.): 123456") == "123456"


def test_camara_recorded_parser_and_access_policy():
    connector = CamaraConnector()
    listing = connector.parse_listing(
        fixture("camara-listing.html", "https://sede.camara.es/sede/")
    )
    assert listing.documents
    result = connector.normalize(
        fixture("camara-programme.html", "https://sede.camara.es/sede/tramites/TR0000006758"),
        DocumentRef(
            external_id="TR0000006758", url="https://sede.camara.es/sede/tramites/TR0000006758"
        ),
    )
    assert "Somos Futuro" in result.title
    assert result.issuing_body == "Cámara de España"
    assert result.profile.application_start == date(2026, 9, 15)
    assert result.profile.application_deadline == date(2026, 9, 28)
    assert result.profile.sme == "UNKNOWN"
    with pytest.raises(SourcePolicyError):
        connector.discover(None, None, "")


@pytest.mark.parametrize(
    "url",
    [
        "http://www.boe.es/",
        "https://127.0.0.1/",
        "https://www.boe.es.evil.test/",
        "https://user:password@www.boe.es/",
        "https://www.boe.es:444/",
    ],
)
def test_transport_rejects_non_official_targets(url):
    http = OfficialHTTP(["www.boe.es"], sleep=lambda _: None)
    try:
        with pytest.raises(SourcePolicyError):
            http.get(url)
    finally:
        http.close()


def test_transport_retry_rate_limit_and_telemetry():
    calls, events, sleeps = [], [], []

    def handler(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "2"})
            if len(calls) == 1
            else httpx.Response(200, text="official data")
        )

    http = OfficialHTTP(
        ["www.boe.es"],
        transport=httpx.MockTransport(handler),
        recorder=lambda **x: events.append(x),
        sleep=sleeps.append,
    )
    try:
        assert http.get("https://www.boe.es/a").body == "official data"
        assert len(events) == 2 and events[0]["retry_delay"] == 2
        assert "User-Agent" in calls[0].headers
        assert 2 in sleeps
    finally:
        http.close()


def test_transport_bounded_retry_and_long_retry_after():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    http = OfficialHTTP(
        ["www.boe.es"], transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    try:
        with pytest.raises(SourceUnavailable):
            http.get("https://www.boe.es/a")
        assert len(calls) == 1
    finally:
        http.close()


def test_transport_size_limit():
    http = OfficialHTTP(
        ["www.boe.es"],
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 2_000_001)),
        sleep=lambda _: None,
    )
    try:
        with pytest.raises(SourceUnavailable):
            http.get("https://www.boe.es/a")
    finally:
        http.close()


def test_ingestion_idempotency_and_concurrency(intelligence):
    a, b = intelligence
    request = request_for(a["bdns"])

    def create():
        return create_ingestion(a["tenant"], a["tokens"]["OPERATOR"], request, uuid4())["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: create(), range(4)))
    assert len(set(ids)) == 1
    with pytest.raises(ConflictError):
        create_ingestion(
            a["tenant"],
            a["tokens"]["OPERATOR"],
            request.model_copy(update={"query": "different"}),
            uuid4(),
        )
    second = create_ingestion(
        b["tenant"],
        b["tokens"]["OPERATOR"],
        request_for(b["bdns"], key=request.idempotency_key),
        uuid4(),
    )
    assert second["id"] != ids[0]


def test_ingest_roundtrip_and_unchanged_dedup(intelligence):
    a = intelligence[0]
    doc = document()
    docs = {doc["codigoBDNS"]: doc}
    first = ingest(a, docs)
    second = ingest(a, docs)
    assert first["counters"]["new_opportunities"] == 1
    assert second["counters"]["unchanged_opportunities"] == 1
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        opportunity = repo.one(
            "opportunities", canonical_external_id="ES:BDNS:" + doc["codigoBDNS"]
        )
        assert len(repo.all("opportunity_versions", opportunity_id=opportunity["id"])) == 1
        link = repo.one("source_links", opportunity_id=opportunity["id"])
        snapshot = repo.one("source_snapshots", id=link["source_snapshot_id"])
        raw = repo.one("raw_source_documents", id=snapshot["raw_document_id"])
        raw_snapshot = repo.one("source_snapshots", id=snapshot["parent_snapshot_id"])
        assert raw_snapshot["representation"] == "RAW"
        assert raw_snapshot["raw_content"] == raw["body"]
        assert raw["is_fixture"] and snapshot["is_fixture"]
        assert json.loads(raw["body"])["codigoBDNS"] == doc["codigoBDNS"]
        assert snapshot["verification_status"] == "UNVERIFIED"
        assert repo.all("opportunity_facts", opportunity_id=opportunity["id"])


def test_change_events_and_revision_immutability(intelligence):
    a = intelligence[0]
    doc = document()
    code = doc["codigoBDNS"]
    ingest(a, {code: doc})
    doc["fechaFinSolicitud"] = "2027-03-12"
    ingest(a, {code: doc})
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        o = repo.one("opportunities", canonical_external_id="ES:BDNS:" + code)
        versions = repo.all("opportunity_versions", opportunity_id=o["id"])
        assert [v["version"] for v in versions] == [1, 2]
        event = repo.one("change_events", opportunity_id=o["id"], event_type="DEADLINE_CHANGED")
        assert (
            event["old_version_id"] == versions[0]["id"]
            and event["new_version_id"] == versions[1]["id"]
        )
    with pytest.raises(DBAPIError), transaction(a["tenant"], a["ingestor"]) as repo:
        repo.connection.execute(
            text("UPDATE opportunity_versions SET title='changed' WHERE id=:id"),
            {"id": versions[0]["id"]},
        )


def test_fixture_cannot_be_attested_or_selected(intelligence):
    a = intelligence[0]
    doc = document()
    ingest(a, {doc["codigoBDNS"]: doc})
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        o = repo.one("opportunities", canonical_external_id="ES:BDNS:" + doc["codigoBDNS"])
        link = repo.one("source_links", opportunity_id=o["id"])
        scores = evaluate(repo, o, UUID(a["mission_id"]))
        assert len(scores) == 7
        assert all(s["decision"]["decision"] == "HUMAN_REVIEW" for s in scores)
    with pytest.raises(DBAPIError), transaction(a["tenant"], a["ingestor"]) as repo:
        repo.connection.execute(
            text("SELECT attest_official_snapshot(:id)"), {"id": link["source_snapshot_id"]}
        )


def test_resume_after_interruption_no_duplicate_artifacts(intelligence):
    a = intelligence[0]
    d1 = document()
    d2 = document()
    docs = {d["codigoBDNS"]: d for d in (d1, d2)}
    run = create_ingestion(a["tenant"], a["tokens"]["OPERATOR"], request_for(a["bdns"]), uuid4())
    with pytest.raises(KeyboardInterrupt):
        IngestionRunner(a["tenant"], a["ingestor"], RecordedHTTP(docs, fail_on=3)).execute(
            run["id"]
        )
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        stored = repo.one("ingestion_runs", id=run["id"])
        assert stored["status"] == "RUNNING" and stored["document_offset"] == 1
    http = RecordedHTTP(docs)
    resumed = IngestionRunner(a["tenant"], a["ingestor"], http).execute(run["id"])
    assert resumed["status"] == "SUCCEEDED" and resumed["counters"]["documents_fetched"] == 2
    assert len(http.calls) == 1
    again = IngestionRunner(a["tenant"], a["ingestor"], http).execute(run["id"])
    assert again["id"] == resumed["id"] and len(http.calls) == 1


def test_cross_tenant_ingestion_and_mutation(client, intelligence):
    a, b = intelligence
    doc = document()
    ingest(a, {doc["codigoBDNS"]: doc})
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        o = repo.one("opportunities", canonical_external_id="ES:BDNS:" + doc["codigoBDNS"])
    assert (
        client.get(f"/v1/intelligence/opportunities/{o['id']}", headers=headers(b)).status_code
        == 404
    )
    with transaction(b["tenant"], b["tokens"]["OPERATOR"]) as repo:
        assert (
            repo.connection.execute(
                text("SELECT id FROM opportunities WHERE id=:id"), {"id": o["id"]}
            ).first()
            is None
        )
    with pytest.raises(DBAPIError), transaction(b["tenant"], b["ingestor"]) as repo:
        repo.insert(
            "duplicate_candidates",
            opportunity_id=o["id"],
            candidate_id=uuid4(),
            score=0.95,
            status="POSSIBLE_DUPLICATE",
            algorithm="test",
        )


def test_operator_cannot_write_official_facts(intelligence):
    a = intelligence[0]
    with pytest.raises(DBAPIError), transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        repo.insert(
            "opportunities", canonical_external_id="FAKE", country="ES", opportunity_type="SUBSIDY"
        )


def test_false_semantic_duplicate_is_not_merged(intelligence):
    a = intelligence[0]
    d1 = document()
    d2 = document()
    assert duplicate_similarity(d1["descripcion"], d2["descripcion"]) == 1
    result = ingest(a, {d["codigoBDNS"]: d for d in (d1, d2)})
    assert result["counters"]["new_opportunities"] == 2
    assert result["counters"]["duplicate_candidates"] >= 1


def test_boe_and_bdns_link_and_conflict(intelligence):
    a = intelligence[0]
    doc = document()
    ingest(a, {doc["codigoBDNS"]: doc})
    ref = DocumentRef(
        external_id="BOE-B-2026-30165",
        url="https://www.boe.es/diario_boe/xml.php?id=BOE-B-2026-30165",
    )
    original = fixture("boe-notice.xml", ref.url)
    original_code = bdns_reference(original.body)
    assert original_code is not None
    # Change the recorded evidence itself, never a parsed claim without source support.
    captured = response(
        original.body.replace(original_code, doc["codigoBDNS"]).replace(
            "</texto>", "<p>Fin de solicitud: 2027-04-12</p></texto>"
        ),
        ref.url,
    )
    parsed = BOEConnector().normalize(captured, ref)
    run = create_ingestion(a["tenant"], a["tokens"]["OPERATOR"], request_for(a["boe"]), uuid4())
    with transaction(a["tenant"], a["ingestor"]) as repo:
        definition = repo.one("source_definitions", id=a["boe"])
        result = persist_document(repo, run, definition, captured, ref, parsed)
        assert result["outcome"] == "changed_opportunities" and result["conflicts"] == 1
        links = repo.all("source_links", opportunity_id=result["opportunity_id"])
        assert len(links) == 2
        opportunity = repo.one("opportunities", id=result["opportunity_id"])
        assert (
            repo.one("opportunity_versions", id=opportunity["current_version_id"])["status"]
            == "CONFLICT"
        )


def version_for_policy(**changes_):
    result = {
        "title": "Ayudas para PYMES",
        "payload": {
            "country": "ES",
            "profile": {
                "sme": "YES",
                "nace": ["56"],
                "industries": [],
                "application_start": "2026-01-01",
                "application_deadline": "2027-01-01",
            },
        },
        "opening_date": date(2026, 1, 1),
        "closing_date": date(2027, 1, 1),
        "max_amount": None,
        "source_confidence": 1.0,
        "created_at": datetime(2026, 9, 18, tzinfo=UTC),
    }
    result.update(changes_)
    return result


def test_audience_eligibility_is_separate_from_score():
    v = version_for_policy()
    p = EditorialPolicy()
    restaurant = score_opportunity(
        v,
        Audience(code="RESTAURANT", name="Restaurants", nace_prefixes=["56"]),
        p,
        date(2026, 9, 20),
    )
    salon = score_opportunity(
        v, Audience(code="SALON", name="Salons", nace_prefixes=["96"]), p, date(2026, 9, 20)
    )
    assert restaurant.eligibility == "POTENTIALLY_ELIGIBLE"
    assert salon.eligibility == "INELIGIBLE"
    assert restaurant.components["financial_value"] == 0
    assert restaurant.final_score > salon.final_score
    assert restaurant.weights.brand_strategic_fit == 0.02


def test_missing_and_expired_deadlines_fail_closed():
    p = EditorialPolicy()
    a = Audience(code="GENERIC", name="Business")
    v = version_for_policy(closing_date=None)
    score = score_opportunity(v, a, p, date(2026, 9, 20))
    assert (
        editorial_decision(v, score, p, {"objective": "Value"}, [], [], True).decision
        == "HUMAN_REVIEW"
    )
    v = version_for_policy(closing_date=date(2025, 1, 1))
    assert score_opportunity(v, a, p, date(2026, 9, 20)).eligibility == "INELIGIBLE"
    assert opportunity_status({"application_deadline": "2025-01-01"}, date(2026, 9, 20)) == "CLOSED"


def test_relevance_weights_validate_and_editor_history():
    with pytest.raises(ValueError):
        ScoreWeights(brand_strategic_fit=0.9)
    v = version_for_policy()
    p = EditorialPolicy()
    s = score_opportunity(v, Audience(code="GENERIC", name="Business"), p, date(2026, 9, 20))
    assert (
        editorial_decision(v, s, p, {"objective": "Value"}, [], [], True).decision
        == "CREATE_CONTENT"
    )
    assert (
        editorial_decision(
            v, s, p, {"objective": "Value"}, [{"workflow_run_id": uuid4()}], [], True
        ).decision
        == "WATCH"
    )
    assert (
        editorial_decision(
            v, s, p, {"objective": "Value"}, [], [{"conflict": "deadline"}], True
        ).decision
        == "HUMAN_REVIEW"
    )


def test_date_changes_are_deterministic():
    old = {
        "title": "x",
        "status": "UPCOMING",
        "payload": {"source_hashes": {}},
        "opening_date": date(2026, 1, 1),
    }
    new = {**old, "status": "OPEN", "opening_date": "2026-01-01"}
    events = changes(old, new)
    assert {e.type for e in events} == {"STATUS_CHANGED", "APPLICATION_OPENED"}


def test_intelligence_api_auth_and_contracts(client, intelligence):
    a = intelligence[0]
    assert client.get("/v1/intelligence/sources").status_code in (401, 422)
    sources = client.get("/v1/intelligence/sources", headers=headers(a)).json()
    assert len(sources) == 3
    assert next(s for s in sources if s["connector"] == "CAMARA")["enabled"] is False
    request = request_for(a["bdns"])
    denied = client.post(
        "/v1/intelligence/ingestions",
        headers=headers(a, "APPROVER"),
        json=request.model_dump(mode="json"),
    )
    assert denied.status_code == 403
    created = client.post(
        "/v1/intelligence/ingestions", headers=headers(a), json=request.model_dump(mode="json")
    )
    assert created.status_code == 200 and created.json()["status"] == "CREATED"


def test_raw_snapshot_commits_before_normalization_failure(intelligence):
    a = intelligence[0]
    doc = document()
    doc.pop("descripcion")
    result = ingest(a, {doc["codigoBDNS"]: doc})
    assert result["counters"]["extraction_failures"] == 1
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        raw = repo.one(
            "raw_source_documents", ingestion_run_id=result["id"], document_key=doc["codigoBDNS"]
        )
        snapshots = repo.all("source_snapshots", raw_document_id=raw["id"])
        assert len(snapshots) == 1 and snapshots[0]["representation"] == "RAW"
        assert snapshots[0]["raw_content"] == raw["body"]


def test_raw_snapshot_immutability_and_cross_tenant_parent(intelligence):
    a, b = intelligence
    doc = document()
    ingest(a, {doc["codigoBDNS"]: doc})
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        o = repo.one("opportunities", canonical_external_id="ES:BDNS:" + doc["codigoBDNS"])
        normalized = repo.one(
            "source_snapshots",
            id=repo.one("source_links", opportunity_id=o["id"])["source_snapshot_id"],
        )
    with pytest.raises(DBAPIError), transaction(a["tenant"], a["ingestor"]) as repo:
        repo.connection.execute(
            text("UPDATE source_snapshots SET raw_content='replacement' WHERE id=:id"),
            {"id": normalized["parent_snapshot_id"]},
        )
    with transaction(b["tenant"], b["ingestor"]) as repo:
        assert (
            repo.connection.execute(
                text("SELECT id FROM source_snapshots WHERE id=:id"),
                {"id": normalized["parent_snapshot_id"]},
            ).first()
            is None
        )


def test_boe_pagination_keeps_remainder_of_daily_summary():
    class Summary:
        def get(self, url):
            return fixture("boe-summary.json", url)

    connector = BOEConnector()
    request = request_for(uuid4(), page_size=1)
    first = connector.discover(Summary(), request, "2026-09-18")
    assert first.next_cursor == "2026-09-18:1"
    second = connector.discover(Summary(), request, first.next_cursor)
    assert first.documents[0].external_id != second.documents[0].external_id


def test_boe_missing_daily_issue_is_empty_not_execution_failure():
    from app.intelligence.http import SourceNotFound

    class Missing:
        def get(self, url):
            raise SourceNotFound()

    result = BOEConnector().discover(Missing(), request_for(uuid4()), "2026-09-18")
    assert result.documents == [] and result.next_cursor == "2026-09-19"


def test_normalized_fact_rejects_wrong_evidence_span():
    from app.intelligence.schemas import NormalizedOpportunity

    parsed = BDNSConnector().normalize(
        fixture("bdns-929780.json"), DocumentRef(external_id="929780", url="unused")
    )
    payload = parsed.model_dump(mode="json")
    payload["fields"][0]["statement"] = "Unsupported fabricated grant promise"
    with pytest.raises(ValueError):
        NormalizedOpportunity.model_validate_json(json.dumps(payload))


def test_repeated_capture_records_observations_without_false_changes(intelligence):
    a = intelligence[0]
    doc = document()
    ingest(a, {doc["codigoBDNS"]: doc})
    second = ingest(a, {doc["codigoBDNS"]: doc})
    assert second["counters"]["unchanged_opportunities"] == 1
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        raw = repo.one(
            "raw_source_documents", source_definition_id=a["bdns"], document_key=doc["codigoBDNS"]
        )
        assert len(repo.all("source_observations", raw_document_id=raw["id"])) == 2


def test_source_reversion_creates_new_version_with_original_evidence(intelligence):
    a = intelligence[0]
    doc = document()
    code = doc["codigoBDNS"]
    original_deadline = doc["fechaFinSolicitud"]
    ingest(a, {code: doc})
    doc["fechaFinSolicitud"] = "2027-05-12"
    ingest(a, {code: doc})
    doc["fechaFinSolicitud"] = original_deadline
    third = ingest(a, {code: doc})
    assert third["counters"]["changed_opportunities"] == 1
    with transaction(a["tenant"], a["tokens"]["OPERATOR"]) as repo:
        o = repo.one("opportunities", canonical_external_id="ES:BDNS:" + code)
        versions = repo.all("opportunity_versions", opportunity_id=o["id"])
        assert len(versions) == 3
        assert versions[-1]["closing_date"] == date.fromisoformat(original_deadline)
        assert versions[-1]["payload"]["source_hashes"] == versions[0]["payload"]["source_hashes"]


def test_minimis_requires_explicit_positive_source_label():
    doc = document(code="929780")
    doc["reglamento"] = {"descripcion": "No sometido al régimen de minimis"}
    data = response(
        json.dumps(doc),
        "https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=929780",
    )
    result = BDNSConnector().normalize(data, DocumentRef(external_id="929780", url=data.url))
    assert result.profile.de_minimis == "UNKNOWN"


def test_bdns_rejects_invalid_budget_and_does_not_invert_exclusion():
    doc = document(code="929780")
    doc["tiposBeneficiarios"] = [{"descripcion": "NO PYMES"}]
    url = "https://www.infosubvenciones.es/bdnstrans/api/convocatorias?numConv=929780"
    parsed = BDNSConnector().normalize(
        response(json.dumps(doc), url), DocumentRef(external_id="929780", url=url)
    )
    assert parsed.profile.sme == "UNKNOWN"
    doc["presupuestoTotal"] = True
    with pytest.raises(ValueError):
        BDNSConnector().normalize(
            response(json.dumps(doc), url), DocumentRef(external_id="929780", url=url)
        )


def test_brief_uses_selected_audience_without_changing_character(identities):
    from app.models.schemas import CharacterConfig, ResearchPack
    from app.services.workflows import typed
    from app.skills.editor import build_brief

    a = identities[0]
    with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
        config = typed(CharacterConfig, repo.all("character_config_versions")[0]["payload"])
    result = build_brief(
        ResearchPack(facts=[], verification_status="VERIFIED"),
        config,
        "Selected mission",
        audience=["Dental clinics"],
    )
    assert result.audience == ["Dental clinics"]
    assert (
        result.tone == config.tone
        and result.brand_association_level == config.brand_association_level
    )
