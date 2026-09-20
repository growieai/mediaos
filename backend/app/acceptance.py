"""Exercise separate operator/approver API requests against the isolated local seed."""

import copy
import json
import os
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.config import REPO_ROOT
from app.main import app


def main():
    credentials = json.loads((REPO_ROOT / ".local/credentials.json").read_text(encoding="utf-8"))
    identity = credentials["tenant_id"]
    operator = {
        "Authorization": "Bearer " + credentials["tokens"]["OPERATOR"],
        "X-Tenant-ID": identity,
    }
    approver = {
        "Authorization": "Bearer " + credentials["tokens"]["APPROVER"],
        "X-Tenant-ID": identity,
    }
    # This is a real internal policy statement, sourced from the repository security document.
    statement = "An OPERATOR cannot approve content."
    raw = (REPO_ROOT / "docs/SECURITY.md").read_text(encoding="utf-8")
    start = raw.index(statement)
    request = {
        "influencer_id": credentials["influencer_id"],
        "mission_id": credentials["mission_id"],
        "idempotency_key": str(uuid4()),
        "source": {
            "source_type": "MANUAL",
            "origin": "repository:docs/SECURITY.md",
            "title": "Internal approval policy",
            "publisher": "Media OS engineering",
            "raw_content": raw,
            "captured_at": datetime.now(UTC).isoformat(),
            "classification": "INTERNAL",
            "is_fixture": False,
            "evidence": [{"start": start, "end": start + len(statement), "statement": statement}],
        },
    }
    events = []
    target = os.environ.get("ACCEPTANCE_API_URL")
    if target:
        parts = urlsplit(target)
        if (
            parts.scheme != "http"
            or parts.hostname not in ("127.0.0.1", "localhost")
            or parts.username
            or parts.password
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Acceptance credentials may only be sent to a loopback HTTP API")
    connection = (
        httpx.Client(base_url=target, timeout=60, trust_env=False) if target else TestClient(app)
    )
    with connection as client:

        def call(method, path, role=operator, body=None, expected=200):
            response = client.request(method, "/v1/" + path, headers=role, json=body)
            assert response.status_code == expected, (path, response.status_code, response.text)
            return response.json()

        run = call("POST", "workflow-runs", body=request)
        events.append(
            {
                "event": "SOURCE_CAPTURED",
                "workflow_run_id": run["id"],
                "source_snapshot_id": run["source_snapshot_id"],
            }
        )
        call(
            "POST",
            f"source-snapshots/{run['source_snapshot_id']}/verify",
            approver,
            {
                "decision": "VERIFIED",
                "comment": "Acceptance harness attests the exact repository policy span.",
            },
        )
        run = call("POST", f"workflow-runs/{run['id']}/execute")
        assert run["state"] == "AWAITING_APPROVAL"
        data = call("GET", f"workflow-runs/{run['id']}/artifacts")
        assert len(data["skill_runs"]) == 5 and data["qa_reports"][0]["status"] == "PASS"
        events.append({"event": "QA_PASS_AWAITING_APPROVAL", "run": run})

        def decision(r):
            return {k: r[k] for k in ("asset_version_id", "research_version_id", "qa_report_id")}

        approved = call("POST", f"workflow-runs/{run['id']}/approve", approver, decision(run))
        assert approved["workflow"]["state"] == "APPROVED"
        events.append(
            {
                "event": "APPROVED_BY_APPROVER_API",
                "approval_record_id": approved["approval_record_id"],
            }
        )
        duplicate = call("POST", "workflow-runs", body=request)
        assert duplicate["id"] == run["id"]
        events.append({"event": "IDEMPOTENT_REPLAY", "same_run": True})
        denied = call(
            "GET",
            f"workflow-runs/{run['id']}",
            {**operator, "X-Tenant-ID": str(uuid4())},
            expected=401,
        )
        events.append({"event": "CROSS_TENANT_REJECTED", "response": denied})

        draft = data["content_asset_versions"][0]["payload"]
        changed = call("POST", f"workflow-runs/{run['id']}/revisions", body=draft)
        assert changed["qa_report_id"] is None and changed["state"] == "CONTENT_COMPLETE"
        call("POST", f"workflow-runs/{run['id']}/approve", approver, decision(run), expected=409)
        fresh = call("POST", f"workflow-runs/{run['id']}/execute")
        assert (
            fresh["qa_report_id"] != run["qa_report_id"] and fresh["state"] == "AWAITING_APPROVAL"
        )
        events.append({"event": "STALE_APPROVAL_REJECTED_FRESH_QA_REQUIRED", "run": fresh})
        call("POST", f"workflow-runs/{run['id']}/approve", approver, decision(fresh))

        bad_request = copy.deepcopy(request)
        bad_request["idempotency_key"] = str(uuid4())
        negative = call("POST", "workflow-runs", body=bad_request)
        call(
            "POST",
            f"source-snapshots/{negative['source_snapshot_id']}/verify",
            approver,
            {"decision": "VERIFIED", "comment": "Exact repository policy span reviewed."},
        )
        negative = call("POST", f"workflow-runs/{negative['id']}/execute")
        bad = copy.deepcopy(draft)
        bad["slides"][0]["body"]["text"] = "Every business is guaranteed free money."
        # Use this run's evidence ID, keeping the unsupported text detectable by QA.
        negative_data = call("GET", f"workflow-runs/{negative['id']}/artifacts")
        bad["slides"][0]["body"]["fact_ids"] = negative_data["content_asset_versions"][0][
            "payload"
        ]["slides"][0]["body"]["fact_ids"]
        call("POST", f"workflow-runs/{negative['id']}/revisions", body=bad)
        blocked = call("POST", f"workflow-runs/{negative['id']}/execute")
        assert blocked["state"] == "BLOCKED"
        call(
            "POST",
            f"workflow-runs/{negative['id']}/approve",
            approver,
            decision(blocked),
            expected=409,
        )
        events.append({"event": "UNSUPPORTED_CLAIM_BLOCKED_APPROVAL_REJECTED", "run": blocked})
        events.append(
            {"event": "AUDIT", "entries": call("GET", f"workflow-runs/{run['id']}/audit")}
        )
    output = REPO_ROOT / ".local/acceptance-report.json"
    output.write_text(json.dumps(events, indent=2))
    print(
        "Acceptance passed. Persisted operator/approver API simulation; report: .local/acceptance-report.json"
    )


if __name__ == "__main__":
    main()
