import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.repository import canonical_hash
from app.metrics.schemas import (
    MAX_COUNT,
    MetricLearningInput,
    MetricLearningPayload,
    MetricSnapshotPayload,
    MetricSubjectPayload,
)
from app.metrics.service import _typed_row, describe_snapshots


def snapshot_payload(**changes):
    data = {
        "schema_version": 1,
        "observed_at": "2026-09-01T12:00:00Z",
        "scope": "LIFETIME_CUMULATIVE",
        "reach": 100,
        "saves": 10,
        "shares": None,
        "comments": 0,
        "follows": 3,
        "evidence_text": "Recorded fixture observations; not live platform measurements.",
        "definition_notes": "Fixture lifetime counters for this content only.",
    }
    return {**data, **changes}


def rows(mode="FIXTURE", before_changes=None, after_changes=None):
    tenant_id, subject_id = uuid4(), uuid4()
    payload = MetricSubjectPayload(
        render_run_id=uuid4(),
        mode=mode,
        platform_label="Internal test",
        external_reference="operator-supplied-reference" if mode == "MANUAL" else None,
        provenance_note="Internal deterministic acceptance fixture.",
    ).model_dump(mode="json")
    subject = {
        "id": subject_id,
        "tenant_id": tenant_id,
        "mode": mode,
        "payload": payload,
        "content_hash": canonical_hash(payload),
    }
    result = []
    for changes in (
        before_changes or {},
        {"observed_at": "2026-09-02T12:00:00Z", **(after_changes or {})},
    ):
        data = snapshot_payload(**changes)
        result.append(
            {
                "id": uuid4(),
                "subject_id": subject_id,
                "tenant_id": tenant_id,
                "mode": mode,
                "payload": data,
                "content_hash": canonical_hash(data),
            }
        )
    return subject, *result


def test_descriptive_learning_preserves_null_zero_decrease_and_evidence_hashes():
    subject, baseline, current = rows(
        after_changes={"reach": 125, "saves": 7, "shares": 5, "comments": 0, "follows": None}
    )
    report = describe_snapshots(subject, baseline, current)
    assert report.metrics.reach.delta == 25
    assert report.metrics.reach.direction == "INCREASED"
    assert report.metrics.saves.delta == -3
    assert report.metrics.saves.direction == "DECREASED"
    assert report.metrics.comments.delta == 0
    assert report.metrics.comments.direction == "UNCHANGED"
    assert report.metrics.shares.delta is None
    assert report.metrics.shares.direction == "UNKNOWN"
    assert report.metrics.follows.delta is None
    assert report.baseline_snapshot_hash == baseline["content_hash"]
    assert report.current_snapshot_hash == current["content_hash"]
    assert report.status == "DESCRIPTIVE"
    assert report.limitations == ["FIXTURE", "DESCRIPTIVE_ONLY", "NO_CAUSAL_INFERENCE"]
    assert report.causal_claim is report.policy_updated is False


def test_all_unknown_is_insufficient_data_not_zero():
    missing = dict.fromkeys(("reach", "saves", "shares", "comments", "follows"))
    report = describe_snapshots(*rows(before_changes=missing, after_changes=missing))
    assert report.status == "INSUFFICIENT_DATA"
    assert all(metric["delta"] is None for metric in report.metrics.model_dump().values())


def test_manual_report_is_self_reported_not_platform_verified():
    report = describe_snapshots(*rows(mode="MANUAL"))
    assert report.mode == "MANUAL"
    assert report.limitations[0] == "SELF_REPORTED"


@pytest.mark.parametrize("value", [True, "10", 10.0, -1, MAX_COUNT + 1])
def test_invalid_counter_types_and_ranges_are_rejected(value):
    with pytest.raises(ValidationError):
        MetricSnapshotPayload.model_validate_json(json.dumps(snapshot_payload(reach=value)))


def test_unavailable_counter_must_be_explicitly_null():
    payload = snapshot_payload()
    del payload["shares"]
    with pytest.raises(ValidationError):
        MetricSnapshotPayload.model_validate_json(json.dumps(payload))
    assert MetricSnapshotPayload.model_validate_json(json.dumps(snapshot_payload())).shares is None


@pytest.mark.parametrize("timestamp", ["2026-09-01T12:00:00", "2026-09-01T12:00:00+01:00"])
def test_observation_timestamp_must_explicitly_use_utc(timestamp):
    with pytest.raises(ValidationError):
        MetricSnapshotPayload.model_validate_json(
            json.dumps(snapshot_payload(observed_at=timestamp))
        )


@pytest.mark.parametrize("mode,reference", [("MANUAL", None), ("MANUAL", " "), ("FIXTURE", "post")])
def test_subject_cannot_confuse_fixture_with_external_reference(mode, reference):
    with pytest.raises(ValidationError):
        MetricSubjectPayload(
            render_run_id=uuid4(),
            mode=mode,
            platform_label="Example",
            external_reference=reference,
            provenance_note="Operator supplied.",
        )


@pytest.mark.parametrize("field", ["subject_id", "tenant_id", "mode"])
def test_comparison_rejects_unrelated_observations(field):
    subject, baseline, current = rows()
    current[field] = "MANUAL" if field == "mode" else uuid4()
    with pytest.raises(ValueError, match="same subject"):
        describe_snapshots(subject, baseline, current)


@pytest.mark.parametrize(
    "change",
    [
        {"observed_at": "2026-09-01T12:00:00Z"},
        {"observed_at": "2026-08-01T12:00:00Z"},
        {"definition_notes": "Different counter definitions cannot be compared."},
    ],
)
def test_undefined_comparison_order_or_definitions_are_rejected(change):
    with pytest.raises(ValueError):
        describe_snapshots(*rows(after_changes=change))


def test_tampered_observation_cannot_enter_learning():
    subject, baseline, current = rows()
    current["payload"]["reach"] = 999
    with pytest.raises(ValueError, match="checksum"):
        describe_snapshots(subject, baseline, current)


@pytest.mark.parametrize(
    "field,value",
    [("causal_claim", True), ("policy_updated", True), ("status", "INSUFFICIENT_DATA")],
)
def test_derived_report_cannot_claim_causality_update_policy_or_hide_data(field, value):
    payload = describe_snapshots(*rows()).model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        MetricLearningPayload.model_validate_json(json.dumps(payload))


def test_learning_schema_rejects_invented_delta_and_missing_provenance_limitations():
    payload = describe_snapshots(*rows()).model_dump(mode="json")
    payload["metrics"]["reach"]["delta"] = 15
    with pytest.raises(ValidationError):
        MetricLearningPayload.model_validate_json(json.dumps(payload))
    payload = describe_snapshots(*rows()).model_dump(mode="json")
    payload["limitations"] = ["DESCRIPTIVE_ONLY", "NO_CAUSAL_INFERENCE"]
    with pytest.raises(ValidationError):
        MetricLearningPayload.model_validate_json(json.dumps(payload))


def test_learning_requires_two_distinct_snapshot_ids():
    snapshot_id = uuid4()
    with pytest.raises(ValidationError):
        MetricLearningInput(
            idempotency_key="same-observation",
            baseline_snapshot_id=snapshot_id,
            current_snapshot_id=snapshot_id,
        )


def test_empty_evidence_or_extraneous_schema_fields_are_rejected():
    for changes in ({"evidence_text": " "}, {"provider_verified": True}):
        with pytest.raises(ValidationError):
            MetricSnapshotPayload.model_validate_json(json.dumps(snapshot_payload(**changes)))


def test_largest_safe_integer_delta_stays_exact_without_float_conversion():
    report = describe_snapshots(
        *rows(before_changes={"reach": MAX_COUNT}, after_changes={"reach": 0})
    )
    assert report.metrics.reach.delta == -MAX_COUNT
    assert isinstance(report.metrics.reach.delta, int)
    assert report.current_observed_at - report.baseline_observed_at == timedelta(days=1)
    assert report.baseline_observed_at == datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-01T12:00:00+00:00",
        "2026-09-01T12:00:00.000000+00:00",
        "2026-09-01T12:00:00.120000+00:00",
    ],
)
@pytest.mark.parametrize("kind", ["snapshot", "learning"])
def test_metric_responses_preserve_immutable_timestamp_representation_and_hash(timestamp, kind):
    subject, baseline, current = rows(before_changes={"observed_at": timestamp})
    if kind == "snapshot":
        record = {**baseline, "verification_status": "FIXTURE"}
        schema = MetricSnapshotPayload
        timestamp_field = "observed_at"
    else:
        payload = describe_snapshots(subject, baseline, current).model_dump(mode="json")
        # SQL keeps the original observation text in its immutable report payload.
        payload["baseline_observed_at"] = timestamp
        record = {
            "mode": "FIXTURE",
            "verification_status": "FIXTURE",
            "payload": payload,
            "content_hash": canonical_hash(payload),
        }
        schema = MetricLearningPayload
        timestamp_field = "baseline_observed_at"
    returned = _typed_row(record, schema)
    assert returned["payload"][timestamp_field] == timestamp
    assert returned["payload"] == record["payload"]
    assert canonical_hash(returned["payload"]) == returned["content_hash"]
