"""Descriptive comparison of immutable connector observations, never causal learning."""

import logging
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import text

from app.db.repository import canonical_hash, transaction
from app.metrics.schemas import Digest, Direction, MetricComparison, MetricLearningInput
from app.models.schemas import StrictModel
from app.services.workflows import typed
from app.social.schemas import PlatformObservation

ALGORITHM = "platform-descriptive-v1"
MetricName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Definition = Annotated[str, Field(min_length=1, max_length=2000)]
log = logging.getLogger("mediaos")


class PlatformLearning(StrictModel):
    schema_version: Literal[1] = 1
    algorithm_version: Literal["platform-descriptive-v1"] = "platform-descriptive-v1"
    provenance: Literal["PLATFORM"] = "PLATFORM"
    scope: Literal["LIFETIME_CUMULATIVE"] = "LIFETIME_CUMULATIVE"
    publish_run_id: UUID
    workflow_run_id: UUID
    connection_id: UUID
    account_id: str = Field(pattern=r"^[0-9]{1,64}$")
    media_id: str = Field(pattern=r"^[0-9]{1,64}$")
    api_version: str = Field(pattern=r"^v[0-9]{1,2}\.0$")
    baseline_snapshot_id: UUID
    current_snapshot_id: UUID
    baseline_snapshot_hash: Digest
    current_snapshot_hash: Digest
    baseline_observed_at: datetime
    current_observed_at: datetime
    definitions: dict[MetricName, Definition] = Field(min_length=1, max_length=50)
    metrics: dict[MetricName, MetricComparison] = Field(min_length=1, max_length=50)
    status: Literal["DESCRIPTIVE", "INSUFFICIENT_DATA"]
    limitations: list[Literal["PLATFORM_OBSERVATION", "DESCRIPTIVE_ONLY", "NO_CAUSAL_INFERENCE"]]
    causal_claim: Literal[False] = False
    policy_updated: Literal[False] = False
    network_performed: Literal[False] = False

    @model_validator(mode="after")
    def exact_description(self):
        if any(
            t.tzinfo is None or t.utcoffset() != timedelta(0)
            for t in (self.baseline_observed_at, self.current_observed_at)
        ):
            raise ValueError("UTC observation timestamps required")
        if (
            self.baseline_snapshot_id == self.current_snapshot_id
            or self.baseline_observed_at >= self.current_observed_at
        ):
            raise ValueError("Distinct snapshots in strictly increasing time order required")
        if self.definitions.keys() != self.metrics.keys() or any(
            not d.strip() for d in self.definitions.values()
        ):
            raise ValueError("Every metric requires its exact nonblank definition")
        if self.limitations != ["PLATFORM_OBSERVATION", "DESCRIPTIVE_ONLY", "NO_CAUSAL_INFERENCE"]:
            raise ValueError("Descriptive platform limitations required")
        expected = (
            "INSUFFICIENT_DATA"
            if all(v.delta is None for v in self.metrics.values())
            else "DESCRIPTIVE"
        )
        if self.status != expected:
            raise ValueError("Status must reflect comparable observations")
        return self


def _observation(row):
    if canonical_hash(row["payload"]) != row["content_hash"]:
        raise ValueError("Observation hash mismatch")
    payload = typed(PlatformObservation, row["payload"])
    if canonical_hash(payload.raw) != payload.raw_hash or row["captured_at"] != payload.captured_at:
        raise ValueError("Observation evidence mismatch")
    return payload


def describe(publish, connection, baseline, current) -> PlatformLearning:
    before, after = _observation(baseline), _observation(current)
    if (
        publish["status"] != "PUBLISHED"
        or connection["id"] != publish["connection_id"]
        or connection["account_id"] != publish["account_id"]
        or connection["tenant_id"] != publish["tenant_id"]
    ):
        raise ValueError("Exact published account required")
    for row, data in ((baseline, before), (current, after)):
        if (
            row["tenant_id"] != publish["tenant_id"]
            or row["publish_run_id"] != publish["id"]
            or row["workflow_run_id"] != publish["workflow_run_id"]
            or data.media_id != publish["post_id"]
            or data.api_version != connection["api_version"]
        ):
            raise ValueError(
                "Snapshots must belong to the same published post and pinned API version"
            )
        if data.metrics.keys() != data.definitions.keys():
            raise ValueError("Exact metric definitions required")
    if before.definitions != after.definitions or before.metrics.keys() != after.metrics.keys():
        raise ValueError("Snapshots require identical metric keys and definitions")
    metrics = {}
    for name in sorted(before.metrics):
        old, new = before.metrics[name], after.metrics[name]
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
        metrics[name] = MetricComparison(
            baseline=old, current=new, delta=delta, direction=direction
        )
    return PlatformLearning(
        publish_run_id=publish["id"],
        workflow_run_id=publish["workflow_run_id"],
        connection_id=connection["id"],
        account_id=connection["account_id"],
        media_id=publish["post_id"],
        api_version=connection["api_version"],
        baseline_snapshot_id=baseline["id"],
        current_snapshot_id=current["id"],
        baseline_snapshot_hash=baseline["content_hash"],
        current_snapshot_hash=current["content_hash"],
        baseline_observed_at=before.captured_at,
        current_observed_at=after.captured_at,
        definitions=before.definitions,
        metrics=metrics,
        status="INSUFFICIENT_DATA"
        if all(v.delta is None for v in metrics.values())
        else "DESCRIPTIVE",
        limitations=["PLATFORM_OBSERVATION", "DESCRIPTIVE_ONLY", "NO_CAUSAL_INFERENCE"],
    )


def _checked_report(repo, row):
    actual = typed(PlatformLearning, row["payload"])
    publish = repo.one("social_publish_runs", id=row["publish_run_id"])
    expected = describe(
        publish,
        repo.one("social_connections", id=publish["connection_id"]),
        repo.one("social_insight_snapshots", id=row["baseline_snapshot_id"]),
        repo.one("social_insight_snapshots", id=row["current_snapshot_id"]),
    )
    if (
        actual != expected
        or canonical_hash(row["payload"]) != row["content_hash"]
        or row["status"] != actual.status
        or row["workflow_run_id"] != actual.workflow_run_id
        or row["tenant_id"] != publish["tenant_id"]
    ):
        raise ValueError("Stored learning must match exact immutable observations")
    return row


def list_reports(repo, pid):
    repo.one("social_publish_runs", id=pid)
    return [
        _checked_report(repo, r) for r in repo.all("social_learning_reports", publish_run_id=pid)
    ]


def create(tenant, token, pid, request: MetricLearningInput):
    request = MetricLearningInput.model_validate_json(request.model_dump_json(), strict=True)
    expected = {
        "publish_run_id": pid,
        "baseline_snapshot_id": request.baseline_snapshot_id,
        "current_snapshot_id": request.current_snapshot_id,
        "algorithm_version": ALGORITHM,
    }
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        rid = repo.connection.execute(
            text("SELECT build_social_learning(:pid,:baseline,:current,:key,:digest)"),
            {
                "pid": pid,
                "baseline": request.baseline_snapshot_id,
                "current": request.current_snapshot_id,
                "key": request.idempotency_key,
                "digest": canonical_hash(expected),
            },
        ).scalar_one()
        report = _checked_report(repo, repo.one("social_learning_reports", id=rid))
    log.info(
        "platform_learning_created",
        extra={
            "tenant_id": str(tenant),
            "workflow_run_id": str(report["workflow_run_id"]),
            "skill_run_id": str(report["skill_run_id"]),
            "attempt": 1,
            "state": report["status"],
        },
    )
    return report
