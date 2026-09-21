import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.repository import canonical_hash
from app.social.learning import PlatformLearning, describe


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def rows():
    publish = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "workflow_run_id": uuid4(),
        "connection_id": uuid4(),
        "account_id": "111",
        "post_id": "222",
        "status": "PUBLISHED",
    }
    connection = {
        "id": publish["connection_id"],
        "tenant_id": publish["tenant_id"],
        "account_id": "111",
        "api_version": "v25.0",
    }
    observations = []
    for offset, metrics in enumerate(
        (
            {"reach": 100, "saved": 5, "comments": 0, "shares": None},
            {"reach": 130, "saved": 3, "comments": 0, "shares": 4},
        )
    ):
        instant = datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=offset)
        raw = {"data": metrics}
        payload = {
            "schema_version": 1,
            "media_id": "222",
            "captured_at": instant.isoformat(),
            "api_version": "v25.0",
            "raw": raw,
            "raw_hash": canonical_hash(raw),
            "metrics": metrics,
            "definitions": {key: f"Exact lifetime {key}" for key in metrics},
        }
        observations.append(
            {
                "id": uuid4(),
                "tenant_id": publish["tenant_id"],
                "publish_run_id": publish["id"],
                "workflow_run_id": publish["workflow_run_id"],
                "payload": payload,
                "content_hash": canonical_hash(payload),
                "captured_at": instant,
            }
        )
    return publish, connection, *observations


def rehash(row):
    row["content_hash"] = canonical_hash(row["payload"])


def test_platform_description_preserves_null_zero_negative_and_exact_provenance():
    inputs = rows()
    result = describe(*inputs)
    assert result.metrics["reach"].delta == 30
    assert result.metrics["saved"].delta == -2
    assert result.metrics["saved"].direction == "DECREASED"
    assert result.metrics["comments"].delta == 0
    assert result.metrics["shares"].delta is None
    assert result.metrics["shares"].direction == "UNKNOWN"
    assert result.baseline_snapshot_hash == inputs[2]["content_hash"]
    assert result.provenance == "PLATFORM" and result.status == "DESCRIPTIVE"
    assert result.causal_claim is result.policy_updated is result.network_performed is False
    assert PlatformLearning.model_validate_json(result.model_dump_json(), strict=True) == result


def test_no_comparable_counters_are_insufficient_data():
    inputs = rows()
    for row in inputs[2:]:
        row["payload"]["metrics"] = dict.fromkeys(row["payload"]["metrics"])
        rehash(row)
    result = describe(*inputs)
    assert result.status == "INSUFFICIENT_DATA"
    assert all(v.delta is None for v in result.metrics.values())


@pytest.mark.parametrize(
    "field,value",
    [("tenant_id", uuid4()), ("publish_run_id", uuid4()), ("workflow_run_id", uuid4())],
)
def test_unrelated_snapshot_cannot_be_compared(field, value):
    inputs = rows()
    inputs[3][field] = value
    with pytest.raises(ValueError):
        describe(*inputs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("media_id", "333"),
        ("api_version", "v26.0"),
        ("raw_hash", "e" * 64),
        ("definitions", {"reach": "changed"}),
        ("metrics", {"reach": True}),
        ("metrics", {"reach": 1.5}),
        ("metrics", {"reach": -1}),
    ],
)
def test_mismatched_or_malformed_observation_is_rejected(field, value):
    inputs = rows()
    inputs[3]["payload"][field] = value
    rehash(inputs[3])
    with pytest.raises(ValueError):
        describe(*inputs)


@pytest.mark.parametrize(
    "change", ["same", "reverse", "hash", "unpublished", "definition_keys", "overflow"]
)
def test_descriptive_comparison_fails_closed(change):
    inputs = rows()
    if change == "same":
        inputs = inputs[:3] + (copy.deepcopy(inputs[2]),)
    elif change == "reverse":
        inputs = inputs[:2] + (inputs[3], inputs[2])
    elif change == "hash":
        inputs[2]["content_hash"] = "a" * 64
    elif change == "unpublished":
        inputs[0]["status"] = "UNKNOWN_OUTCOME"
    elif change == "definition_keys":
        for row in inputs[2:]:
            del row["payload"]["definitions"]["reach"]
            rehash(row)
    else:
        inputs[2]["payload"]["metrics"]["reach"] = 9007199254740992
        rehash(inputs[2])
    with pytest.raises(ValueError):
        describe(*inputs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("causal_claim", True),
        ("policy_updated", True),
        ("network_performed", True),
        ("provenance", "MANUAL"),
        ("limitations", ["DESCRIPTIVE_ONLY"]),
        ("status", "INSUFFICIENT_DATA"),
    ],
)
def test_report_schema_cannot_claim_causation_or_upgrade_provenance(field, value):
    report = describe(*rows()).model_dump(mode="json")
    report[field] = value
    import json

    with pytest.raises(ValidationError):
        PlatformLearning.model_validate_json(json.dumps(report), strict=True)
