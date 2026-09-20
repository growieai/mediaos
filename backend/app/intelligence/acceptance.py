"""Opt-in LIVE official-source acceptance, with isolated controlled negative cases.

Requires the standalone seed and admin URL only to create a separate verification tenant.
Approval API calls simulate a human reviewer; this never publishes content.
"""

import copy
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import REPO_ROOT
from app.db.repository import transaction
from app.intelligence.connectors import BDNSConnector, BOEConnector, bdns_reference
from app.intelligence.editorial import evaluate
from app.intelligence.http import FetchResult
from app.intelligence.ingestion import (
    IngestionRunner,
    create_ingestion,
    persist_document,
    service_token,
    update_run,
)
from app.intelligence.schemas import DiscoveryRequest, DocumentRef
from app.intelligence.seed import seed_intelligence
from app.main import app
from app.models.schemas import CreateRun
from app.seed import seed
from app.services.workflows import Runner, config_for, create_run, source_input


def headers(identity, role="OPERATOR"):
    return {
        "X-Tenant-ID": identity["tenant_id"],
        "Authorization": "Bearer " + identity["tokens"][role],
    }


def approval_payload(run):
    return {
        key: str(run[key]) for key in ("asset_version_id", "research_version_id", "qa_report_id")
    } | {
        "comment": "Acceptance simulation: reviewed exact source, research, QA and asset revisions."
    }


def ingest_live(identity, key):
    tenant = UUID(identity["tenant_id"])
    with transaction(tenant, identity["tokens"]["OPERATOR"]) as repo:
        source = repo.one("source_definitions", source_key="BDNS")
    today = datetime.now(UTC).date()
    request = DiscoveryRequest(
        source_definition_id=source["id"],
        idempotency_key=key,
        since=today - timedelta(days=20),
        until=today,
        query="PYME",
        page_size=5,
    )
    run = create_ingestion(tenant, identity["tokens"]["OPERATOR"], request, uuid4())
    return IngestionRunner(tenant, identity.get("ingestor") or service_token(tenant)).execute(
        run["id"]
    )


def select_and_draft(client, identity, key):
    tenant = UUID(identity["tenant_id"])
    with transaction(tenant, identity["tokens"]["OPERATOR"]) as repo:
        audience = repo.one("audience_segments", code="GENERIC_SMB")
        existing = repo.all("workflow_runs", idempotency_key=key)
        if existing:
            binding = repo.one("workflow_opportunities", workflow_run_id=existing[0]["id"])
            selected = repo.one("opportunities", id=binding["opportunity_id"])
        else:
            selected = None
            for opportunity in repo.all("opportunities"):
                results = evaluate(repo, opportunity, UUID(identity["mission_id"]))
                if any(
                    r["audience_segment"]["id"] == audience["id"]
                    and r["decision"]["decision"] == "CREATE_CONTENT"
                    for r in results
                ):
                    selected = opportunity
                    break
        if selected is None:
            raise RuntimeError(
                "No current official opportunity qualifies; acceptance cannot be claimed"
            )
    payload = {
        "influencer_id": identity["influencer_id"],
        "mission_id": identity["mission_id"],
        "audience_segment_id": str(audience["id"]),
        "idempotency_key": key,
    }
    url = f"/v1/intelligence/opportunities/{selected['id']}/workflow"
    result = client.post(url, headers=headers(identity), json=payload)
    assert result.status_code == 200, result.text
    return result.json(), url, payload


def clone_live_workflow(identity, run, key):
    tenant = UUID(identity["tenant_id"])
    token = identity["tokens"]["OPERATOR"]
    with transaction(tenant, token) as repo:
        original = repo.one("workflow_runs", id=run["id"])
        binding = repo.one("workflow_opportunities", workflow_run_id=run["id"])
        source = source_input(repo.one("source_snapshots", id=original["source_snapshot_id"]))
    request = CreateRun(
        influencer_id=original["influencer_id"],
        mission_id=original["mission_id"],
        idempotency_key=key,
        source=source,
    )

    def bind(repo, created):
        if repo.all("workflow_opportunities", workflow_run_id=created["id"]):
            return
        repo.connection.execute(
            text("SELECT bind_opportunity_run(:r,:v,:s,:e,:a,CAST(:c AS jsonb))"),
            {
                "r": created["id"],
                "v": binding["opportunity_version_id"],
                "s": binding["source_snapshot_id"],
                "e": binding["editorial_decision_id"],
                "a": binding["audience_segment_id"],
                "c": json.dumps(binding["research_context"], default=str),
            },
        )

    created = create_run(tenant, token, request, uuid4(), after_create=bind)
    return Runner(tenant, token).execute(created["id"])


def controlled_document(identity, source_key, reference, body):
    """Explicit test fixture in the verification tenant; never attested as live evidence."""
    tenant = UUID(identity["tenant_id"])
    with transaction(tenant, identity["tokens"]["OPERATOR"]) as repo:
        source = repo.one("source_definitions", source_key=source_key)
    today = datetime.now(UTC).date()
    request = DiscoveryRequest(
        source_definition_id=source["id"], idempotency_key=str(uuid4()), since=today, until=today
    )
    run = create_ingestion(tenant, identity["tokens"]["OPERATOR"], request, uuid4())
    response = FetchResult(
        url=reference.url,
        body=body,
        media_type="application/json" if source_key == "BDNS" else "application/xml",
        captured_at=datetime.now(UTC),
        checksum=hashlib.sha256(body.encode()).hexdigest(),
        is_fixture=True,
    )
    connector = BDNSConnector() if source_key == "BDNS" else BOEConnector()
    normalized = connector.normalize(response, reference)
    with transaction(tenant, identity["ingestor"]) as repo:
        update_run(repo, run["id"], status="RUNNING", started_at=datetime.now(UTC))
        result = persist_document(repo, run, source, response, reference, normalized)
        update_run(
            repo,
            run["id"],
            status="SUCCEEDED",
            ended_at=datetime.now(UTC),
            counters={"controlled_fixture": True, "documents_fetched": 1},
        )
    return result


def main():
    load_dotenv(REPO_ROOT / ".env")
    identity = json.loads((REPO_ROOT / ".local/credentials.json").read_text(encoding="utf-8"))
    tenant = UUID(identity["tenant_id"])
    suffix = str(uuid4())
    ingestion = ingest_live(identity, "acceptance-live:" + suffix)
    with TestClient(app) as client:
        run, url, payload = select_and_draft(client, identity, "m2-live-acceptance")
        assert run["state"] in ("AWAITING_APPROVAL", "APPROVED")
        artifacts = client.get(
            f"/v1/workflow-runs/{run['id']}/artifacts", headers=headers(identity)
        ).json()
        assert artifacts["qa_reports"][-1]["status"] == "PASS"
        assert all(s["provider"] == "deterministic" for s in artifacts["skill_runs"])
        assert (
            client.post(
                f"/v1/workflow-runs/{run['id']}/approve",
                headers=headers(identity),
                json=approval_payload(run),
            ).status_code
            == 403
        )
        if run["state"] == "AWAITING_APPROVAL":
            approved = client.post(
                f"/v1/workflow-runs/{run['id']}/approve",
                headers=headers(identity, "APPROVER"),
                json=approval_payload(run),
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["workflow"]["state"] == "APPROVED"
        replay = client.post(url, headers=headers(identity), json=payload)
        assert replay.status_code == 200 and replay.json()["id"] == run["id"]
        audit = client.get(f"/v1/workflow-runs/{run['id']}/audit", headers=headers(identity)).json()
        with transaction(tenant, identity["tokens"]["OPERATOR"]) as repo:
            stored = repo.one("workflow_runs", id=run["id"])
            config = config_for(repo, stored)
            bound = repo.one("workflow_opportunities", workflow_run_id=run["id"])
            normalized = repo.one("source_snapshots", id=bound["source_snapshot_id"])
            raw_snapshot = repo.one("source_snapshots", id=normalized["parent_snapshot_id"])
            raw = repo.one("raw_source_documents", id=normalized["raw_document_id"])
            assert raw_snapshot["raw_content"] == raw["body"] and not raw_snapshot["is_fixture"]
        admin = create_engine(
            os.environ["MIGRATION_DATABASE_URL"],
            hide_parameters=True,
            connect_args={"connect_timeout": 15},
        )
        tokens = {role: secrets.token_urlsafe(32) for role in ("OPERATOR", "APPROVER", "ADMIN")}
        ids = seed(
            admin,
            "m2-verification-" + suffix,
            "M2 isolated verification",
            "Verification creator",
            "Business opportunity verification",
            config,
            tokens,
        )
        controls = {**ids, "tokens": tokens, "ingestor": secrets.token_urlsafe(32)}
        seed_intelligence(admin, ids["tenant_id"], ids["mission_id"], controls["ingestor"])
        admin.dispose()
        cross = client.get(f"/v1/workflow-runs/{run['id']}", headers=headers(controls))
        assert cross.status_code == 404

        ingest_live(controls, "control-live:" + suffix)
        control_run, _, _ = select_and_draft(client, controls, "control-draft:" + suffix)
        assert control_run["state"] == "AWAITING_APPROVAL"
        unsupported = clone_live_workflow(controls, control_run, "unsupported:" + suffix)
        unsupported_artifacts = client.get(
            f"/v1/workflow-runs/{unsupported['id']}/artifacts", headers=headers(controls)
        ).json()
        draft = copy.deepcopy(unsupported_artifacts["content_asset_versions"][-1]["payload"])
        draft["slides"][0]["body"]["text"] = (
            "Esta ayuda garantiza 100.000 euros a todos los negocios."
        )
        revised = client.post(
            f"/v1/workflow-runs/{unsupported['id']}/revisions",
            headers=headers(controls),
            json=draft,
        )
        assert revised.status_code == 200, revised.text
        blocked = client.post(
            f"/v1/workflow-runs/{unsupported['id']}/execute", headers=headers(controls)
        ).json()
        assert blocked["state"] == "BLOCKED"
        assert (
            client.post(
                f"/v1/workflow-runs/{blocked['id']}/approve",
                headers=headers(controls, "APPROVER"),
                json=approval_payload(blocked),
            ).status_code
            == 409
        )

        control_tenant = UUID(controls["tenant_id"])
        with transaction(control_tenant, controls["tokens"]["OPERATOR"]) as repo:
            binding = repo.one("workflow_opportunities", workflow_run_id=control_run["id"])
            snapshot = repo.one("source_snapshots", id=binding["source_snapshot_id"])
            original = repo.one("raw_source_documents", id=snapshot["raw_document_id"])
            original_json = json.loads(original["body"])
        code = original_json["codigoBDNS"]
        original_json["fechaFinSolicitud"] = (
            datetime.now(UTC).date() + timedelta(days=200)
        ).isoformat()
        changed = controlled_document(
            controls,
            "BDNS",
            DocumentRef(external_id=code, url=original["url"]),
            json.dumps(original_json, ensure_ascii=False),
        )
        assert changed["outcome"] == "changed_opportunities"
        stale = client.post(
            f"/v1/workflow-runs/{control_run['id']}/approve",
            headers=headers(controls, "APPROVER"),
            json=approval_payload(control_run),
        )
        assert stale.status_code == 409

        fixture_path = REPO_ROOT / "backend/tests/fixtures/official/boe-notice.xml"
        xml = fixture_path.read_text(encoding="utf-8")
        original_code = bdns_reference(xml)
        assert original_code
        xml = xml.replace(original_code, code).replace(
            "</texto>",
            f"<p>Fin de solicitud: {(datetime.now(UTC).date() + timedelta(days=250)).isoformat()}</p></texto>",
        )
        conflict = controlled_document(
            controls,
            "BOE",
            DocumentRef(
                external_id="BOE-B-2026-30165",
                url="https://www.boe.es/diario_boe/xml.php?id=BOE-B-2026-30165",
            ),
            xml,
        )
        assert conflict["conflicts"] > 0
        current_artifacts = client.get(
            f"/v1/workflow-runs/{control_run['id']}/artifacts", headers=headers(controls)
        ).json()
        assert (
            client.post(
                f"/v1/workflow-runs/{control_run['id']}/revisions",
                headers=headers(controls),
                json=current_artifacts["content_asset_versions"][-1]["payload"],
            ).status_code
            == 200
        )
        conflict_run = client.post(
            f"/v1/workflow-runs/{control_run['id']}/execute", headers=headers(controls)
        ).json()
        assert conflict_run["state"] == "BLOCKED"
        report_artifacts = client.get(
            f"/v1/workflow-runs/{control_run['id']}/artifacts", headers=headers(controls)
        ).json()
        codes = {f["code"] for f in report_artifacts["qa_reports"][-1]["payload"]["findings"]}
        assert "OFFICIAL_SOURCE_CONFLICT" in codes and "STALE_OPPORTUNITY" in codes
        assert (
            client.post(
                f"/v1/workflow-runs/{control_run['id']}/approve",
                headers=headers(controls, "APPROVER"),
                json=approval_payload(conflict_run),
            ).status_code
            == 409
        )
        with transaction(control_tenant, controls["tokens"]["OPERATOR"]) as repo:
            change_events = repo.all("change_events", opportunity_id=binding["opportunity_id"])
        assert any(e["event_type"] == "DEADLINE_CHANGED" for e in change_events)
        report = {
            "executed_at": datetime.now(UTC).isoformat(),
            "live_source_verified": True,
            "live_ingestion_run_id": str(ingestion["id"]),
            "ingestion_counters": ingestion["counters"],
            "tenant_id": str(tenant),
            "workflow_run_id": run["id"],
            "opportunity_id": str(bound["opportunity_id"]),
            "opportunity_version_id": str(bound["opportunity_version_id"]),
            "official_url": raw["url"],
            "raw_sha256": raw["checksum"],
            "raw_snapshot_id": str(raw_snapshot["id"]),
            "normalized_snapshot_id": str(normalized["id"]),
            "research_version_id": run["research_version_id"],
            "asset_version_id": run["asset_version_id"],
            "qa_report_id": run["qa_report_id"],
            "qa": "PASS",
            "final_state": "APPROVED",
            "approval_mode": "Dedicated approver API simulation; not a claim of an actual human review",
            "audit_event_count": len(audit),
            "idempotency_replay": True,
            "cross_tenant_status": cross.status_code,
            "controls_tenant_id": controls["tenant_id"],
            "unsupported_workflow_id": str(blocked["id"]),
            "unsupported_state": "BLOCKED",
            "stale_approval_status": stale.status_code,
            "conflict_workflow_id": control_run["id"],
            "conflict_state": conflict_run["state"],
            "conflict_findings": sorted(codes),
            "controlled_changes_are_fixtures": True,
            "material_change_events": [e["event_type"] for e in change_events],
        }
        path = REPO_ROOT / ".local/milestone-2-acceptance.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
