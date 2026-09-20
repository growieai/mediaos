"""Pure deterministic comparisons and eligibility. Missing evidence remains unknown."""

from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal

from app.intelligence.schemas import (
    Audience,
    ChangeEvent,
    EditorialDecision,
    EditorialPolicy,
    RelevanceScore,
)


def opportunity_status(profile: dict, today: date, conflicted=False):
    if conflicted:
        return "CONFLICT"
    closing, opening = profile.get("application_deadline"), profile.get("application_start")
    if closing and date.fromisoformat(closing) < today:
        return "CLOSED"
    if opening and date.fromisoformat(opening) > today:
        return "UPCOMING"
    if opening and closing:
        return "OPEN"
    return "UNKNOWN"


def changes(old, new):
    if old is None:
        return [
            ChangeEvent(
                type="NEW_OPPORTUNITY",
                field="opportunity",
                new_value=new["title"],
                materiality="HIGH",
            )
        ]
    result = []
    fields: dict[
        str,
        Literal[
            "STATUS_CHANGED",
            "OPENING_DATE_CHANGED",
            "DEADLINE_CHANGED",
            "AMOUNT_CHANGED",
            "FUNDING_PERCENTAGE_CHANGED",
            "ELIGIBILITY_CHANGED",
        ],
    ] = {
        "status": "STATUS_CHANGED",
        "opening_date": "OPENING_DATE_CHANGED",
        "closing_date": "DEADLINE_CHANGED",
        "max_amount": "AMOUNT_CHANGED",
        "funding_percentage": "FUNDING_PERCENTAGE_CHANGED",
        "geography": "ELIGIBILITY_CHANGED",
        "business_types": "ELIGIBILITY_CHANGED",
        "industries": "ELIGIBILITY_CHANGED",
    }
    for field, kind in fields.items():
        before, after = old.get(field), new.get(field)
        # Normalize DB date/Decimal values to canonical strings for comparison.
        if isinstance(before, (date, Decimal)):
            before = str(before)
        if isinstance(after, (date, Decimal)):
            after = str(after)
        if before != after:
            result.append(
                ChangeEvent(
                    type=kind, field=field, old_value=before, new_value=after, materiality="HIGH"
                )
            )
    if old["payload"].get("source_hashes") != new["payload"].get("source_hashes"):
        result.append(
            ChangeEvent(type="DOCUMENT_CHANGED", field="source_hashes", materiality="LOW")
        )
    for field, kind in (
        ("available_budget", "AMOUNT_CHANGED"),
        ("minimum_amount", "AMOUNT_CHANGED"),
        ("excluded_sectors", "ELIGIBILITY_CHANGED"),
        ("required_documents", "ELIGIBILITY_CHANGED"),
        ("eligible_expenses", "ELIGIBILITY_CHANGED"),
        ("excluded_expenses", "ELIGIBILITY_CHANGED"),
        ("nace", "ELIGIBILITY_CHANGED"),
    ):
        before = old["payload"].get("profile", {}).get(field)
        after = new["payload"].get("profile", {}).get(field)
        if before != after:
            result.append(
                ChangeEvent.model_validate(
                    {
                        "type": kind,
                        "field": field,
                        "old_value": before,
                        "new_value": after,
                        "materiality": "HIGH",
                    }
                )
            )
    if old["status"] != new["status"] and new["status"] in ("OPEN", "CLOSED"):
        result.append(
            ChangeEvent(
                type="APPLICATION_OPENED" if new["status"] == "OPEN" else "APPLICATION_CLOSED",
                field="status",
                old_value=old["status"],
                new_value=new["status"],
                materiality="HIGH",
            )
        )
    return result


def duplicate_similarity(left: str, right: str) -> float:
    def normalized(value):
        return " ".join("".join(c if c.isalnum() else " " for c in value.casefold()).split())

    return SequenceMatcher(None, normalized(left), normalized(right)).ratio()


def score_opportunity(
    version, audience: Audience, policy: EditorialPolicy, today: date
) -> RelevanceScore:
    profile = version["payload"]["profile"]
    closing, opening = version["closing_date"], version["opening_date"]
    eligibility: Literal["POTENTIALLY_ELIGIBLE", "INELIGIBLE", "UNKNOWN"] = "UNKNOWN"
    reasons = ["Broad programme fit is not confirmation that a particular applicant qualifies."]
    if profile.get("sme") == "YES":
        eligibility = "POTENTIALLY_ELIGIBLE"
    if profile.get("sme") == "NO" or (closing and closing < today):
        eligibility = "INELIGIBLE"
        reasons.append("Expired window or explicitly excluded applicant type.")
    nace = profile.get("nace", [])
    if (
        audience.nace_prefixes
        and nace
        and not any(
            code.startswith(prefix) or prefix.startswith(code)
            for code in nace
            for prefix in audience.nace_prefixes
        )
    ):
        if any(not code.isdecimal() for code in nace):
            if eligibility != "INELIGIBLE":
                eligibility = "UNKNOWN"
            reasons.append(
                "Sector hierarchy is unresolved; numeric divisions cannot be excluded from letter sections."
            )
        else:
            eligibility = "INELIGIBLE"
            reasons.append("Registered sector codes do not match this audience segment.")
    if audience.country != version["payload"]["country"]:
        eligibility = "INELIGIBLE"
        reasons.append("Country mismatch.")
    text = (version["title"] + " " + " ".join(profile.get("industries", []))).casefold()
    keyword_match = any(w.casefold() in text for w in audience.keywords)
    age = max(0, (today - version["created_at"].date()).days) if version.get("created_at") else 0
    days = (closing - today).days if closing else None
    # Programme budget is deliberately NOT used as value per business.
    amount = float(version["max_amount"] or 0)
    components = {
        "audience_relevance": 85.0
        if keyword_match
        else (65.0 if eligibility == "POTENTIALLY_ELIGIBLE" else 20.0),
        "financial_value": min(100.0, amount / 1000) if amount else 0.0,
        "urgency": 90.0
        if days is not None and 0 <= days <= 14
        else (55.0 if days is not None and days > 14 else 0.0),
        "freshness": max(0.0, 100.0 - age * 5),
        "addressable_audience": 60.0 if profile.get("sme") == "YES" else 10.0,
        "shareability": 60.0 if closing else 10.0,
        "authority_potential": float(version["source_confidence"]) * 100,
        "brand_strategic_fit": 0.0,
    }
    if eligibility == "INELIGIBLE":
        components["audience_relevance"] = 0.0
    if opening is None:
        reasons.append("Opening date is unknown; do not claim applications are open.")
    final = round(
        sum(components[k] * weight for k, weight in policy.weights.model_dump().items()), 2
    )
    return RelevanceScore(
        eligibility=eligibility,
        components=components,
        weights=policy.weights,
        final_score=final,
        reasons=reasons,
    )


def editorial_decision(
    version, score: RelevanceScore, policy: EditorialPolicy, mission, history, conflicts, verified
):
    reasons = list(score.reasons)
    decision: Literal["CREATE_CONTENT", "WATCH", "IGNORE", "HUMAN_REVIEW"]
    if conflicts or not verified or not version["closing_date"]:
        decision = "HUMAN_REVIEW"
        reasons.append("Conflicting, unverified or incomplete mandatory source evidence.")
    elif score.eligibility == "INELIGIBLE":
        decision = "IGNORE"
    elif history and (
        any(h.get("opportunity_version_id") == version.get("id") for h in history)
        or not version.get("has_material_changes", True)
    ):
        decision = "WATCH"
        reasons.append("A workflow already covers this opportunity in the content-history window.")
    elif score.eligibility == "UNKNOWN":
        decision = "HUMAN_REVIEW"
        reasons.append("Audience eligibility has not been established even at programme level.")
    elif score.final_score >= policy.create_threshold:
        decision = "CREATE_CONTENT"
        reasons.append(
            "Verified programme information clears the mission's configured score threshold."
        )
    else:
        decision = "WATCH"
    return EditorialDecision(
        decision=decision,
        reasons=reasons,
        recommended_angle="Review the official programme and its stated conditions; do not promise eligibility or that applications are open.",
        content_history_ids=[h["workflow_run_id"] for h in history],
        mission_objective=mission["objective"],
        content_mix=policy.content_mix,
    )
