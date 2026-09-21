"""Deterministic exact-phrase routing and evidence-excerpt replies.

Comments are untrusted data. A whole phrase must match immutable configuration;
there is no substring matching, instruction execution, inference or provider call.
"""

from typing import Literal
from uuid import UUID

from app.community.schemas import CommunityClassification, CommunityDraft
from app.db.repository import canonical_hash
from app.models.schemas import CharacterConfig, QAFinding, QAReport, TextBlock
from app.services.workflows import typed


def classify(comment_text: str, config: CharacterConfig) -> CommunityClassification:
    policy = config.community_policy
    if policy is None:
        return CommunityClassification(category="HUMAN_REVIEW", reason_code="POLICY_MISSING")
    phrase = comment_text.strip(" ")
    if phrase in policy.acknowledgement_phrases:
        return CommunityClassification(
            category="ACKNOWLEDGEMENT", reason_code="EXACT_ACKNOWLEDGEMENT"
        )
    if phrase in policy.source_request_phrases:
        return CommunityClassification(
            category="SOURCE_REQUEST", reason_code="EXACT_SOURCE_REQUEST"
        )
    return CommunityClassification(category="HUMAN_REVIEW", reason_code="UNRECOGNIZED_INPUT")


def build_draft(review: dict, config: CharacterConfig, facts: list[dict]) -> CommunityDraft:
    policy = config.community_policy
    classification = typed(CommunityClassification, review["classification"])
    if policy is None or classification.category == "HUMAN_REVIEW":
        raise ValueError("Human review has no automatically generated reply")
    blocks = [
        TextBlock(
            kind="CREATIVE",
            text=(
                policy.acknowledgement_text
                if classification.category == "ACKNOWLEDGEMENT"
                else policy.source_intro
            ),
        )
    ]
    if classification.category == "SOURCE_REQUEST":
        indexed = {str(fact["id"]): fact for fact in facts}
        for fid in review["fact_ids"]:
            fact = indexed[str(fid)]
            blocks.append(TextBlock(kind="FACT", text=fact["statement"], fact_ids=[UUID(str(fid))]))
    return CommunityDraft(
        influencer_version_id=review["influencer_version_id"],
        character_config_version_id=review["character_config_version_id"],
        policy_hash=canonical_hash(policy.model_dump(mode="json")),
        language=config.language,
        blocks=blocks,
        disclosure=config.disclosure,
    )


def validate_reply(
    review: dict, event: dict, config: CharacterConfig, facts: list[dict], parent_findings: list
) -> QAReport:
    findings = [typed(QAFinding, value) for value in parent_findings]

    def add(code, category, severity, message):
        findings.append(QAFinding(code=code, category=category, severity=severity, message=message))

    draft = typed(CommunityDraft, review["draft"])
    expected = build_draft(review, config, facts)
    if event["mode"] == "FIXTURE":
        add("COMMUNITY_FIXTURE", "WORKFLOW", "BLOCKED", "Fixture events cannot be approved")
    if draft != expected:
        add(
            "COMMUNITY_UNSUPPORTED",
            "EVIDENCE",
            "BLOCKED",
            "Reply differs from configured templates and exact approved facts",
        )
    if not draft.disclosure or draft.disclosure != config.disclosure:
        add("COMMUNITY_DISCLOSURE", "CONTENT", "BLOCKED", "Mandatory AI disclosure mismatch")
    category = review["classification"]["category"]
    if category == "SOURCE_REQUEST" and not review["fact_ids"]:
        add(
            "COMMUNITY_EVIDENCE",
            "EVIDENCE",
            "BLOCKED",
            "Source requests require selected approved facts",
        )
    if category == "ACKNOWLEDGEMENT" and review["fact_ids"]:
        add(
            "COMMUNITY_UNUSED_FACTS",
            "CONTENT",
            "BLOCKED",
            "Acknowledgements cannot attach unused facts",
        )
    policy = config.community_policy
    length = len("\n".join([block.text for block in draft.blocks] + [draft.disclosure]))
    if policy and length > policy.max_reply_chars:
        add(
            "COMMUNITY_LENGTH",
            "CONTENT",
            "REVISION_REQUIRED",
            "Reply exceeds configured character limit",
        )
    status: Literal["PASS", "REVISION_REQUIRED", "BLOCKED"] = (
        "BLOCKED"
        if any(f.severity == "BLOCKED" for f in findings)
        else "REVISION_REQUIRED"
        if findings
        else "PASS"
    )
    return QAReport(status=status, findings=findings)
