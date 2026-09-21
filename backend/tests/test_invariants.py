import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from conftest import artifact_data, create, decision, headers, request_data, revise
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.repository import engine, transaction
from app.models.schemas import CreateRun
from app.services.workflows import Runner, create_run


def test_migration_and_runtime_role(database):
    with database.connect() as c:
        assert c.execute(text("select version_num from alembic_version")).scalar_one() == "0008"
        unprotected = c.execute(
            text(
                "select tablename from pg_tables where schemaname='public' and not rowsecurity and tablename not in ('principals','alembic_version')"
            )
        ).all()
        assert unprotected == []
    with engine().connect() as c:
        role = c.execute(
            text(
                "select current_user,rolsuper,rolbypassrls from pg_roles where rolname=current_user"
            )
        ).one()
        assert tuple(role) == ("mediaos_runtime", False, False)
        assert c.execute(text("select count(*) from workflow_runs")).scalar_one() == 0


@pytest.mark.parametrize("suffix", ["", "/artifacts", "/audit"])
def test_cross_tenant_retrieval(client, identities, suffix):
    run = create(client, identities[1], execute=False)
    assert (
        client.get(
            f"/v1/workflow-runs/{run['id']}{suffix}", headers=headers(identities[0])
        ).status_code
        == 404
    )


def test_cross_tenant_membership(client, identities):
    h = headers(identities[0])
    h["X-Tenant-ID"] = identities[1]["tenant_id"]
    assert client.get("/v1/workflow-runs", headers=h).status_code == 401


def test_context_cannot_be_forged(identities):
    with engine().begin() as c:
        c.execute(
            text("select set_config('app.tenant_id',:v,true)"), {"v": identities[0]["tenant_id"]}
        )
        assert c.execute(text("select context_tenant()")).scalar_one() is None
        assert c.execute(text("select count(*) from influencers")).scalar_one() == 0


def test_cross_tenant_mutation_approval_and_reference(client, identities):
    a, b = identities
    run = create(client, b)
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(a, "APPROVER"),
            json=decision(run),
        ).status_code
        == 404
    )
    draft = artifact_data(client, b, run)["content_asset_versions"][0]["payload"]
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/revisions", headers=headers(a), json=draft
        ).status_code
        == 404
    )
    payload = request_data(a)
    payload["influencer_id"] = b["influencer_id"]
    assert client.post("/v1/workflow-runs", headers=headers(a), json=payload).status_code == 404


@pytest.mark.parametrize("kind", ["foreign", "unrelated", "fabricated"])
def test_evidence_foreign_keys(client, identities, kind):
    a, b = identities
    run = create(client, a)
    draft = artifact_data(client, a, run)["content_asset_versions"][0]["payload"]
    if kind == "fabricated":
        fid = str(uuid4())
    else:
        other = b if kind == "foreign" else a
        other_run = create(client, other)
        fid = artifact_data(client, other, other_run)["content_asset_versions"][0]["payload"][
            "slides"
        ][0]["body"]["fact_ids"][0]
    draft["slides"][0]["body"]["fact_ids"] = [fid]
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/revisions", headers=headers(a), json=draft
    )
    assert response.status_code == 409
    assert (
        client.get(f"/v1/workflow-runs/{run['id']}", headers=headers(a)).json()["state"]
        == "AWAITING_APPROVAL"
    )


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("unsupported", "UNSUPPORTED_CLAIM"),
        ("missing_fact", "MISSING_FACT"),
        ("disclosure", "DISCLOSURE"),
        ("creative", "UNSUPPORTED_CREATIVE"),
        ("cta", "CHARACTER_POLICY"),
        ("language", "CHARACTER_POLICY"),
    ],
)
def test_qa_blocks_and_cannot_approve(client, identities, mutation, code):
    a = identities[0]
    run = create(client, a)
    draft = artifact_data(client, a, run)["content_asset_versions"][0]["payload"]
    if mutation == "unsupported":
        draft["slides"][0]["body"]["text"] = "Every business is guaranteed free money."
    if mutation == "missing_fact":
        draft["slides"][0]["body"]["fact_ids"] = []
    if mutation == "disclosure":
        draft["disclosure"] = ""
    if mutation == "creative":
        draft["caption"]["text"] = "You are guaranteed a grant."
    if mutation == "cta":
        draft["cta"]["text"] = "Buy now."
    if mutation == "language":
        draft["language"] = "xx"
    revised = revise(client, a, run, draft)
    assert revised["state"] == "BLOCKED"
    qa = artifact_data(client, a, run)["qa_reports"][-1]
    assert code in [f["code"] for f in qa["payload"]["findings"]]
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/approve",
        headers=headers(a, "APPROVER"),
        json=decision(revised),
    )
    assert response.status_code == 409


@pytest.mark.parametrize("fixture", [False, True])
def test_unverified_and_fixture_sources_block(client, identities, fixture):
    a = identities[0]
    run = create(client, a, verify=False, payload=request_data(a, is_fixture=fixture))
    assert run["state"] == "BLOCKED"
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(a, "APPROVER"),
            json=decision(run),
        ).status_code
        == 409
    )


def test_fixture_cannot_be_attested(client, identities):
    a = identities[0]
    run = create(client, a, verify=False, execute=False, payload=request_data(a, is_fixture=True))
    response = client.post(
        f"/v1/source-snapshots/{run['source_snapshot_id']}/verify",
        headers=headers(a, "APPROVER"),
        json={"decision": "VERIFIED", "comment": "Attempt to verify fixture"},
    )
    assert response.status_code == 409


def test_missing_or_wrong_source_span(client, identities):
    payload = request_data(identities[0])
    payload["source"]["evidence"][0]["statement"] = "Fabricated"
    assert (
        client.post("/v1/workflow-runs", headers=headers(identities[0]), json=payload).status_code
        == 422
    )
    payload["source"]["evidence"] = []
    assert (
        client.post("/v1/workflow-runs", headers=headers(identities[0]), json=payload).status_code
        == 422
    )


@pytest.mark.parametrize("missing_date", [True, False])
def test_grant_dates_fail_closed(client, identities, missing_date):
    a = identities[0]
    payload = request_data(a)
    grant = {
        "opening_date": "2000-01-01",
        "deadline": None if missing_date else "2001-01-01",
        "eligible_geography": ["local"],
        "eligible_business_type": ["small business"],
        "required_evidence": ["registration"],
        "eligibility_status": "VERIFIED",
        "field_evidence": {},
    }
    payload["source"]["evidence"][0].update(fact_type="GRANT", grant=grant)
    run = create(client, a, payload=payload)
    assert run["state"] == "BLOCKED"
    codes = [
        f["code"] for f in artifact_data(client, a, run)["qa_reports"][0]["payload"]["findings"]
    ]
    assert ("GRANT_REQUIRED" if missing_date else "GRANT_EXPIRED") in codes


def test_revision_required_not_blocked(client, identities):
    a = identities[0]
    run = create(client, a)
    draft = artifact_data(client, a, run)["content_asset_versions"][0]["payload"]
    draft["slides"][0]["index"] = 3
    revised = revise(client, a, run, draft)
    assert revised["state"] == "REVISION_REQUIRED"
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(a, "APPROVER"),
            json=decision(revised),
        ).status_code
        == 409
    )


def test_approval_without_qa_and_operator_permission(client, identities):
    a = identities[0]
    run = create(client, a, execute=False)
    invalid = {
        "asset_version_id": str(uuid4()),
        "research_version_id": str(uuid4()),
        "qa_report_id": str(uuid4()),
    }
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve", headers=headers(a, "APPROVER"), json=invalid
        ).status_code
        == 409
    )
    run = client.post(f"/v1/workflow-runs/{run['id']}/execute", headers=headers(a)).json()
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve", headers=headers(a), json=decision(run)
        ).status_code
        == 403
    )


@pytest.mark.parametrize("approve_first", [False, True])
def test_stale_revisions_require_fresh_qa_and_approval(client, identities, approve_first):
    a = identities[0]
    run = create(client, a)
    if approve_first:
        assert (
            client.post(
                f"/v1/workflow-runs/{run['id']}/approve",
                headers=headers(a, "APPROVER"),
                json=decision(run),
            ).status_code
            == 200
        )
    draft = artifact_data(client, a, run)["content_asset_versions"][0]["payload"]
    changed = client.post(
        f"/v1/workflow-runs/{run['id']}/revisions", headers=headers(a), json=draft
    ).json()
    assert changed["state"] == "CONTENT_COMPLETE" and changed["qa_report_id"] is None
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(a, "APPROVER"),
            json=decision(run),
        ).status_code
        == 409
    )
    fresh = client.post(f"/v1/workflow-runs/{run['id']}/execute", headers=headers(a)).json()
    assert fresh["state"] == "AWAITING_APPROVAL"
    assert fresh["qa_report_id"] != run["qa_report_id"]
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(a, "APPROVER"),
            json=decision(fresh),
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE workflow_runs SET state='APPROVED' WHERE id=:id",
        "UPDATE qa_reports SET status='PASS' WHERE workflow_run_id=:id",
        "DELETE FROM audit_events WHERE workflow_run_id=:id",
        "SELECT checkpoint(:id,'APPROVED',NULL)",
    ],
)
def test_database_approval_bypass_rejected(client, identities, sql):
    a = identities[0]
    run = create(client, a)
    with (
        pytest.raises(DBAPIError),
        transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo,
    ):
        repo.connection.execute(text(sql), {"id": UUID(run["id"])})


def test_invalid_transition(client, identities):
    a = identities[0]
    run = create(client, a, execute=False)
    with (
        pytest.raises(DBAPIError),
        transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo,
    ):
        repo.checkpoint(UUID(run["id"]), "CONTENT_COMPLETE", uuid4())


def test_idempotency_same_payload_conflict_and_other_tenant(client, identities):
    a, b = identities
    payload = request_data(a)
    first = create(client, a, verify=False, execute=False, payload=payload)
    second = create(client, a, verify=False, execute=False, payload=payload)
    assert first["id"] == second["id"]
    changed = copy.deepcopy(payload)
    changed["source"]["title"] = "Different title"
    assert client.post("/v1/workflow-runs", headers=headers(a), json=changed).status_code == 409
    other = request_data(b)
    other["idempotency_key"] = payload["idempotency_key"]
    assert create(client, b, verify=False, execute=False, payload=other)["id"] != first["id"]


def test_concurrent_idempotency(identities):
    a = identities[0]
    data = CreateRun.model_validate_json(json.dumps(request_data(a)))

    def submit(_):
        return create_run(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"], data, uuid4())["id"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(submit, range(6)))
    assert len(set(ids)) == 1


def test_api_auth_schema_and_limits(client, identities):
    a = identities[0]
    assert client.get("/v1/health").json() == {"status": "ok"}
    assert (
        client.get(
            "/v1/workflow-runs", headers={**headers(a), "Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    assert client.get("/v1/readiness", headers=headers(a)).status_code == 200
    payload = request_data(a)
    payload["source"]["is_fixture"] = "false"
    assert client.post("/v1/workflow-runs", headers=headers(a), json=payload).status_code == 422
    assert (
        client.post("/v1/workflow-runs", headers=headers(a), content=b"x" * 262145).status_code
        == 413
    )


def test_retryable_failure_and_no_duplicate_artifacts(client, identities, monkeypatch):
    from app.skills import creator

    original = creator.create_carousel
    attempts = 0

    def flaky(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("private details must not be logged")
        return original(*args, **kwargs)

    monkeypatch.setattr(creator, "create_carousel", flaky)
    a = identities[0]
    run = create(client, a)
    data = artifact_data(client, a, run)
    assert len(data["content_asset_versions"]) == 1
    tries = [s for s in data["skill_runs"] if s["skill_identifier"] == "content.carousel"]
    assert [s["status"] for s in tries] == ["FAILED", "SUCCEEDED"]
    assert tries[0]["retry_at"] is not None


def test_nonretryable_failure(client, identities, monkeypatch):
    from app.skills import creator

    monkeypatch.setattr(
        creator,
        "create_carousel",
        lambda *args: (_ for _ in ()).throw(ValueError("invalid output")),
    )
    a = identities[0]
    run = create(client, a, execute=False)
    result = client.post(f"/v1/workflow-runs/{run['id']}/execute", headers=headers(a))
    assert result.status_code == 409
    stored = client.get(f"/v1/workflow-runs/{run['id']}", headers=headers(a)).json()
    assert stored["state"] == "FAILED"
    tries = [
        s
        for s in artifact_data(client, a, run)["skill_runs"]
        if s["skill_identifier"] == "content.carousel"
    ]
    assert len(tries) == 1 and not tries[0]["retryable"]


def test_restart_resume_checkpoint(client, identities, monkeypatch):
    from app.skills import creator

    original = creator.create_carousel
    a = identities[0]
    run = create(client, a, execute=False)

    def interrupted(*args):
        raise SystemExit("simulated process interruption")

    monkeypatch.setattr(creator, "create_carousel", interrupted)
    with pytest.raises(SystemExit):
        Runner(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]).execute(UUID(run["id"]))
    monkeypatch.setattr(creator, "create_carousel", original)
    resumed = Runner(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]).execute(UUID(run["id"]))
    assert resumed["state"] == "AWAITING_APPROVAL"
    data = artifact_data(client, a, run)
    assert (
        len(data["research_pack_versions"])
        == len(data["content_briefs"])
        == len(data["content_asset_versions"])
        == 1
    )
    assert any(s["status"] == "INTERRUPTED" for s in data["skill_runs"])


def test_concurrent_revision_and_approval(client, identities):
    a = identities[0]
    run = create(client, a)
    draft = artifact_data(client, a, run)["content_asset_versions"][0]["payload"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        approve = pool.submit(
            client.post,
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(a, "APPROVER"),
            json=decision(run),
        )
        change = pool.submit(
            client.post, f"/v1/workflow-runs/{run['id']}/revisions", headers=headers(a), json=draft
        )
        assert change.result().status_code == 200
        assert approve.result().status_code in (200, 409)
    current = client.get(f"/v1/workflow-runs/{run['id']}", headers=headers(a)).json()
    assert current["state"] == "CONTENT_COMPLETE" and current["qa_report_id"] is None


def test_reject_records_human_decision(client, identities):
    a = identities[0]
    run = create(client, a)
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/reject", headers=headers(a, "APPROVER"), json=decision(run)
    )
    assert response.status_code == 200
    assert response.json()["workflow"]["state"] == "REVISION_REQUIRED"
    assert artifact_data(client, a, run)["approval_records"][0]["decision"] == "REJECT"


def test_pooled_connection_forgets_authentication(identities):
    a, b = identities
    with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
        assert all(str(r["tenant_id"]) == a["tenant_id"] for r in repo.all("influencers"))
    with engine().begin() as conn:
        assert conn.execute(text("select context_tenant()")).scalar_one() is None
        assert conn.execute(text("select count(*) from influencers")).scalar_one() == 0
    with transaction(UUID(b["tenant_id"]), b["tokens"]["OPERATOR"]) as repo:
        assert all(str(r["tenant_id"]) == b["tenant_id"] for r in repo.all("influencers"))


def test_source_and_approval_records_are_immutable(client, identities):
    a = identities[0]
    run = create(client, a)
    client.post(
        f"/v1/workflow-runs/{run['id']}/approve", headers=headers(a, "APPROVER"), json=decision(run)
    )
    for statement in (
        "update source_snapshots set is_fixture=false where workflow_run_id=:id",
        "update approval_records set decision='REJECT' where workflow_run_id=:id",
        "delete from content_asset_versions where workflow_run_id=:id",
    ):
        with (
            pytest.raises(DBAPIError),
            transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo,
        ):
            repo.connection.execute(text(statement), {"id": UUID(run["id"])})


def test_retry_exhaustion(client, identities, monkeypatch):
    from app.skills import creator

    def unavailable(*args):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(creator, "create_carousel", unavailable)
    a = identities[0]
    run = create(client, a, execute=False)
    assert (
        client.post(f"/v1/workflow-runs/{run['id']}/execute", headers=headers(a)).status_code == 409
    )
    data = artifact_data(client, a, run)
    tries = [s for s in data["skill_runs"] if s["skill_identifier"] == "content.carousel"]
    assert len(tries) == 3 and all(t["status"] == "FAILED" for t in tries)
    assert not data["content_asset_versions"]
    assert (
        client.get(f"/v1/workflow-runs/{run['id']}", headers=headers(a)).json()["state"] == "FAILED"
    )


def test_sealed_evidence_links_and_terminal_skill_runs(client, identities):
    a = identities[0]
    run = create(client, a)
    data = artifact_data(client, a, run)
    skill_id = data["skill_runs"][0]["id"]
    with (
        pytest.raises(DBAPIError),
        transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo,
    ):
        repo.update_skill(UUID(skill_id), status="FAILED")
    fact = data["research_pack_versions"][0]["payload"]["facts"][0]
    with (
        pytest.raises(DBAPIError),
        transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo,
    ):
        repo.insert(
            "content_claims",
            asset_version_id=UUID(run["asset_version_id"]),
            brief_id=UUID(run["brief_id"]),
            research_version_id=UUID(run["research_version_id"]),
            fact_id=UUID(fact["id"]),
            field_path="extra",
        )


def test_operator_cannot_attest_source(client, identities):
    a = identities[0]
    run = create(client, a, verify=False, execute=False)
    assert (
        client.post(
            f"/v1/source-snapshots/{run['source_snapshot_id']}/verify",
            headers=headers(a),
            json={"decision": "VERIFIED", "comment": "Attempted self-verification"},
        ).status_code
        == 403
    )


def test_model_output_schema_rejects_coercion():
    from pydantic import ValidationError

    from app.models.schemas import QAReport

    with pytest.raises(ValidationError):
        QAReport.model_validate_json(
            '{"schema_version":"1","status":"PASS","findings":[]}', strict=True
        )


def test_feature_flags_fail_closed(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv("ENABLE_EXTERNAL_CREATORS", "true")
    with pytest.raises(ValidationError):
        Settings.model_validate({})


def test_logs_do_not_serialize_credentials():
    import logging

    from app.observability import SafeJsonFormatter

    record = logging.LogRecord("mediaos", logging.ERROR, "", 1, "skill_failed", (), None)
    record.authorization = "Bearer secret"
    record.raw_content = "Private document"
    result = SafeJsonFormatter().format(record)
    assert "secret" not in result and "Private document" not in result


def test_concurrent_source_attestation_is_once_only(client, identities):
    a = identities[0]
    run = create(client, a, verify=False, execute=False)
    path = f"/v1/source-snapshots/{run['source_snapshot_id']}/verify"

    def attest(decision):
        return client.post(
            path,
            headers=headers(a, "APPROVER"),
            json={"decision": decision, "comment": "Independent review"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attest, ("VERIFIED", "REJECTED")))
    assert sorted(results) == [200, 409]


@pytest.mark.parametrize("case", ["outside", "backwards", "tab_trim", "blank"])
def test_invalid_evidence_span_is_rejected_before_persistence(client, identities, case):
    a = identities[0]
    payload = request_data(a)
    fact = payload["source"]["evidence"][0]
    if case == "outside":
        fact["end"] += 1
    elif case == "backwards":
        fact["start"] = fact["end"]
    elif case == "tab_trim":
        payload["source"]["raw_content"] = "\t" + payload["source"]["raw_content"] + "\n"
        fact["end"] += 2
    else:
        payload["source"]["raw_content"] = "\t"
        fact.update(start=0, end=1, statement="\t")
    assert client.post("/v1/workflow-runs", headers=headers(a), json=payload).status_code == 422
    with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
        assert not repo.all("workflow_runs", idempotency_key=payload["idempotency_key"])


def test_rejection_can_record_decision_after_evidence_becomes_ineligible(
    client, identities, database
):
    a = identities[0]
    run = create(client, a)
    # Trusted admin fixture simulates evidence revoked after QA; runtime has no such write privilege.
    with database.begin() as c:
        c.execute(
            text("update source_snapshots set verification_status='REJECTED' where id=:id"),
            {"id": UUID(run["source_snapshot_id"])},
        )
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(a, "APPROVER"),
            json=decision(run),
        ).status_code
        == 409
    )
    result = client.post(
        f"/v1/workflow-runs/{run['id']}/reject", headers=headers(a, "APPROVER"), json=decision(run)
    )
    assert result.status_code == 200
    assert result.json()["workflow"]["state"] == "REVISION_REQUIRED"


def test_character_rollback_creates_active_version_and_tokens_can_rotate(database, identities):
    from app.models.schemas import CharacterConfig
    from app.seed import seed

    a = identities[0]
    with database.connect() as c:
        payload = c.execute(
            text("select payload from character_config_versions where tenant_id=:id limit 1"),
            {"id": UUID(a["tenant_id"])},
        ).scalar_one()
    config = CharacterConfig.model_validate(payload)
    tokens = {role: str(uuid4()) for role in ("OPERATOR", "APPROVER", "ADMIN")}
    slug = "config-" + str(uuid4())
    ids = seed(
        database, slug, "Config tenant", "Generic creator", "Generic mission", config, tokens
    )
    changed = config.model_copy(update={"tone": "Changed tone"})
    seed(database, slug, "Config tenant", "Generic creator", "Generic mission", changed, tokens)
    seed(database, slug, "Config tenant", "Generic creator", "Generic mission", config, tokens)
    # Repeated identical seed is idempotent.
    seed(database, slug, "Config tenant", "Generic creator", "Generic mission", config, tokens)
    with transaction(UUID(ids["tenant_id"]), tokens["OPERATOR"]) as repo:
        configs = repo.all("character_config_versions")
        versions = repo.all("influencer_versions")
        assert len(configs) == len(versions) == 3
        current = max(configs, key=lambda row: row["version"])
        assert current["payload"] == config.model_dump(mode="json")
    old = tokens["OPERATOR"]
    tokens["OPERATOR"] = str(uuid4())
    seed(database, slug, "Config tenant", "Generic creator", "Generic mission", config, tokens)
    with pytest.raises(DBAPIError), transaction(UUID(ids["tenant_id"]), old):
        pass
    with transaction(UUID(ids["tenant_id"]), tokens["OPERATOR"]) as repo:
        assert repo.all("influencers")


def test_brief_uses_selected_mission_objective(client, identities, database):
    a = identities[0]
    mid = uuid4()
    objective = "Help readers understand a specific internal process."
    with database.begin() as c:
        c.execute(
            text(
                "insert into missions(id,tenant_id,influencer_id,name,objective) values(:id,:tenant,:influencer,'Secondary mission',:objective)"
            ),
            {
                "id": mid,
                "tenant": UUID(a["tenant_id"]),
                "influencer": UUID(a["influencer_id"]),
                "objective": objective,
            },
        )
    payload = request_data(a)
    payload["mission_id"] = str(mid)
    run = create(client, a, payload=payload)
    assert artifact_data(client, a, run)["content_briefs"][0]["payload"]["objective"] == objective


def test_cross_tenant_sql_mutation_is_blocked_by_rls(client, identities):
    a, b = identities
    run = create(client, b)
    sid = artifact_data(client, b, run)["skill_runs"][0]["id"]
    with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
        result = repo.connection.execute(
            text("update skill_runs set status='FAILED' where id=:id"), {"id": UUID(sid)}
        )
        assert result.rowcount == 0
        assert (
            repo.connection.execute(
                text("select count(*) from workflow_runs where id=:id"), {"id": UUID(run["id"])}
            ).scalar_one()
            == 0
        )


def test_runtime_refuses_admin_database_credentials(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://mediaos_admin@127.0.0.1/mediaos")
    with pytest.raises(ValidationError):
        Settings.model_validate({})


def test_grant_metadata_cannot_hide_under_general_fact_type(client, identities):
    payload = request_data(identities[0])
    payload["source"]["evidence"][0]["grant"] = {"deadline": "2001-01-01"}
    assert (
        client.post("/v1/workflow-runs", headers=headers(identities[0]), json=payload).status_code
        == 422
    )


def test_duplicate_evidence_is_rejected_before_workflow_creation(client, identities):
    a = identities[0]
    payload = request_data(a)
    payload["source"]["evidence"].append(copy.deepcopy(payload["source"]["evidence"][0]))
    assert client.post("/v1/workflow-runs", headers=headers(a), json=payload).status_code == 422
    with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
        assert not repo.all("workflow_runs", idempotency_key=payload["idempotency_key"])
