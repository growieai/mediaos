import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import REPO_ROOT, get_settings
from app.db.repository import engine, metadata
from app.models.schemas import CharacterConfig
from app.seed import seed


@pytest.fixture(scope="session", autouse=True)
def database():
    load_dotenv(REPO_ROOT / ".env")
    admin_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    runtime_url = os.environ["TEST_DATABASE_URL"]
    # Destructive reset is permitted only for the explicitly configured disposable test DB.
    assert make_url(admin_url).database == "mediaos_test"
    assert make_url(runtime_url).database == "mediaos_test"
    admin = create_engine(admin_url, hide_parameters=True)
    with admin.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS private CASCADE"))
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    previous = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["MIGRATION_DATABASE_URL"] = admin_url
    os.environ["DATABASE_URL"] = runtime_url
    command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
    if previous:
        os.environ["MIGRATION_DATABASE_URL"] = previous
    get_settings.cache_clear()
    engine.cache_clear()
    metadata.cache_clear()
    yield admin
    engine().dispose()
    admin.dispose()


@pytest.fixture(scope="session")
def identities(database):
    data = json.loads((REPO_ROOT / "characters/sofia/runtime.json").read_text(encoding="utf-8-sig"))
    data.update(
        language="en",
        audience=["Local teams"],
        franchise="Verified notes",
        objective="Explain verified information",
        persona="A virtual business assistant",
        voice="Plain language",
        visual_policy="No graphics",
        brand_policy="No product promotion",
        disclosure="This is an AI creator.",
        headline="For your reference",
        cta="Read the source.",
        creative_allowlist=["For your reference", "Read the source."],
    )
    cfg = CharacterConfig.model_validate(data)
    result = []
    for slug in ("test-a", "test-b"):
        tokens = {role: secrets.token_urlsafe(32) for role in ("OPERATOR", "APPROVER", "ADMIN")}
        ids = seed(database, slug, slug, f"Influencer {slug}", "Business growth", cfg, tokens)
        result.append({**ids, "tokens": tokens})
    return result


@pytest.fixture
def client(identities):
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def headers(identity, role="OPERATOR"):
    return {
        "Authorization": "Bearer " + identity["tokens"][role],
        "X-Tenant-ID": identity["tenant_id"],
    }


def request_data(identity, **overrides):
    statement = "The office opens at 09:00 on weekdays."
    source = {
        "source_type": "MANUAL",
        "origin": "internal:office-handbook",
        "title": "Office hours",
        "publisher": "Operations",
        "raw_content": statement,
        "captured_at": datetime.now(UTC).isoformat(),
        "classification": "INTERNAL",
        "is_fixture": False,
        "evidence": [{"start": 0, "end": len(statement), "statement": statement}],
    }
    source.update(overrides)
    return {
        "influencer_id": identity["influencer_id"],
        "mission_id": identity["mission_id"],
        "idempotency_key": str(uuid4()),
        "source": source,
    }


def create(client, identity, verify=True, execute=True, payload=None):
    result = client.post(
        "/v1/workflow-runs", headers=headers(identity), json=payload or request_data(identity)
    )
    assert result.status_code == 200, result.text
    run = result.json()
    if verify:
        response = client.post(
            f"/v1/source-snapshots/{run['source_snapshot_id']}/verify",
            headers=headers(identity, "APPROVER"),
            json={
                "decision": "VERIFIED",
                "comment": "Compared exact spans with original supplied document.",
            },
        )
        assert response.status_code == 200, response.text
    if execute:
        response = client.post(f"/v1/workflow-runs/{run['id']}/execute", headers=headers(identity))
        assert response.status_code == 200, response.text
        run = response.json()
    return run


def decision(run):
    return {
        "asset_version_id": run["asset_version_id"],
        "research_version_id": run["research_version_id"],
        "qa_report_id": run["qa_report_id"],
        "comment": "Reviewed exact evidence and content.",
    }


def artifact_data(client, identity, run):
    response = client.get(f"/v1/workflow-runs/{run['id']}/artifacts", headers=headers(identity))
    assert response.status_code == 200
    return response.json()


def revise(client, identity, run, payload):
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/revisions", headers=headers(identity), json=payload
    )
    assert response.status_code == 200, response.text
    response = client.post(f"/v1/workflow-runs/{run['id']}/execute", headers=headers(identity))
    assert response.status_code == 200, response.text
    return response.json()
