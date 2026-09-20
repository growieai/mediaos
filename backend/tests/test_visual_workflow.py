"""PostgreSQL and API contracts for exact-revision visual approval and export."""

import copy
import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from conftest import artifact_data, create, decision, headers, request_data, revise
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.repository import canonical_hash, transaction
from app.rendering import seed as visual_seed
from app.rendering import service
from app.rendering.seed import seed_visual_config
from app.rendering.service import RenderInput, create_render, execute_render


@pytest.fixture
def visual_context(database, identities, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "asset_storage_path", tmp_path / "private-renders")
    configs = [
        seed_visual_config(
            database,
            identity["tenant_id"],
            identity["influencer_id"],
            display_name=f"Influencer test-{'a' if index == 0 else 'b'}",
            disclosure="This is an AI creator.",
        )
        for index, identity in enumerate(identities)
    ]
    return {
        "a": identities[0],
        "b": identities[1],
        "configs": configs,
        "storage": tmp_path / "private-renders",
    }


def render_request(run, config_id, key=None):
    return {
        "asset_version_id": run["asset_version_id"],
        "visual_config_version_id": str(config_id),
        "idempotency_key": key or str(uuid4()),
    }


def render(client, identity, run, config_id, key=None):
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/renders",
        headers=headers(identity),
        json=render_request(run, config_id, key),
    )
    assert response.status_code == 200, response.text
    return response.json()


def approve_content(client, identity, run):
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/approve",
        headers=headers(identity, "APPROVER"),
        json=decision(run),
    )
    assert response.status_code == 200, response.text
    return response.json()


def visual_decision(rendered):
    return {
        "manifest_hash": rendered["manifest_hash"],
        "comment": "Reviewed exact PNGs and source content.",
    }


def approve_visual(client, identity, rendered):
    response = client.post(
        f"/v1/renders/{rendered['id']}/approve",
        headers=headers(identity, "APPROVER"),
        json=visual_decision(rendered),
    )
    assert response.status_code == 200, response.text
    return response.json()


def export(client, identity, rendered):
    return client.get(f"/v1/renders/{rendered['id']}/export", headers=headers(identity))


def start_only(identity, run, config_id, key=None):
    """Persist the same start boundary without executing, as a stopped worker would."""
    payload = render_request(run, config_id, key)
    digest = canonical_hash(
        {
            "workflow_run_id": run["id"],
            "asset_version_id": run["asset_version_id"],
            "visual_config_version_id": str(config_id),
            "renderer_version": "pillow-editorial-v1",
        }
    )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        render_id = repo.connection.execute(
            text("SELECT start_render(:run,:asset,:config,:key,:hash)"),
            {
                "run": UUID(run["id"]),
                "asset": UUID(run["asset_version_id"]),
                "config": config_id,
                "key": payload["idempotency_key"],
                "hash": digest,
            },
        ).scalar_one()
    return render_id


@pytest.fixture
def passing_render(client, visual_context):
    identity = visual_context["a"]
    run = create(client, identity)
    rendered = render(client, identity, run, visual_context["configs"][0])
    assert rendered["status"] == "PASS", rendered
    return identity, run, rendered


def test_visual_closed_loop_exports_exact_approved_images_and_metadata(client, passing_render):
    identity, run, rendered = passing_render
    assert rendered["asset_version_id"] == run["asset_version_id"]
    assert rendered["research_version_id"] == run["research_version_id"]
    assert rendered["qa_report_id"] == run["qa_report_id"]
    assert export(client, identity, rendered).status_code == 409
    approval = client.post(
        f"/v1/renders/{rendered['id']}/approve",
        headers=headers(identity, "APPROVER"),
        json=visual_decision(rendered),
    )
    assert approval.status_code == 409, "Visual approval must wait for exact content approval"
    approve_content(client, identity, run)
    assert export(client, identity, rendered).status_code == 409
    visual = approve_visual(client, identity, rendered)
    assert visual["visual_approval_record_id"]
    response = export(client, identity, rendered)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"slide-01.png", "manifest.json", "caption.txt"}
        manifest = json.loads(archive.read("manifest.json"))
        assert canonical_hash(manifest) == rendered["manifest_hash"]
        assert archive.read("caption.txt").decode("utf-8") == manifest["caption"]["text"]
        for slide in manifest["slides"]:
            data = archive.read(slide["filename"])
            assert hashlib.sha256(data).hexdigest() == slide["sha256"]
            with Image.open(io.BytesIO(data)) as image:
                assert image.size == (1080, 1350)
    audit = client.get(f"/v1/workflow-runs/{run['id']}/audit", headers=headers(identity)).json()
    events = audit if isinstance(audit, list) else audit["audit_events"]
    assert {row["event_type"] for row in events} >= {
        "RENDER_CREATED",
        "RENDER_STARTED",
        "RENDER_COMPLETED",
        "VISUAL_APPROVED",
        "RENDER_EXPORT_AUTHORIZED",
    }
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        attempts = repo.all(
            "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="visual.render"
        )
        costs = repo.all("cost_events", skill_run_id=attempts[0]["id"])
    assert len(attempts) == 1 and attempts[0]["status"] == "SUCCEEDED"
    assert attempts[0]["provider"] == "deterministic" and not attempts[0]["is_mock"]
    assert costs and all(row["cost"] == 0 for row in costs)


def test_visual_actions_require_identity_and_approver_role(client, passing_render):
    identity, run, rendered = passing_render
    for path in (
        f"/v1/renders/{rendered['id']}",
        f"/v1/renders/{rendered['id']}/slides/1",
        f"/v1/renders/{rendered['id']}/export",
    ):
        assert client.get(path).status_code == 401
    approve_content(client, identity, run)
    for action in ("approve", "reject"):
        response = client.post(
            f"/v1/renders/{rendered['id']}/{action}",
            headers=headers(identity),
            json=visual_decision(rendered),
        )
        assert response.status_code == 403
    wrong_hash = {**visual_decision(rendered), "manifest_hash": "0" * 64}
    assert (
        client.post(
            f"/v1/renders/{rendered['id']}/approve",
            headers=headers(identity, "APPROVER"),
            json=wrong_hash,
        ).status_code
        == 409
    )


def test_rejected_visual_is_immutable_and_cannot_export(client, passing_render):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    rejected = client.post(
        f"/v1/renders/{rendered['id']}/reject",
        headers=headers(identity, "APPROVER"),
        json=visual_decision(rendered),
    )
    assert rejected.status_code == 200
    assert export(client, identity, rendered).status_code == 409
    assert (
        client.post(
            f"/v1/renders/{rendered['id']}/approve",
            headers=headers(identity, "APPROVER"),
            json=visual_decision(rendered),
        ).status_code
        == 409
    )
    stored = client.get(f"/v1/renders/{rendered['id']}", headers=headers(identity)).json()
    assert len(stored["approvals"]) == 1 and stored["approvals"][0]["decision"] == "REJECT"


def test_cross_tenant_render_retrieval_references_mutation_and_approval_are_rejected(
    client, passing_render, visual_context
):
    owner, run, rendered = passing_render
    outsider = visual_context["b"]
    for suffix in ("", "/slides/1", "/export"):
        assert (
            client.get(
                f"/v1/renders/{rendered['id']}{suffix}", headers=headers(outsider)
            ).status_code
            == 404
        )
    assert (
        client.get(f"/v1/workflow-runs/{run['id']}/renders", headers=headers(outsider)).status_code
        == 404
    )
    assert (
        client.post(f"/v1/renders/{rendered['id']}/execute", headers=headers(outsider)).status_code
        == 404
    )
    for action in ("approve", "reject"):
        assert (
            client.post(
                f"/v1/renders/{rendered['id']}/{action}",
                headers=headers(outsider, "APPROVER"),
                json=visual_decision(rendered),
            ).status_code
            == 404
        )
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/renders",
            headers=headers(outsider),
            json=render_request(run, visual_context["configs"][1]),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/workflow-runs/{run['id']}/renders",
            headers=headers(owner),
            json=render_request(run, visual_context["configs"][1]),
        ).status_code
        == 409
    )
    own_run = create(client, outsider)
    foreign_asset = {
        **render_request(own_run, visual_context["configs"][1]),
        "asset_version_id": run["asset_version_id"],
    }
    assert (
        client.post(
            f"/v1/workflow-runs/{own_run['id']}/renders",
            headers=headers(outsider),
            json=foreign_asset,
        ).status_code
        == 409
    )
    with transaction(UUID(outsider["tenant_id"]), outsider["tokens"]["OPERATOR"]) as repo:
        assert (
            repo.connection.execute(
                text("SELECT * FROM render_runs WHERE id=:id"), {"id": UUID(rendered["id"])}
            ).all()
            == []
        )
        assert (
            repo.connection.execute(
                text("SELECT * FROM visual_config_versions WHERE id=:id"),
                {"id": visual_context["configs"][0]},
            ).all()
            == []
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE render_runs SET status='PASS' WHERE id=:id",
        "DELETE FROM render_runs WHERE id=:id",
        "UPDATE visual_config_versions SET payload='{}'::jsonb WHERE tenant_id=:tenant",
        "DELETE FROM visual_approval_records WHERE render_run_id=:id",
        "INSERT INTO render_runs OVERRIDING SYSTEM VALUE SELECT * FROM render_runs WHERE id=:id",
        "INSERT INTO visual_approval_records SELECT * FROM visual_approval_records WHERE render_run_id=:id",
    ],
)
def test_runtime_cannot_write_protected_visual_tables(client, passing_render, statement):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    approve_visual(client, identity, rendered)
    with pytest.raises(DBAPIError) as rejected:
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text(statement), {"id": UUID(rendered["id"]), "tenant": UUID(identity["tenant_id"])}
            )
    assert getattr(rejected.value.orig, "sqlstate", None) == "42501"


@pytest.mark.parametrize(
    "corruption",
    [
        "text",
        "draft_hash",
        "manifest_hash",
        "missing_slide",
        "truncated_line",
        "missing_disclosure",
        "false_pass",
    ],
)
def test_database_rejects_fabricated_or_incomplete_manifest(
    client, passing_render, visual_context, corruption
):
    identity, run, rendered = passing_render
    render_id = start_only(identity, run, visual_context["configs"][0])
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        repo.connection.execute(text("SELECT claim_render(:id)"), {"id": render_id})
    manifest = copy.deepcopy(rendered["manifest"])
    if corruption == "text":
        manifest["slides"][0]["text_coverage"][2]["text"] = "Unsupported promise of free money."
    elif corruption == "draft_hash":
        manifest["draft_sha256"] = "0" * 64
    elif corruption == "missing_slide":
        manifest["slides"] = []
    elif corruption == "truncated_line":
        manifest["slides"][0]["text_coverage"][2]["lines"] = []
    elif corruption == "missing_disclosure":
        manifest["slides"][0]["text_coverage"][-1]["text"] = ""
    elif corruption == "false_pass":
        manifest["findings"] = [
            {
                "code": "BLOCK",
                "severity": "BLOCKED",
                "field_path": "body",
                "message": "Missing evidence",
                "slide_index": 1,
            }
        ]
    digest = "0" * 64 if corruption == "manifest_hash" else canonical_hash(manifest)
    with pytest.raises(DBAPIError) as rejected:
        with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
            repo.connection.execute(
                text("SELECT complete_render(:id,CAST(:manifest AS jsonb),:hash)"),
                {
                    "id": render_id,
                    "manifest": json.dumps(manifest, ensure_ascii=False),
                    "hash": digest,
                },
            )
    assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
    stored = client.get(f"/v1/renders/{render_id}", headers=headers(identity)).json()
    assert stored["status"] == "RENDERING" and stored["manifest"] is None


def test_render_idempotency_same_payload_conflict_and_tenant_scope(
    client, passing_render, visual_context
):
    identity, run, first = passing_render
    same = render(client, identity, run, visual_context["configs"][0], first["idempotency_key"])
    assert same["id"] == first["id"] and same["manifest_hash"] == first["manifest_hash"]
    other_run = create(client, identity)
    response = client.post(
        f"/v1/workflow-runs/{other_run['id']}/renders",
        headers=headers(identity),
        json=render_request(other_run, visual_context["configs"][0], first["idempotency_key"]),
    )
    assert response.status_code == 409
    other = visual_context["b"]
    other_run = create(client, other)
    independent = render(
        client, other, other_run, visual_context["configs"][1], first["idempotency_key"]
    )
    assert independent["id"] != first["id"]
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert (
            len(
                repo.all(
                    "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="visual.render"
                )
            )
            == 1
        )


def test_new_content_and_qa_invalidate_old_visual_approval_and_export(client, passing_render):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    approve_visual(client, identity, rendered)
    draft = artifact_data(client, identity, run)["content_asset_versions"][0]["payload"]
    newer = revise(client, identity, run, draft)
    assert newer["asset_version_id"] != run["asset_version_id"]
    assert newer["qa_report_id"] != run["qa_report_id"]
    assert export(client, identity, rendered).status_code == 409
    approve_content(client, identity, newer)
    assert export(client, identity, rendered).status_code == 409
    old = client.get(f"/v1/renders/{rendered['id']}", headers=headers(identity)).json()
    assert len(old["approvals"]) == 1, "Historical approval must remain immutable"


def test_new_visual_configuration_invalidates_old_export(
    client, passing_render, database, visual_context
):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    approve_visual(client, identity, rendered)
    newer = seed_visual_config(
        database,
        identity["tenant_id"],
        identity["influencer_id"],
        display_name="Updated visual identity",
        disclosure="This is an AI creator.",
    )
    assert newer != visual_context["configs"][0]
    assert export(client, identity, rendered).status_code == 409


def test_new_render_requires_new_visual_approval(client, passing_render, visual_context):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    approve_visual(client, identity, rendered)
    newer = render(client, identity, run, visual_context["configs"][0])
    assert newer["id"] != rendered["id"]
    assert export(client, identity, rendered).status_code == 409
    assert export(client, identity, newer).status_code == 409
    approve_visual(client, identity, newer)
    assert export(client, identity, newer).status_code == 200


def test_overflow_preserves_content_but_cannot_be_approved_or_exported(client, visual_context):
    identity = visual_context["a"]
    statement = "Every application requires the original supporting documentation. " * 27
    payload = request_data(
        identity,
        raw_content=statement,
        evidence=[{"start": 0, "end": len(statement), "statement": statement.rstrip(" ")}],
    )
    # The normalized statement permits trimming ASCII spaces at the evidence edge.
    run = create(client, identity, payload=payload)
    rendered = render(client, identity, run, visual_context["configs"][0])
    assert rendered["status"] == "REVISION_REQUIRED"
    assert rendered["manifest"]["slides"][0]["overflow"]
    assert not list(
        (visual_context["storage"] / identity["tenant_id"] / rendered["id"]).rglob("*.png")
    )
    approve_content(client, identity, run)
    assert (
        client.post(
            f"/v1/renders/{rendered['id']}/approve",
            headers=headers(identity, "APPROVER"),
            json=visual_decision(rendered),
        ).status_code
        == 409
    )
    assert export(client, identity, rendered).status_code == 409


def test_blocked_content_cannot_start_a_render(client, visual_context):
    identity = visual_context["a"]
    run = create(client, identity, verify=False)
    assert run["state"] == "BLOCKED"
    response = client.post(
        f"/v1/workflow-runs/{run['id']}/renders",
        headers=headers(identity),
        json=render_request(run, visual_context["configs"][0]),
    )
    assert response.status_code == 409


def test_explicit_source_newlines_round_trip_through_database_visual_guard(client, visual_context):
    identity = visual_context["a"]
    statement = "The first office opens at 09:00.\nThe second office opens at 10:00."
    payload = request_data(
        identity,
        raw_content=statement,
        evidence=[{"start": 0, "end": len(statement), "statement": statement}],
    )
    run = create(client, identity, payload=payload)
    rendered = render(client, identity, run, visual_context["configs"][0])
    assert rendered["status"] == "PASS"
    body = rendered["manifest"]["slides"][0]["text_coverage"][2]
    assert body["text"] == statement and any(line["hard_break"] for line in body["lines"])
    approve_content(client, identity, run)
    approve_visual(client, identity, rendered)
    assert export(client, identity, rendered).status_code == 200


def test_created_render_resumes_once_without_duplicate_attempts(client, visual_context):
    identity = visual_context["a"]
    run = create(client, identity)
    render_id = start_only(identity, run, visual_context["configs"][0])
    assert (
        client.get(f"/v1/renders/{render_id}", headers=headers(identity)).json()["status"]
        == "CREATED"
    )
    first = client.post(f"/v1/renders/{render_id}/execute", headers=headers(identity))
    assert first.status_code == 200 and first.json()["status"] == "PASS", first.text
    second = client.post(f"/v1/renders/{render_id}/execute", headers=headers(identity))
    assert (
        second.status_code == 200
        and second.json()["manifest_hash"] == first.json()["manifest_hash"]
    )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        assert (
            len(
                repo.all(
                    "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="visual.render"
                )
            )
            == 1
        )


@pytest.mark.parametrize("after_files", [False, True])
def test_rendering_resumes_after_interruption_without_duplicate_committed_files(
    client, visual_context, monkeypatch, after_files
):
    identity = visual_context["a"]
    run = create(client, identity)
    request = RenderInput.model_validate_json(
        json.dumps(render_request(run, visual_context["configs"][0]))
    )
    original_render, original_validate = service.render_carousel, service.validate_files

    def interrupted(*args, **kwargs):
        raise SystemExit("Simulated stopped process")

    def interrupt_after_rename(directory, manifest):
        result = original_validate(directory, manifest)
        if directory.name == "files":
            raise SystemExit("Simulated stop between filesystem and database checkpoints")
        return result

    if after_files:
        monkeypatch.setattr(service, "validate_files", interrupt_after_rename)
    else:
        monkeypatch.setattr(service, "render_carousel", interrupted)
    with pytest.raises(SystemExit):
        create_render(
            UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], UUID(run["id"]), request
        )
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        stored = repo.one("render_runs", workflow_run_id=UUID(run["id"]))
        assert stored["status"] == "RENDERING"
    monkeypatch.setattr(service, "render_carousel", original_render)
    monkeypatch.setattr(service, "validate_files", original_validate)
    resumed = execute_render(
        UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"], stored["id"]
    )
    assert resumed["status"] == "PASS"
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        attempts = repo.all(
            "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="visual.render"
        )
    assert [row["status"] for row in attempts] == ["INTERRUPTED", "SUCCEEDED"]
    assert [row["attempt"] for row in attempts] == [1, 2]
    root = visual_context["storage"] / identity["tenant_id"] / str(stored["id"])
    assert len(list(root.rglob("slide-01.png"))) == 1
    assert len(list(root.rglob("manifest.json"))) == 1


def test_tampered_png_is_rejected_during_preview_and_export(client, passing_render, visual_context):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    approve_visual(client, identity, rendered)
    image = (
        visual_context["storage"]
        / identity["tenant_id"]
        / rendered["id"]
        / "files"
        / "slide-01.png"
    )
    image.write_bytes(image.read_bytes() + b"tampered")
    assert (
        client.get(f"/v1/renders/{rendered['id']}/slides/1", headers=headers(identity)).status_code
        == 409
    )
    assert export(client, identity, rendered).status_code == 409


def test_tampered_png_before_visual_approval_rolls_back_decision(
    client, passing_render, visual_context
):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    image = (
        visual_context["storage"]
        / identity["tenant_id"]
        / rendered["id"]
        / "files"
        / "slide-01.png"
    )
    original = image.read_bytes()
    image.write_bytes(original + b"changed before review")
    response = client.post(
        f"/v1/renders/{rendered['id']}/approve",
        headers=headers(identity, "APPROVER"),
        json=visual_decision(rendered),
    )
    assert response.status_code == 409
    stored = client.get(f"/v1/renders/{rendered['id']}", headers=headers(identity)).json()
    assert stored["approvals"] == [], "A failed byte check must not commit an approval"
    audit = client.get(f"/v1/workflow-runs/{run['id']}/audit", headers=headers(identity)).json()
    assert not any(row["event_type"] == "VISUAL_APPROVED" for row in audit)
    assert export(client, identity, rendered).status_code == 409
    image.write_bytes(original)
    approve_visual(client, identity, rendered)
    assert export(client, identity, rendered).status_code == 200


def test_render_that_becomes_stale_before_claim_has_a_persisted_failure(client, visual_context):
    identity = visual_context["a"]
    run = create(client, identity)
    render_id = start_only(identity, run, visual_context["configs"][0])
    draft = artifact_data(client, identity, run)["content_asset_versions"][0]["payload"]
    newer = revise(client, identity, run, draft)
    assert newer["asset_version_id"] != run["asset_version_id"]
    response = client.post(f"/v1/renders/{render_id}/execute", headers=headers(identity))
    assert response.status_code == 409
    stored = client.get(f"/v1/renders/{render_id}", headers=headers(identity)).json()
    assert stored["status"] == "FAILED"
    assert stored["ended_at"] and stored["error_category"]
    assert stored["manifest"] is None and stored["manifest_hash"] is None
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        attempts = repo.all(
            "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="visual.render"
        )
    assert attempts == [], "No rendering skill was invoked for stale input"
    audit = client.get(f"/v1/workflow-runs/{run['id']}/audit", headers=headers(identity)).json()
    assert any(row["event_type"] == "RENDER_FAILED" for row in audit)
    assert export(client, identity, stored).status_code == 409


def test_concurrent_render_creation_has_one_persisted_identity(client, visual_context):
    identity = visual_context["a"]
    run = create(client, identity)
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [
            pool.submit(start_only, identity, run, visual_context["configs"][0], key)
            for _ in range(2)
        ]
        ids = [request.result() for request in requests]
    assert ids[0] == ids[1]
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        rows = repo.all("render_runs", idempotency_key=key)
        assert len(rows) == 1 and rows[0]["status"] == "CREATED"
    response = client.post(f"/v1/renders/{ids[0]}/execute", headers=headers(identity))
    assert response.status_code == 200 and response.json()["status"] == "PASS", response.text


def test_visual_configuration_and_approval_remain_immutable_for_admin(
    client, passing_render, database
):
    identity, run, rendered = passing_render
    approve_content(client, identity, run)
    approve_visual(client, identity, rendered)
    statements = [
        (
            "UPDATE visual_config_versions SET version=version+1 WHERE id=:id",
            UUID(rendered["visual_config_version_id"]),
        ),
        ("DELETE FROM visual_approval_records WHERE render_run_id=:id", UUID(rendered["id"])),
    ]
    for statement, record_id in statements:
        with pytest.raises(DBAPIError) as rejected:
            with database.begin() as conn:
                conn.execute(text(statement), {"id": record_id})
        assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
    assert export(client, identity, rendered).status_code == 200


def test_invalid_pinned_reference_persists_blocked_qa_instead_of_execution_failure(
    client, visual_context, database, monkeypatch, tmp_path
):
    identity = visual_context["a"]
    run = create(client, identity)
    # Keep deliberately invalid test bytes in this test's private catalog. Production
    # seed data and bundled visual assets are never changed by this negative case.
    catalog = tmp_path / "catalog"
    fonts = catalog / "backend" / "assets" / "fonts"
    fonts.mkdir(parents=True)
    for name in ("Inter-Regular.ttf", "Inter-Bold.ttf"):
        (fonts / name).write_bytes(
            (service.REPO_ROOT / "backend" / "assets" / "fonts" / name).read_bytes()
        )
    reference = catalog / "characters" / "invalid-reference.png"
    reference.parent.mkdir()
    reference.write_bytes(b"Intentionally invalid raster fixture; not a real image.")
    monkeypatch.setattr(service, "REPO_ROOT", catalog)
    monkeypatch.setattr(visual_seed, "REPO_ROOT", catalog)
    config = seed_visual_config(
        database,
        identity["tenant_id"],
        identity["influencer_id"],
        display_name="Influencer test-a",
        disclosure="This is an AI creator.",
        reference_path="characters/invalid-reference.png",
        reference_metadata={"is_fixture": True},
    )
    rendered = render(client, identity, run, config)
    assert rendered["status"] == "BLOCKED", rendered
    assert rendered["error_category"] is None
    assert (
        rendered["manifest"]["reference_sha256"]
        == hashlib.sha256(reference.read_bytes()).hexdigest()
    )
    assert any(
        finding["code"] == "INVALID_REFERENCE" for finding in rendered["manifest"]["findings"]
    )
    root = visual_context["storage"] / identity["tenant_id"] / rendered["id"]
    assert not list(root.rglob("*.png"))
    approve_content(client, identity, run)
    response = client.post(
        f"/v1/renders/{rendered['id']}/approve",
        headers=headers(identity, "APPROVER"),
        json=visual_decision(rendered),
    )
    assert response.status_code == 409
    assert export(client, identity, rendered).status_code == 409
    with transaction(UUID(identity["tenant_id"]), identity["tokens"]["OPERATOR"]) as repo:
        attempt = repo.one(
            "skill_runs", workflow_run_id=UUID(run["id"]), skill_identifier="visual.render"
        )
    assert attempt["status"] == "SUCCEEDED", "A deterministic QA block is not an execution failure"


def test_disclosure_mismatch_persists_specific_blocked_qa(client, visual_context, database):
    identity = visual_context["a"]
    run = create(client, identity)
    assert run["state"] == "AWAITING_APPROVAL"
    config = seed_visual_config(
        database,
        identity["tenant_id"],
        identity["influencer_id"],
        display_name="Influencer test-a",
        disclosure="A different required AI identity disclosure.",
    )
    rendered = render(client, identity, run, config)
    assert rendered["status"] == "BLOCKED", rendered
    assert rendered["manifest"] is not None and rendered["error_category"] is None
    assert any(
        finding["code"] == "DISCLOSURE_MISMATCH" for finding in rendered["manifest"]["findings"]
    )
    root = visual_context["storage"] / identity["tenant_id"] / rendered["id"]
    assert not list(root.rglob("*.png"))
    approve_content(client, identity, run)
    response = client.post(
        f"/v1/renders/{rendered['id']}/approve",
        headers=headers(identity, "APPROVER"),
        json=visual_decision(rendered),
    )
    assert response.status_code == 409
    assert export(client, identity, rendered).status_code == 409
