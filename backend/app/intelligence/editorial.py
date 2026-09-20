from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.db.repository import transaction
from app.intelligence.policy import editorial_decision, score_opportunity
from app.intelligence.schemas import Audience, EditorialPolicy, OpportunityResearch
from app.models.schemas import CreateRun
from app.services.workflows import ConflictError, Runner, create_run, typed


def evaluate(repo, opportunity, mission_id):
    repo.require("OPERATOR")
    version = repo.one("opportunity_versions", id=opportunity["current_version_id"])
    version["has_material_changes"] = any(
        event["materiality"] == "HIGH"
        for event in repo.all(
            "change_events", opportunity_id=opportunity["id"], new_version_id=version["id"]
        )
    )
    mission = repo.one("missions", id=mission_id)
    policies = repo.all("mission_editorial_policies", mission_id=mission_id)
    if not policies:
        raise ConflictError("Mission requires an editorial policy")
    policy_row = max(policies, key=lambda r: r["version"])
    policy = typed(EditorialPolicy, policy_row["payload"])
    links = repo.all("source_links", opportunity_id=opportunity["id"])
    snapshots = [
        repo.one("source_snapshots", id=sid) for sid in version["payload"]["source_hashes"]
    ]
    conflicts = repo.all("verification_conflicts", opportunity_id=opportunity["id"])
    verified = all(
        s["verification_status"] == "VERIFIED" and not s["is_fixture"] for s in snapshots
    )
    history = [
        r
        for r in repo.all("workflow_opportunities", opportunity_id=opportunity["id"])
        if r["created_at"] > datetime.now(UTC) - timedelta(days=policy.repeat_window_days)
    ]
    facts = repo.all("opportunity_facts", opportunity_id=opportunity["id"])
    results = []
    observed_at = []
    for snapshot in snapshots:
        observations = repo.all(
            "source_observations", raw_document_id=snapshot["raw_document_id"], is_fixture=False
        )
        observed_at.append(
            max([o["fetched_at"] for o in observations] or [snapshot["captured_at"]])
        )
    expiry = min(observed_at) + timedelta(hours=policy.freshness_hours)
    for row in repo.all("audience_segments"):
        audience = typed(Audience, row["payload"])
        score = score_opportunity(version, audience, policy, datetime.now(UTC).date())
        decision = editorial_decision(
            version,
            score,
            policy,
            mission,
            history,
            conflicts,
            verified and expiry > datetime.now(UTC),
        )
        score_row = repo.insert(
            "relevance_scores",
            opportunity_version_id=version["id"],
            audience_segment_id=row["id"],
            mission_id=mission_id,
            policy_id=policy_row["id"],
            schema_version=1,
            payload=score.model_dump(mode="json"),
            expires_at=expiry,
        )
        research = {
            "opportunity_metadata": {
                "id": str(opportunity["id"]),
                "canonical_external_id": opportunity["canonical_external_id"],
                "version_id": str(version["id"]),
                "profile": version["payload"]["profile"],
            },
            "verified_fact_ids": [
                str(f["id"])
                for f in facts
                if f["verification_status"] == "VERIFIED"
                and str(f["source_snapshot_id"]) in version["payload"]["source_hashes"]
            ],
            "uncertainty": version["payload"]["uncertainty"],
            "conflict_ids": [str(c["id"]) for c in conflicts],
            "evidence_snapshots": [str(s["source_snapshot_id"]) for s in links],
            "fresh_until": expiry.isoformat(),
        }
        decision_row = repo.insert(
            "editorial_decisions",
            relevance_score_id=score_row["id"],
            decision=decision.decision,
            schema_version=1,
            payload=decision.model_dump(mode="json"),
            research_context=research,
        )
        results.append({"audience_segment": row, "score": score_row, "decision": decision_row})
    return results


def start_opportunity_workflow(tenant, token, opportunity_id, request, correlation_id):
    with transaction(tenant, token) as repo:
        repo.require("OPERATOR")
        existing = repo.all("workflow_runs", idempotency_key=request.idempotency_key)
        if existing:
            bindings = repo.all("workflow_opportunities", workflow_run_id=existing[0]["id"])
            if (
                bindings
                and bindings[0]["opportunity_id"] == opportunity_id
                and bindings[0]["audience_segment_id"] == request.audience_segment_id
                and existing[0]["mission_id"] == request.mission_id
                and existing[0]["influencer_id"] == request.influencer_id
            ):
                return existing[0]
            raise ConflictError("Idempotency key is bound to a different editorial request")
        opportunity = repo.one("opportunities", id=opportunity_id)
        repo.one("missions", id=request.mission_id, influencer_id=request.influencer_id)
        repo.one("audience_segments", id=request.audience_segment_id)
        evaluated = evaluate(repo, opportunity, request.mission_id)
        selected = next(
            r for r in evaluated if r["audience_segment"]["id"] == request.audience_segment_id
        )
        if selected["decision"]["decision"] != "CREATE_CONTENT":
            # Decision must commit even when there is no workflow to create.
            selection_error = selected["decision"]["decision"]
        else:
            selection_error = None
        version = repo.one("opportunity_versions", id=opportunity["current_version_id"])
        sources = [
            repo.one("source_snapshots", id=sid) for sid in version["payload"]["source_hashes"]
        ]
        # Prefer the snapshot with the richest structural evidence, not the newest summary.
        source = max(sources, key=lambda s: len(s["evidence_input"]))
        facts = repo.all(
            "opportunity_facts", opportunity_id=opportunity_id, source_snapshot_id=source["id"]
        )
        context = OpportunityResearch(
            opportunity_id=opportunity_id,
            opportunity_version_id=version["id"],
            canonical_external_id=opportunity["canonical_external_id"],
            audience_segment_id=request.audience_segment_id,
            relevance_score_id=selected["score"]["id"],
            editorial_decision_id=selected["decision"]["id"],
            source_snapshot_ids=[s["id"] for s in sources],
            opportunity_fact_ids=[f["id"] for f in facts],
            uncertainty=version["payload"]["uncertainty"],
            conflict_ids=[],
            opening_date=version["opening_date"],
            closing_date=version["closing_date"],
            fresh_until=selected["score"]["expires_at"],
            recommended_angle=selected["decision"]["payload"]["recommended_angle"],
        )
        payload = {
            "influencer_id": str(request.influencer_id),
            "mission_id": str(request.mission_id),
            "idempotency_key": request.idempotency_key,
            "source": {
                "source_type": "OFFICIAL",
                "origin": source["origin"],
                "canonical_url": source["canonical_url"],
                "title": source["title"],
                "publisher": source["publisher"],
                "raw_content": source["raw_content"],
                "captured_at": source["captured_at"].isoformat(),
                "classification": "PRIMARY",
                "is_fixture": source["is_fixture"],
                "metadata": source["metadata"],
                "evidence": source["evidence_input"],
            },
        }
    if selection_error:
        raise ConflictError("Editorial decision: " + selection_error)

    def bind(repo, run):
        bindings = repo.all("workflow_opportunities", workflow_run_id=run["id"])
        if bindings:
            return
        repo.connection.execute(
            text(
                "SELECT bind_opportunity_run(:rid,:vid,:sid,:eid,:audience,CAST(:context AS jsonb))"
            ),
            {
                "rid": run["id"],
                "vid": version["id"],
                "sid": source["id"],
                "eid": selected["decision"]["id"],
                "audience": request.audience_segment_id,
                "context": context.model_dump_json(),
            },
        )

    run = create_run(tenant, token, typed(CreateRun, payload), correlation_id, after_create=bind)
    return Runner(tenant, token).execute(run["id"])
