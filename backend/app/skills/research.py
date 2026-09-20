from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from app.models.schemas import Fact, ResearchPack, SourceInput


def extract(source_id, payload: SourceInput) -> ResearchPack:
    facts = [
        Fact(
            id=uuid5(NAMESPACE_URL, f"{source_id}:{e.start}:{e.end}:{e.statement}"),
            source_snapshot_id=source_id,
            start=e.start,
            end=e.end,
            statement=e.statement,
            verification_status="UNVERIFIED",
            confidence=0.0,
            fact_type=e.fact_type,
            grant=e.grant,
        )
        for e in payload.evidence
    ]
    return ResearchPack(facts=facts, verification_status="UNVERIFIED")


def verify(pack: ResearchPack, source: dict) -> ResearchPack:
    verified = source["verification_status"] == "VERIFIED" and not source["is_fixture"]
    status: Literal["VERIFIED", "UNVERIFIED"] = "VERIFIED" if verified else "UNVERIFIED"
    facts = [
        f.model_copy(update={"verification_status": status, "confidence": 1.0 if verified else 0.0})
        for f in pack.facts
    ]
    return ResearchPack(facts=facts, verification_status=status)
