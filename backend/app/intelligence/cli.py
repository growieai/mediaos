"""Manual live ingestion and draft generation; never publishes or schedules."""

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from app.config import REPO_ROOT
from app.db.repository import transaction
from app.intelligence.editorial import evaluate, start_opportunity_workflow
from app.intelligence.ingestion import IngestionRunner, create_ingestion
from app.intelligence.schemas import DiscoveryRequest, OpportunityWorkflowRequest
from app.services.workflows import ConflictError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["ingest", "draft"])
    parser.add_argument("--source", default="BDNS", choices=["BDNS", "BOE", "CAMARA"])
    parser.add_argument(
        "--since", type=date.fromisoformat, default=datetime.now(UTC).date() - timedelta(days=20)
    )
    parser.add_argument("--until", type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument("--query", default="PYME")
    parser.add_argument("--key", default=None)
    parser.add_argument("--opportunity", type=UUID)
    args = parser.parse_args()
    credentials = json.loads((REPO_ROOT / ".local/credentials.json").read_text(encoding="utf-8"))
    tenant = UUID(credentials["tenant_id"])
    token = credentials["tokens"]["OPERATOR"]
    if args.action == "ingest":
        with transaction(tenant, token) as repo:
            source = repo.one("source_definitions", source_key=args.source)
        request = DiscoveryRequest(
            source_definition_id=source["id"],
            idempotency_key=args.key or str(uuid4()),
            since=args.since,
            until=args.until,
            query=args.query,
        )
        run = create_ingestion(tenant, token, request, uuid4())
        result = IngestionRunner(tenant).execute(run["id"])
        print(
            json.dumps(
                {
                    "ingestion_run_id": str(run["id"]),
                    "status": result["status"],
                    "counters": result["counters"],
                },
                indent=2,
            )
        )
    else:
        with transaction(tenant, token) as repo:
            repo.require("OPERATOR")
            audience = repo.one("audience_segments", code="GENERIC_SMB")
            existing = (
                repo.all("workflow_runs", idempotency_key=args.key) if args.key is not None else []
            )
            if existing:
                bindings = repo.all("workflow_opportunities", workflow_run_id=existing[0]["id"])
                if not bindings:
                    raise ConflictError("Idempotency key is bound to a different editorial request")
                # A saved draft now appears in content history. Re-evaluation could
                # select WATCH or a different opportunity before the replay guard.
                selected = repo.one("opportunities", id=bindings[0]["opportunity_id"])
                opportunity_id = args.opportunity or selected["id"]
            else:
                choices = (
                    repo.all("opportunities", id=args.opportunity)
                    if args.opportunity
                    else repo.all("opportunities")
                )
                selected = None
                for opportunity in choices:
                    result = evaluate(repo, opportunity, UUID(credentials["mission_id"]))
                    if any(
                        r["audience_segment"]["id"] == audience["id"]
                        and r["decision"]["decision"] == "CREATE_CONTENT"
                        for r in result
                    ):
                        selected = opportunity
                        break
                opportunity_id = selected["id"] if selected else None
        if selected is None:
            raise SystemExit(
                "No opportunity qualified. Review persisted decisions; no unsupported draft was created."
            )
        draft_request = OpportunityWorkflowRequest(
            influencer_id=UUID(credentials["influencer_id"]),
            mission_id=UUID(credentials["mission_id"]),
            audience_segment_id=audience["id"],
            idempotency_key=args.key or str(uuid4()),
        )
        run = start_opportunity_workflow(tenant, token, opportunity_id, draft_request, uuid4())
        print(
            json.dumps(
                {
                    "workflow_run_id": str(run["id"]),
                    "opportunity": selected["canonical_external_id"],
                    "state": run["state"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
