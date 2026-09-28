import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from conftest import artifact_data, decision, headers, request_data
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.repository import transaction
from app.studio.schemas import SourceDraft


def preview(client, identity, **changes):
    result = client.post(
        "/v1/studio/source-drafts",
        headers=headers(identity),
        json={"influencer_id": identity["influencer_id"], "title": "A thoughtful story"} | changes,
    )
    assert result.status_code == 200, result.text
    SourceDraft.model_validate_json(result.text, strict=True)
    return result.json()


def generated_source(draft):
    source = {
        key: draft[key]
        for key in (
            "source_type",
            "origin",
            "title",
            "publisher",
            "raw_content",
            "classification",
            "is_fixture",
            "metadata",
        )
    }
    statement = draft["raw_content"].split("\n")[0]
    return source | {
        "captured_at": datetime.now(UTC).isoformat(),
        "evidence": [{"start": 0, "end": len(statement), "statement": statement}],
    }


def test_source_draft_auth_role_tenant_and_no_artifact_side_effects(client, identities, caplog):
    a, b = identities
    endpoint = "/v1/studio/source-drafts"
    payload = {"influencer_id": a["influencer_id"], "title": "Private planning title"}
    assert client.post(endpoint, json=payload).status_code == 401
    assert client.post(endpoint, headers=headers(a, "APPROVER"), json=payload).status_code == 403
    assert client.post(endpoint, headers=headers(b), json=payload).status_code == 404
    assert (
        client.post(
            endpoint, headers=headers(a) | {"X-Tenant-ID": b["tenant_id"]}, json=payload
        ).status_code
        == 401
    )
    tables = ("workflow_runs", "source_snapshots", "skill_runs", "cost_events", "audit_events")
    with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
        before = {table: len(repo.all(table)) for table in tables}
    result = client.post(endpoint, headers=headers(a), json=payload)
    admin = client.post(endpoint, headers=headers(a, "ADMIN"), json=payload)
    assert result.status_code == admin.status_code == 200
    assert result.json() == admin.json()
    assert "no-store" in result.headers["cache-control"]
    with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
        assert before == {table: len(repo.all(table)) for table in tables}
    assert "Private planning title" not in caplog.text


def test_source_draft_real_mode_and_untyped_input_rejected(client, identities, monkeypatch):
    a = identities[0]
    endpoint = "/v1/studio/source-drafts"
    payload = {"influencer_id": a["influencer_id"], "title": "Ideas"}
    for data in (
        payload | {"title": " "},
        payload | {"title": "x" * 201},
        payload | {"is_fixture": False},
        payload | {"language": "es"},
    ):
        assert client.post(endpoint, headers=headers(a), json=data).status_code == 422
    monkeypatch.setattr(get_settings(), "ai_mock_mode", False)
    result = client.post(endpoint, headers=headers(a), json=payload)
    assert result.status_code == 409 and "mock mode only" in result.text


def test_source_draft_uses_current_persisted_spanish_configuration(client, identities, monkeypatch):
    a = identities[0]
    monkeypatch.setattr(get_settings(), "enable_external_creators", True)
    created = client.post(
        "/v1/studio/influencers",
        headers=headers(a),
        json={
            "idempotency_key": str(uuid4()),
            "name": "Luz",
            "category_id": "food",
            "language": "es",
            "tone": "WARM",
            "audience": ["Cocineros curiosos"],
            "objective": "Explorar ideas de cocina con fuentes y cuidado.",
        },
    )
    assert created.status_code == 200, created.text
    item = created.json()
    draft = preview(client, a, influencer_id=item["id"], title="Preguntas para cocinar")
    assert draft["mission_id"] == item["mission_id"] and draft["language"] == "es"
    assert "Explorar ideas de cocina con fuentes y cuidado." in draft["raw_content"]
    assert (
        "Cocineros curiosos" in draft["raw_content"]
        and "NO ES EVIDENCIA VERIFICADA" in draft["raw_content"]
    )


def test_saved_generated_source_stays_fixture_cannot_verify_or_approve(client, identities):
    a = identities[0]
    draft = preview(client, a)
    payload = request_data(a) | {"source": generated_source(draft)}
    submitted = client.post("/v1/workflow-runs", headers=headers(a), json=payload)
    assert submitted.status_code == 200, submitted.text
    run = submitted.json()
    assert (
        client.post("/v1/workflow-runs", headers=headers(a), json=payload).json()["id"] == run["id"]
    )
    source = artifact_data(client, a, run)["source_snapshots"][0]
    assert source["source_type"] == "GENERATED" and source["is_fixture"] is True
    assert source["raw_content"] == draft["raw_content"] and source["metadata"] == draft["metadata"]
    verification = client.post(
        f"/v1/source-snapshots/{run['source_snapshot_id']}/verify",
        headers=headers(a, "APPROVER"),
        json={"decision": "VERIFIED", "comment": "Attempted generated draft promotion."},
    )
    assert verification.status_code == 409
    executed = client.post(f"/v1/workflow-runs/{run['id']}/execute", headers=headers(a))
    assert executed.status_code == 200, executed.text
    run = executed.json()
    assert run["state"] == "BLOCKED"
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/approve",
            headers=headers(a, "APPROVER"),
            json=decision(run),
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/v1/workflow-runs/{run['id']}/artifacts", headers=headers(identities[1])
        ).status_code
        == 404
    )
    with pytest.raises(DBAPIError):
        with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("UPDATE source_snapshots SET is_fixture=false WHERE id=:id"),
                {"id": run["source_snapshot_id"]},
            )


@pytest.mark.parametrize(
    "change",
    [
        {"is_fixture": False},
        {"classification": "PRIMARY"},
        {"source_type": "MANUAL", "is_fixture": False},
        {"source_type": "MANUAL", "origin": "internal:renamed", "is_fixture": False},
        {"source_type": "MANUAL", "metadata": {}, "is_fixture": False},
    ],
)
def test_api_rejects_generated_flag_and_marker_tampering(client, identities, change):
    a = identities[0]
    source = generated_source(preview(client, a)) | change
    result = client.post(
        "/v1/workflow-runs", headers=headers(a), json=request_data(a) | {"source": source}
    )
    assert result.status_code == 422


@pytest.mark.parametrize(
    "marker",
    [
        {"source_type": "GENERATED"},
        {"origin": "generated:studio-source-draft-v1:reserved"},
        {"origin": " GENERATED:reserved "},
        {"metadata": {"source_draft_policy": "studio-source-draft-v1"}},
    ],
)
@pytest.mark.parametrize(
    "unsafe", [{"is_fixture": False}, {"is_fixture": True, "classification": "PRIMARY"}]
)
def test_database_rejects_generated_source_promotion_without_python_validation(
    identities, marker, unsafe
):
    a = identities[0]
    with pytest.raises(DBAPIError) as caught:
        with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
            versions = repo.all("influencer_versions", influencer_id=UUID(a["influencer_id"]))
            run = repo.insert(
                "workflow_runs",
                influencer_id=UUID(a["influencer_id"]),
                influencer_version_id=max(versions, key=lambda v: v["version"])["id"],
                mission_id=UUID(a["mission_id"]),
                created_by=repo.principal_id,
                idempotency_key=str(uuid4()),
                canonical_input_hash="a" * 64,
                correlation_id=uuid4(),
            )
            raw = "A draft prompt."
            data = (
                {
                    "source_type": "MANUAL",
                    "origin": "internal:notes",
                    "title": "Draft",
                    "publisher": "Editor",
                    "raw_content": raw,
                    "captured_at": datetime.now(UTC),
                    "classification": "INTERNAL",
                    "is_fixture": True,
                    "metadata": {},
                    "evidence_input": [{"start": 0, "end": len(raw), "statement": raw}],
                    "checksum": hashlib.sha256(raw.encode()).hexdigest(),
                    "environment": "test",
                    "created_by": repo.principal_id,
                    "workflow_run_id": run["id"],
                }
                | marker
                | unsafe
            )
            repo.insert("source_snapshots", **data)
    assert caught.value.orig.diag.constraint_name == "generated_source_is_nonpublishable"
