"""Saved synthetic observations of an approved historical render; no platform calls."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.rendering.acceptance import main, require


def exercise(client, credentials: dict, report: dict) -> None:
    headers = {
        "Authorization": "Bearer " + credentials["tokens"]["OPERATOR"],
        "X-Tenant-ID": credentials["tenant_id"],
    }

    def call(method, path, body=None, expected=200, context=None):
        response = client.request(method, "/v1/" + path, headers=context or headers, json=body)
        require(
            response.status_code == expected,
            f"Metrics {method} {path}: HTTP {response.status_code}",
        )
        return response.json()

    # The visual harness has revised this workflow already. Historical approvals
    # still establish lineage but can no longer authorize a current export.
    workflow_id = report["workflow_run_id"]
    before = call("GET", f"workflow-runs/{workflow_id}")
    request = {
        "schema_version": 1,
        "idempotency_key": str(uuid4()),
        "render_run_id": report["initial_render_id"],
        "mode": "FIXTURE",
        "platform_label": "Synthetic acceptance fixture",
        "external_reference": None,
        "provenance_note": "Synthetic schema example, not a real post or audience.",
    }
    subject = call("POST", f"workflow-runs/{workflow_id}/metric-subjects", request)
    require(subject["mode"] == "FIXTURE", "Fixture subject was relabelled")
    require(
        call("POST", f"workflow-runs/{workflow_id}/metric-subjects", request)["id"]
        == subject["id"],
        "Subject idempotency failed",
    )
    call(
        "POST",
        f"workflow-runs/{workflow_id}/metric-subjects",
        {**request, "provenance_note": "Changed payload"},
        expected=409,
    )
    now = datetime.now(UTC)
    first_input = {
        "schema_version": 1,
        "idempotency_key": str(uuid4()),
        "observed_at": (now - timedelta(hours=2)).isoformat(),
        "scope": "LIFETIME_CUMULATIVE",
        "reach": 100,
        "saves": None,
        "shares": 0,
        "comments": 2,
        "follows": None,
        "evidence_text": "SYNTHETIC values: reach=100, saves=unknown, shares=0, comments=2, follows=unknown.",
        "definition_notes": "Synthetic cumulative counters for acceptance only; not platform observations.",
    }
    second_input = {
        **first_input,
        "idempotency_key": str(uuid4()),
        "observed_at": (now - timedelta(hours=1)).isoformat(),
        "reach": 140,
        "saves": 5,
        "shares": 2,
        "comments": 1,
        "evidence_text": "SYNTHETIC values: reach=140, saves=5, shares=2, comments=1, follows=unknown.",
    }
    path = f"metric-subjects/{subject['id']}"
    first = call("POST", path + "/snapshots", first_input)
    second = call("POST", path + "/snapshots", second_input)
    require(
        call("POST", path + "/snapshots", first_input)["id"] == first["id"],
        "Snapshot idempotency failed",
    )
    call(
        "POST",
        path + "/snapshots",
        {k: v for k, v in second_input.items() if k != "follows"},
        expected=422,
    )
    call("POST", path + "/snapshots", {**second_input, "mode": "MANUAL"}, expected=422)
    learning_input = {
        "idempotency_key": str(uuid4()),
        "baseline_snapshot_id": first["id"],
        "current_snapshot_id": second["id"],
    }
    learning = call("POST", path + "/learning-reports", learning_input)
    payload = learning["payload"]
    require(
        payload["mode"] == "FIXTURE"
        and payload["causal_claim"] is False
        and payload["policy_updated"] is False,
        "Learning exceeded descriptive fixture scope",
    )
    values = payload["metrics"]
    require(values["reach"]["delta"] == 40, "Known counter difference changed")
    require(
        values["saves"]["delta"] is None and values["follows"]["delta"] is None,
        "Unknown counters became zeros",
    )
    require(
        values["shares"]["baseline"] == 0 and values["shares"]["delta"] == 2,
        "Explicit zero was lost",
    )
    require(
        values["comments"]["delta"] == -1 and values["comments"]["direction"] == "DECREASED",
        "Reported decrease was hidden",
    )
    require(
        call("POST", path + "/learning-reports", learning_input)["id"] == learning["id"],
        "Learning idempotency failed",
    )
    call("GET", path, context={**headers, "X-Tenant-ID": str(uuid4())}, expected=401)
    detail = call("GET", path)
    require(
        len(detail["snapshots"]) == 2 and len(detail["learning_reports"]) == 1,
        "Repeated requests duplicated metrics",
    )
    require(call("GET", f"workflow-runs/{workflow_id}") == before, "Metrics changed the workflow")
    artifacts = call("GET", f"workflow-runs/{workflow_id}/artifacts")
    attempts = [
        row for row in artifacts["skill_runs"] if row["skill_identifier"] == "learning.describe"
    ]
    require(
        len(attempts) == 1
        and attempts[0]["status"] == "SUCCEEDED"
        and attempts[0]["provider"] == "deterministic"
        and float(attempts[0]["cost"]) == 0,
        "Learning telemetry missing or misclassified",
    )
    report["metrics"] = {
        "mode": "FIXTURE",
        "subject_id": subject["id"],
        "baseline_snapshot_id": first["id"],
        "current_snapshot_id": second["id"],
        "learning_report_id": learning["id"],
        "learning_skill_run_id": attempts[0]["id"],
        "audit_count": len(call("GET", f"workflow-runs/{workflow_id}/audit")),
        "unknown_and_zero_preserved": True,
        "reported_decrease_preserved": True,
        "historical_approval_lineage": True,
        "idempotency_passed": True,
        "causal_claim": False,
        "policy_updated": False,
        "platform_connected": False,
    }


if __name__ == "__main__":
    main(delivery=True, after_exercise=exercise, report_prefix="metrics")
