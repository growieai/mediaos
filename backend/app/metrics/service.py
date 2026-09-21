"""Tenant-scoped imports and database-guarded descriptive learning; no provider calls."""

import json
import logging
from uuid import UUID

from sqlalchemy import text

from app.db.repository import Repository, canonical_hash, transaction
from app.metrics.schemas import (
    ALGORITHM_VERSION,
    METRICS,
    Direction,
    MetricComparison,
    MetricComparisons,
    MetricLearningInput,
    MetricLearningPayload,
    MetricSnapshotInput,
    MetricSnapshotPayload,
    MetricSubjectInput,
    MetricSubjectPayload,
)
from app.services.workflows import typed

log = logging.getLogger("mediaos")


def _checked_payload(row, schema):
    if canonical_hash(row["payload"]) != row["content_hash"]:
        raise ValueError("Metric payload checksum does not match its immutable record")
    return typed(schema, row["payload"])


def _typed_row(row, schema):
    result = dict(row)
    payload = _checked_payload(row, schema)
    # Validate without reserializing: equivalent timestamp representations can
    # normalize differently, while content_hash covers the original immutable JSON.
    mode = (
        payload.mode
        if isinstance(payload, MetricSubjectPayload | MetricLearningPayload)
        else row["mode"]
    )
    expected_verification = "SELF_REPORTED" if mode == "MANUAL" else "FIXTURE"
    if row["mode"] != mode or row["verification_status"] != expected_verification:
        raise ValueError("Metric record provenance does not match its payload")
    return result


def describe_snapshots(subject, baseline, current) -> MetricLearningPayload:
    """Pure comparison, also used to verify the database-produced learning contract."""
    subject_payload = _checked_payload(subject, MetricSubjectPayload)
    before = _checked_payload(baseline, MetricSnapshotPayload)
    after = _checked_payload(current, MetricSnapshotPayload)
    for observation in (baseline, current):
        if (
            observation["subject_id"] != subject["id"]
            or observation["mode"] != subject_payload.mode
            or observation["tenant_id"] != subject["tenant_id"]
        ):
            raise ValueError("Learning snapshots must have the same subject and provenance")
    if before.definition_notes != after.definition_notes or before.scope != after.scope:
        raise ValueError("Learning requires identical metric definitions and scope")
    comparisons = {}
    for metric in METRICS:
        old, new = getattr(before, metric), getattr(after, metric)
        delta = None if old is None or new is None else new - old
        direction: Direction = (
            "UNKNOWN"
            if delta is None
            else "INCREASED"
            if delta > 0
            else "DECREASED"
            if delta < 0
            else "UNCHANGED"
        )
        comparisons[metric] = MetricComparison(
            baseline=old, current=new, delta=delta, direction=direction
        )
    return MetricLearningPayload(
        subject_id=subject["id"],
        baseline_snapshot_id=baseline["id"],
        current_snapshot_id=current["id"],
        baseline_snapshot_hash=baseline["content_hash"],
        current_snapshot_hash=current["content_hash"],
        mode=subject_payload.mode,
        scope=before.scope,
        baseline_observed_at=before.observed_at,
        current_observed_at=after.observed_at,
        definition_notes=before.definition_notes,
        metrics=MetricComparisons.model_validate(comparisons, strict=True),
        status="INSUFFICIENT_DATA"
        if all(value.delta is None for value in comparisons.values())
        else "DESCRIPTIVE",
        limitations=[
            "SELF_REPORTED" if subject_payload.mode == "MANUAL" else "FIXTURE",
            "DESCRIPTIVE_ONLY",
            "NO_CAUSAL_INFERENCE",
        ],
    )


def learning_reports(repo: Repository, subject_id: UUID):
    repo.one("metric_subjects", id=subject_id)
    return [
        _typed_row(row, MetricLearningPayload)
        for row in repo.all("learning_reports", subject_id=subject_id)
    ]


def subject_details(repo: Repository, subject_id: UUID):
    row = _typed_row(repo.one("metric_subjects", id=subject_id), MetricSubjectPayload)
    row["snapshots"] = [
        _typed_row(item, MetricSnapshotPayload)
        for item in repo.all("metric_snapshots", subject_id=subject_id)
    ]
    row["learning_reports"] = learning_reports(repo, subject_id)
    return row


def workflow_subjects(repo: Repository, workflow_id: UUID):
    repo.one("workflow_runs", id=workflow_id)
    return [
        _typed_row(row, MetricSubjectPayload)
        for row in repo.all("metric_subjects", workflow_run_id=workflow_id)
    ]


def create_subject(tenant: UUID, token: str, workflow_id: UUID, request: MetricSubjectInput):
    request = MetricSubjectInput.model_validate_json(
        request.model_dump_json(warnings=False), strict=True
    )
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        repo.one("workflow_runs", id=workflow_id)
        repo.one("render_runs", id=request.render_run_id)
        subject_id = repo.connection.execute(
            text("SELECT register_metric_subject(:workflow,CAST(:payload AS jsonb),:key,:hash)"),
            {
                "workflow": workflow_id,
                "payload": json.dumps(payload, ensure_ascii=False),
                "key": request.idempotency_key,
                "hash": canonical_hash({"workflow_run_id": workflow_id, "payload": payload}),
            },
        ).scalar_one()
        result = subject_details(repo, subject_id)
    _log("metric_subject_registered", result, "RECORDED")
    return result


def import_snapshot(tenant: UUID, token: str, subject_id: UUID, request: MetricSnapshotInput):
    request = MetricSnapshotInput.model_validate_json(
        request.model_dump_json(warnings=False), strict=True
    )
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        repo.one("metric_subjects", id=subject_id)
        snapshot_id = repo.connection.execute(
            text("SELECT import_metric_snapshot(:subject,CAST(:payload AS jsonb),:key,:hash)"),
            {
                "subject": subject_id,
                "payload": json.dumps(payload, ensure_ascii=False),
                "key": request.idempotency_key,
                "hash": canonical_hash({"subject_id": subject_id, "payload": payload}),
            },
        ).scalar_one()
        result = _typed_row(repo.one("metric_snapshots", id=snapshot_id), MetricSnapshotPayload)
    _log("metric_snapshot_imported", result, "RECORDED")
    return result


def create_learning(tenant: UUID, token: str, subject_id: UUID, request: MetricLearningInput):
    request = MetricLearningInput.model_validate_json(
        request.model_dump_json(warnings=False), strict=True
    )
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        subject = repo.one("metric_subjects", id=subject_id)
        baseline = repo.one("metric_snapshots", id=request.baseline_snapshot_id)
        current = repo.one("metric_snapshots", id=request.current_snapshot_id)
        report_id = repo.connection.execute(
            text("SELECT build_metric_learning(:subject,:baseline,:current,:key,:hash)"),
            {
                "subject": subject_id,
                "baseline": request.baseline_snapshot_id,
                "current": request.current_snapshot_id,
                "key": request.idempotency_key,
                "hash": canonical_hash(
                    {
                        "subject_id": subject_id,
                        "baseline_snapshot_id": request.baseline_snapshot_id,
                        "current_snapshot_id": request.current_snapshot_id,
                        "algorithm_version": ALGORITHM_VERSION,
                    }
                ),
            },
        ).scalar_one()
        row = repo.one("learning_reports", id=report_id)
        actual = _checked_payload(row, MetricLearningPayload)
        if actual != describe_snapshots(subject, baseline, current):
            raise ValueError("Persisted learning output differs from its exact observations")
        result = _typed_row(row, MetricLearningPayload)
    _log("metric_learning_created", result, result["status"])
    return result


def _log(event, row, state):
    log.info(
        event,
        extra={
            "tenant_id": str(row["tenant_id"]),
            "workflow_run_id": str(row["workflow_run_id"]),
            "skill_run_id": str(row["skill_run_id"]) if row.get("skill_run_id") else None,
            "state": state,
        },
    )
