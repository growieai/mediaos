from hashlib import sha256

from app.models.schemas import ResearchPack, SourceInput, VerifiedFact


def research_source_mock(source: SourceInput) -> ResearchPack:
    """Deterministic demo implementation.

    It intentionally does not invent facts. It treats only the supplied source text as evidence.
    Replace this skill implementation with live retrieval + structured model extraction later.
    """
    digest = sha256(source.raw_text.encode("utf-8")).hexdigest()[:10]
    evidence = source.raw_text.strip().replace("\n", " ")[:360]
    fact = VerifiedFact(
        claim=f"A relevant Spain SMB opportunity/update was identified: {source.title}",
        evidence=evidence,
        source_id=source.source_id,
        confidence=0.96,
    )
    return ResearchPack(
        research_pack_id=f"rp_{digest}",
        topic=source.title,
        market=source.market,
        audience=["Spanish SMB owners", "autónomos"],
        verified_facts=[fact],
        uncertain_facts=[],
        implications=["Potentially useful enough to explain in simple language to SMB owners."],
        confidence=0.96,
        publishable=source.source_type in {"official", "manual_test"},
    )
