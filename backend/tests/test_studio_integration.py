import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from conftest import artifact_data, create, headers, request_data
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.repository import transaction
from app.models.schemas import CharacterConfig
from app.rendering.schemas import VisualConfig
from app.studio.schemas import CreateInfluencer, StudioInfluencer
from app.studio.service import create_influencer


@pytest.fixture(autouse=True)
def enable_studio(monkeypatch):
    monkeypatch.setenv("ENABLE_EXTERNAL_CREATORS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def studio_payload(**changes):
    return {
        "idempotency_key": str(uuid4()),
        "name": "Alex",
        "category_id": "education",
        "language": "en",
        "tone": "CLEAR",
        "audience": ["Curious learners"],
        "objective": "Explain sourced ideas clearly.",
    } | changes


def register(client, identity, payload=None):
    response = client.post(
        "/v1/studio/influencers", headers=headers(identity), json=payload or studio_payload()
    )
    assert response.status_code == 200, response.text
    StudioInfluencer.model_validate_json(response.text)
    return response.json()


def test_catalog_and_authenticated_tenant_context(client, identities):
    assert client.get("/v1/studio/categories").status_code == 401
    response = client.get("/v1/studio/categories", headers=headers(identities[0]))
    assert response.status_code == 200
    assert len(response.json()["categories"]) == 8
    h = headers(identities[0]) | {"X-Tenant-ID": identities[1]["tenant_id"]}
    assert client.get("/v1/studio/influencers", headers=h).status_code == 401


def test_staged_maintenance_identity_does_not_break_studio_catalog(client, identities, database):
    identity = identities[0]
    pending = uuid4()
    with database.begin() as connection:
        connection.execute(
            text("INSERT INTO influencers(id,tenant_id,slug,name) VALUES(:id,:tenant,:slug,:name)"),
            {
                "id": pending,
                "tenant": identity["tenant_id"],
                "slug": str(pending),
                "name": "Staged identity",
            },
        )
    response = client.get("/v1/studio/influencers", headers=headers(identity))
    assert response.status_code == 200, response.text
    listed = response.json()["influencers"]
    assert str(pending) not in {item["id"] for item in listed}
    detail = client.get(f"/v1/studio/influencers/{pending}", headers=headers(identity))
    assert detail.status_code == 409
    overview = client.get("/v1/studio/overview", headers=headers(identity))
    assert overview.status_code == 200, overview.text
    assert overview.json()["counts"]["influencers"] == len(listed)


def test_creation_persists_typed_generic_config_and_audit_receipt(client, identities):
    identity = identities[0]
    data = studio_payload(name="Luz", category_id="food", language="es", tone="WARM")
    item = register(client, identity, data)
    assert item["category_id"] == "food" and item["language"] == "es"
    assert item["opportunity_discovery"] is False and item["portrait_available"] is False
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        config = repo.one("character_config_versions", id=UUID(item["character_config_version_id"]))
        typed = CharacterConfig.model_validate(config["payload"])
        assert typed.disclosure and typed.brand_association_level == 0
        assert "Sofía" not in json.dumps(config["payload"], ensure_ascii=False)
        visual = repo.one("visual_config_versions", id=UUID(item["visual_config_version_id"]))
        assert (
            VisualConfig.model_validate(visual["payload"]).template_version == "social-editorial-v2"
        )
        assert visual["reference_path"] is None
        audit = repo.one("studio_creations", influencer_id=UUID(item["id"]))
        assert audit["created_by"] == repo.principal_id and audit["correlation_id"]
        assert audit["payload"] == {
            key: value for key, value in data.items() if key != "idempotency_key"
        }


def test_flag_and_approver_role_cannot_create(client, identities, monkeypatch):
    identity = identities[0]
    assert (
        client.post(
            "/v1/studio/influencers", headers=headers(identity, "APPROVER"), json=studio_payload()
        ).status_code
        == 403
    )
    monkeypatch.setenv("ENABLE_EXTERNAL_CREATORS", "false")
    get_settings.cache_clear()
    assert (
        client.post(
            "/v1/studio/influencers", headers=headers(identity), json=studio_payload()
        ).status_code
        == 409
    )
    assert (
        client.get("/v1/studio/influencers", headers=headers(identity)).json()["creation_enabled"]
        is False
    )


def test_idempotency_replay_conflict_and_tenant_namespace(client, identities):
    data = studio_payload()
    first = register(client, identities[0], data)
    assert register(client, identities[0], data)["id"] == first["id"]
    changed = client.post(
        "/v1/studio/influencers", headers=headers(identities[0]), json=data | {"name": "Other"}
    )
    assert changed.status_code == 409
    other = register(client, identities[1], data)
    assert first["id"] != other["id"]


def test_concurrent_registration_has_one_persisted_identity(identities):
    identity = identities[0]
    data = CreateInfluencer.model_validate(studio_payload())

    def submit(_):
        return create_influencer(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], data, uuid4()
        ).id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(submit, range(4)))
    assert len(set(ids)) == 1
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert len(repo.all("studio_creations", idempotency_key=data.idempotency_key)) == 1


def test_cross_tenant_reads_mutation_and_reference_rejected(client, identities):
    a, b = identities
    item = register(client, b)
    assert client.get(f"/v1/studio/influencers/{item['id']}", headers=headers(a)).status_code == 404
    assert (
        client.get(f"/v1/studio/influencers/{item['id']}/portrait", headers=headers(a)).status_code
        == 404
    )
    listing = client.get("/v1/studio/influencers", headers=headers(a)).json()
    assert item["id"] not in {row["id"] for row in listing["influencers"]}
    payload = request_data(a) | {"influencer_id": item["id"], "mission_id": item["mission_id"]}
    assert client.post("/v1/workflow-runs", headers=headers(a), json=payload).status_code == 404
    with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
        assert repo.all("studio_creations", influencer_id=UUID(item["id"])) == []
    for table in ("influencers", "studio_creations", "visual_config_versions"):
        with pytest.raises(DBAPIError):
            with transaction(UUID(a["tenant_id"]), a["tokens"]["OPERATOR"]) as repo:
                repo.connection.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": b["tenant_id"]}
                )


def test_direct_database_creation_rejects_approver_and_untyped_payload(identities):
    identity = identities[0]
    data = studio_payload()
    key = data.pop("idempotency_key")
    for role, payload in (
        ("APPROVER", data),
        ("OPERATOR", data | {"creative_allowlist": ["claim"]}),
    ):
        with pytest.raises(DBAPIError):
            with transaction(UUID(identity["tenant_id"]), identity["tokens"][role]) as repo:
                repo.connection.execute(
                    text(
                        "SELECT studio_create_influencer(:key,CAST(:payload AS jsonb),:correlation)"
                    ),
                    {"key": key, "payload": json.dumps(payload), "correlation": uuid4()},
                )


def test_creator_uses_existing_sourced_workflow_and_response_contract(
    client, identities, monkeypatch, tmp_path
):
    identity = identities[0]
    monkeypatch.setattr(get_settings(), "asset_storage_path", tmp_path / "studio-renders")
    item = register(client, identity)
    payload = request_data(identity) | {
        "influencer_id": item["id"],
        "mission_id": item["mission_id"],
    }
    run = create(client, identity, payload=payload)
    assert run["state"] == "AWAITING_APPROVAL"
    assert run["influencer_id"] == item["id"] and run["mission_id"] == item["mission_id"]
    artifacts = artifact_data(client, identity, run)
    assert (
        artifacts["content_asset_versions"][0]["payload"]["disclosure"]
        == "AI-generated virtual creator."
    )
    overview = client.get("/v1/studio/overview", headers=headers(identity))
    assert overview.status_code == 200, overview.text
    saved = next(row for row in overview.json()["workflows"] if row["id"] == run["id"])
    assert saved["title"] == "Office hours" and saved["influencer_id"] == item["id"]
    assert overview.json()["counts"]["awaiting_approval"] > 0
    rendered = client.post(
        f"/v1/workflow-runs/{run['id']}/renders",
        headers=headers(identity),
        json={
            "asset_version_id": run["asset_version_id"],
            "visual_config_version_id": item["visual_config_version_id"],
            "idempotency_key": str(uuid4()),
        },
    )
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["status"] == "PASS", rendered.text
    preview = client.get(f"/v1/renders/{rendered.json()['id']}/slides/1", headers=headers(identity))
    assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")


def test_readiness_auth_tenant_scope_and_current_qa(client, identities):
    run = create(client, identities[0])
    path = f"/v1/studio/workflow-runs/{run['id']}/readiness"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers(identities[1])).status_code == 404
    response = client.get(path, headers=headers(identities[0]))
    assert response.status_code == 200, response.text
    assert response.json()["current_ids"]["asset_version_id"] == run["asset_version_id"]
    assert response.json()["current_ids"]["qa_report_id"] == run["qa_report_id"]
    assert (
        client.get(f"/v1/workflow-runs/{run['id']}", headers=headers(identities[0])).json()["state"]
        == "AWAITING_APPROVAL"
    )
