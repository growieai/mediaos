"""Persist a local visual acceptance flow; no database reset or public publishing.

Approver requests simulate review using the separate seeded APPROVER identity.
They are not a person's editorial sign-off or approval of Sofía's final identity.
"""

import copy
import hashlib
import io
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app.config import REPO_ROOT
from app.db.repository import canonical_hash
from app.main import app
from app.models.schemas import CreateRun
from app.rendering.schemas import RenderManifest


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verified_zip(content: bytes, rendered: dict, directory: Path) -> dict:
    """Check named entries without extracting caller-controlled archive paths."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = RenderManifest.model_validate_json(archive.read("manifest.json"), strict=True)
        payload = manifest.model_dump(mode="json")
        require(manifest.status == "PASS", "Export does not contain a passing manifest")
        require(
            canonical_hash(payload) == rendered["manifest_hash"],
            "Export manifest differs from the exact approved manifest",
        )
        require(payload == rendered["manifest"], "Export manifest content changed")
        filenames = [f"slide-{index:02d}.png" for index in range(1, len(manifest.slides) + 1)]
        require(
            sorted(archive.namelist()) == sorted([*filenames, "manifest.json", "caption.txt"]),
            "Export contains unexpected or duplicate files",
        )
        require(
            archive.read("caption.txt").decode("utf-8") == manifest.caption.text,
            "Export caption differs from the manifest",
        )
        slide_checks = []
        for index, slide in enumerate(manifest.slides, 1):
            filename = filenames[index - 1]
            require(slide.index == index and slide.filename == filename, "Invalid slide order")
            image_bytes = archive.read(filename)
            digest = hashlib.sha256(image_bytes).hexdigest()
            require(digest == slide.sha256, "Export image differs from its reviewed checksum")
            with Image.open(io.BytesIO(image_bytes)) as picture:
                require(
                    picture.format == "PNG" and picture.size == (1080, 1350),
                    "Export image format or dimensions are invalid",
                )
                picture.load()
            if index == 1:
                (directory / "slide-01.png").write_bytes(image_bytes)
            slide_checks.append({"filename": filename, "sha256": digest, "size": [1080, 1350]})
    (directory / "approved-carousel.zip").write_bytes(content)
    return {"zip_sha256": hashlib.sha256(content).hexdigest(), "slides": slide_checks}


def exercise(client, credentials: dict, report: dict, directory: Path) -> None:
    operator = {
        "Authorization": "Bearer " + credentials["tokens"]["OPERATOR"],
        "X-Tenant-ID": credentials["tenant_id"],
    }
    approver = {
        "Authorization": "Bearer " + credentials["tokens"]["APPROVER"],
        "X-Tenant-ID": credentials["tenant_id"],
    }

    def request(method, path, role=operator, body=None, expected=200):
        response = client.request(method, "/v1/" + path, headers=role, json=body)
        require(
            response.status_code == expected,
            f"{method} {path}: expected HTTP {expected}, received {response.status_code}",
        )
        return response

    def call(method, path, role=operator, body=None, expected=200):
        return request(method, path, role, body, expected).json()

    def decision(run):
        return {
            key: run[key] for key in ("asset_version_id", "research_version_id", "qa_report_id")
        }

    # The evidence is a real policy document. It is not a fabricated grant or a
    # relabelled recorded fixture. Its use is limited to this internal acceptance.
    statement = "An OPERATOR cannot approve content."
    raw = (REPO_ROOT / "docs/SECURITY.md").read_text(encoding="utf-8")
    start = raw.index(statement)
    submission = {
        "influencer_id": credentials["influencer_id"],
        "mission_id": credentials["mission_id"],
        "idempotency_key": str(uuid4()),
        "source": {
            "source_type": "MANUAL",
            "origin": "repository:docs/SECURITY.md",
            "title": "Internal approval policy — visual acceptance",
            "publisher": "Media OS engineering",
            "raw_content": raw,
            "captured_at": datetime.now(UTC).isoformat(),
            "classification": "INTERNAL",
            "is_fixture": False,
            "metadata": {"purpose": "INTERNAL_VISUAL_ACCEPTANCE", "public_publishing": "false"},
            "evidence": [{"start": start, "end": start + len(statement), "statement": statement}],
        },
    }
    # Validate locally as well, so field-level schema errors are visible before an
    # API call whose deliberately redacted 422 response omits the original input.
    CreateRun.model_validate_json(json.dumps(submission), strict=True)
    run = call("POST", "workflow-runs", body=submission)
    report["workflow_run_id"] = run["id"]
    report["source_snapshot_id"] = run["source_snapshot_id"]
    call(
        "POST",
        f"source-snapshots/{run['source_snapshot_id']}/verify",
        approver,
        {
            "decision": "VERIFIED",
            "comment": "Acceptance simulation reviews the exact repository policy span.",
        },
    )
    run = call("POST", f"workflow-runs/{run['id']}/execute")
    require(run["state"] == "AWAITING_APPROVAL", "Current content must await human approval")
    data = call("GET", f"workflow-runs/{run['id']}/artifacts")
    draft = next(
        row["payload"]
        for row in data["content_asset_versions"]
        if row["id"] == run["asset_version_id"]
    )
    require(
        any(
            row["id"] == run["qa_report_id"] and row["status"] == "PASS"
            for row in data["qa_reports"]
        ),
        "Current content QA did not pass",
    )
    catalog = call("GET", f"workflow-runs/{run['id']}/renders")
    require(bool(catalog["configurations"]), "Seed the influencer's visual configuration first")
    config = max(catalog["configurations"], key=lambda row: row["version"])
    render_request = {
        "asset_version_id": run["asset_version_id"],
        "visual_config_version_id": config["id"],
        "idempotency_key": str(uuid4()),
    }
    rendered = call("POST", f"workflow-runs/{run['id']}/renders", body=render_request)
    require(rendered["status"] == "PASS", "Visual QA did not pass")
    require(rendered["manifest"]["findings"] == [], "Passing render has visual findings")
    require(
        rendered["asset_version_id"] == run["asset_version_id"], "Wrong content revision rendered"
    )
    require(
        rendered["manifest"]["reference_sha256"] == config["reference_sha256"],
        "Render does not use the pinned character reference",
    )
    report["visual_config_version_id"] = config["id"]
    report["reference_sha256"] = config["reference_sha256"]
    report["reference_status"] = config["reference_metadata"].get("status", "UNSPECIFIED")
    report["initial_render_id"] = rendered["id"]
    report["initial_manifest_hash"] = rendered["manifest_hash"]
    report["initial_artifacts"] = decision(run)
    preview = request("GET", f"renders/{rendered['id']}/slides/1")
    require(
        hashlib.sha256(preview.content).hexdigest() == rendered["manifest"]["slides"][0]["sha256"],
        "Authenticated preview differs from the stored render",
    )
    visual_decision = {
        "manifest_hash": rendered["manifest_hash"],
        "comment": "Automated acceptance simulation, not a person's visual sign-off.",
    }
    call("POST", f"renders/{rendered['id']}/approve", approver, visual_decision, expected=409)
    call("GET", f"renders/{rendered['id']}/export", expected=409)
    call("POST", f"workflow-runs/{run['id']}/approve", operator, decision(run), expected=403)
    approved = call("POST", f"workflow-runs/{run['id']}/approve", approver, decision(run))
    require(approved["workflow"]["state"] == "APPROVED", "Content approval did not persist")
    call("POST", f"renders/{rendered['id']}/approve", operator, visual_decision, expected=403)
    visually_approved = call("POST", f"renders/{rendered['id']}/approve", approver, visual_decision)
    require(
        len(visually_approved["render"]["approvals"]) == 1
        and visually_approved["render"]["approvals"][0]["content_approval_record_id"]
        == approved["approval_record_id"],
        "Visual approval does not point to the exact content approval",
    )
    report["content_approval_record_id"] = approved["approval_record_id"]
    report["visual_approval_record_id"] = visually_approved["visual_approval_record_id"]
    report["export"] = verified_zip(
        request("GET", f"renders/{rendered['id']}/export").content, rendered, directory
    )
    repeated = call("POST", f"workflow-runs/{run['id']}/renders", body=render_request)
    require(repeated["id"] == rendered["id"], "Same render key created a duplicate")
    require(
        call("POST", "workflow-runs", body=submission)["id"] == run["id"],
        "Same source request created a duplicate workflow",
    )
    foreign_context = {**operator, "X-Tenant-ID": str(uuid4())}
    call("GET", f"renders/{rendered['id']}", foreign_context, expected=401)
    call("GET", f"renders/{rendered['id']}/export", foreign_context, expected=401)
    report["checks"] = {
        "visual_approval_before_content_approval_rejected": True,
        "operator_content_approval_rejected": True,
        "operator_visual_approval_rejected": True,
        "export_before_approvals_rejected": True,
        "workflow_idempotency": True,
        "render_idempotency": True,
        "unauthorized_tenant_context_rejected": True,
    }

    # A real, safe text change uses an existing allowed creative template. The
    # historical approval and ZIP remain evidence of an earlier revision only.
    revised_draft = copy.deepcopy(draft)
    revised_draft["caption"] = copy.deepcopy(draft["cta"])
    require(revised_draft != draft, "Acceptance needs a changed content payload")
    changed = call("POST", f"workflow-runs/{run['id']}/revisions", body=revised_draft)
    require(changed["qa_report_id"] is None, "Revision did not invalidate old QA")
    call("GET", f"renders/{rendered['id']}/export", expected=409)
    call("POST", f"workflow-runs/{run['id']}/approve", approver, decision(run), expected=409)
    fresh = call("POST", f"workflow-runs/{run['id']}/execute")
    require(
        fresh["state"] == "AWAITING_APPROVAL" and fresh["qa_report_id"] != run["qa_report_id"],
        "Changed content did not require fresh QA and approval",
    )
    fresh_render = call(
        "POST",
        f"workflow-runs/{run['id']}/renders",
        body={
            **render_request,
            "asset_version_id": fresh["asset_version_id"],
            "idempotency_key": str(uuid4()),
        },
    )
    require(fresh_render["status"] == "PASS", "Fresh current revision did not render successfully")
    require(fresh_render["id"] != rendered["id"], "Fresh content reused an old render")
    call("GET", f"renders/{fresh_render['id']}/export", expected=409)
    report["checks"]["revision_invalidates_old_export"] = True
    report["checks"]["fresh_render_requires_new_approvals"] = True
    report["user_review"] = {
        "workflow_run_id": fresh["id"],
        "render_run_id": fresh_render["id"],
        "state": fresh["state"],
        "visual_status": fresh_render["status"],
        "manifest_hash": fresh_render["manifest_hash"],
        **decision(fresh),
        "approvals_required": ["CONTENT", "VISUAL"],
    }

    negative_submission = copy.deepcopy(submission)
    negative_submission["idempotency_key"] = str(uuid4())
    negative = call("POST", "workflow-runs", body=negative_submission)
    call(
        "POST",
        f"source-snapshots/{negative['source_snapshot_id']}/verify",
        approver,
        {
            "decision": "VERIFIED",
            "comment": "Acceptance simulation reviews the same repository policy.",
        },
    )
    negative = call("POST", f"workflow-runs/{negative['id']}/execute")
    negative_data = call("GET", f"workflow-runs/{negative['id']}/artifacts")
    unsupported = copy.deepcopy(
        next(
            row["payload"]
            for row in negative_data["content_asset_versions"]
            if row["id"] == negative["asset_version_id"]
        )
    )
    unsupported["slides"][0]["body"]["text"] = "An OPERATOR may approve every content revision."
    call("POST", f"workflow-runs/{negative['id']}/revisions", body=unsupported)
    blocked = call("POST", f"workflow-runs/{negative['id']}/execute")
    require(blocked["state"] == "BLOCKED", "Unsupported claim was not blocked")
    call(
        "POST", f"workflow-runs/{blocked['id']}/approve", approver, decision(blocked), expected=409
    )
    call(
        "POST",
        f"workflow-runs/{blocked['id']}/renders",
        body={
            **render_request,
            "asset_version_id": blocked["asset_version_id"],
            "idempotency_key": str(uuid4()),
        },
        expected=409,
    )
    report["blocked_workflow_run_id"] = blocked["id"]
    report["checks"]["unsupported_claim_blocks_content_approval_and_render"] = True
    audit = call("GET", f"workflow-runs/{run['id']}/audit")
    event_types = sorted({row["event_type"] for row in audit})
    for required_event in (
        "RENDER_CREATED",
        "RENDER_COMPLETED",
        "VISUAL_APPROVED",
        "RENDER_EXPORT_AUTHORIZED",
    ):
        require(required_event in event_types, f"Missing persisted audit event: {required_event}")
    report["audit"] = {"count": len(audit), "event_types": event_types}
    final_artifacts = call("GET", f"workflow-runs/{run['id']}/artifacts")
    render_skills = [
        row for row in final_artifacts["skill_runs"] if row["skill_identifier"] == "visual.render"
    ]
    require(len(render_skills) == 2, "Visual render attempts were not persisted exactly once")
    require(
        all(
            row["status"] == "SUCCEEDED" and row["provider"] == "deterministic"
            for row in render_skills
        ),
        "Unexpected rendering provider or attempt status",
    )
    report["render_skill_run_ids"] = [row["id"] for row in render_skills]
    render_costs = [
        row
        for row in final_artifacts["cost_events"]
        if row["skill_run_id"] in report["render_skill_run_ids"]
    ]
    require(
        len(render_costs) == 2
        and all(
            row["provider"] == "deterministic"
            and float(row["cost"]) == 0
            and row["input_tokens"] == 0
            and row["output_tokens"] == 0
            for row in render_costs
        ),
        "Deterministic render cost records are missing or inconsistent",
    )
    report["render_cost_events"] = {
        "count": len(render_costs),
        "provider": "deterministic",
        "tokens": 0,
        "cost_usd": 0,
    }


def main() -> None:
    target = os.environ.get("ACCEPTANCE_API_URL")
    via_console = os.environ.get("ACCEPTANCE_VIA_CONSOLE") == "true"
    if via_console and not target:
        raise ValueError("Console acceptance requires an explicit loopback URL")
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
    credentials = json.loads((REPO_ROOT / ".local/credentials.json").read_text(encoding="utf-8"))
    directory = REPO_ROOT / ".local/visual-acceptance"
    directory.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "status": "RUNNING",
        "started_at": datetime.now(UTC).isoformat(),
        "tenant_id": credentials["tenant_id"],
        "execution": "CONSOLE_PROXY_HTTP"
        if via_console
        else "LOOPBACK_HTTP"
        if target
        else "IN_PROCESS_API",
        "approver_mode": "SIMULATED_API_CALLS_USING_SEPARATE_APPROVER_IDENTITY",
        "public_publishing": False,
        "database_reset": False,
        "source": "repository:docs/SECURITY.md",
    }

    def through_console(request: httpx.Request):
        require(request.url.path.startswith("/v1/"), "Unexpected acceptance request path")
        request.url = request.url.copy_with(path="/api/internal/" + request.url.path[4:])

    connection = (
        httpx.Client(
            base_url=target,
            timeout=120,
            trust_env=False,
            event_hooks={"request": [through_console]} if via_console else {},
        )
        if target
        else TestClient(app)
    )
    try:
        with connection as client:
            exercise(client, credentials, report, directory)
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAILED"
        report["error_category"] = type(exc).__name__
        raise
    finally:
        report["completed_at"] = datetime.now(UTC).isoformat()
        (REPO_ROOT / ".local/visual-acceptance-report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(
        "Visual acceptance passed. Approvals were simulated; nothing was published. "
        "Report: .local/visual-acceptance-report.json. A fresh render awaits your own review."
    )


if __name__ == "__main__":
    main()
