from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.models.schemas import OpportunityResearch as OpportunityResearch
from app.models.schemas import StrictModel

Knowledge = Literal["YES", "NO", "UNKNOWN"]


class GrantProfile(StrictModel):
    applicant_types: list[str] = Field(default_factory=list)
    autonomo: Knowledge = "UNKNOWN"
    sme: Knowledge = "UNKNOWN"
    microenterprise: Knowledge = "UNKNOWN"
    geography: list[str] = Field(default_factory=list)
    nace: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    excluded_sectors: list[str] = Field(default_factory=list)
    maximum_amount: str | None = None
    minimum_amount: str | None = None
    currency: str | None = None
    eligible_cost_percentage: str | None = None
    eligible_expenses: list[str] = Field(default_factory=list)
    excluded_expenses: list[str] = Field(default_factory=list)
    application_start: date | None = None
    application_deadline: date | None = None
    available_budget: str | None = None
    application_mechanism: str | None = None
    required_documents: list[str] = Field(default_factory=list)
    de_minimis: Knowledge = "UNKNOWN"
    programme_status: str | None = None


class ExtractedField(StrictModel):
    field: str
    value: str | list[str]
    statement: str = Field(min_length=1, max_length=2000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    locator: str
    unit: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class NormalizedOpportunity(StrictModel):
    schema_version: Literal[1] = 1
    country: str = Field(min_length=2, max_length=2)
    opportunity_type: Literal["SUBSIDY", "PROGRAMME"] = "SUBSIDY"
    canonical_external_id: str
    external_ids: dict[str, str] = Field(default_factory=dict)
    title: str = Field(min_length=1, max_length=2000)
    issuing_body: str
    application_url: str | None = None
    publication_date: date | None = None
    profile: GrantProfile = Field(default_factory=GrantProfile)
    linked_material: list[str] = Field(default_factory=list)
    source_text: str = Field(min_length=1, max_length=100000)
    fields: list[ExtractedField] = Field(min_length=1, max_length=100)
    uncertainty: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def exact_spans(self):
        for fact in self.fields:
            if self.source_text[fact.start : fact.end] != fact.statement:
                raise ValueError("Extraction evidence span mismatch")
        return self


class DiscoveryRequest(StrictModel):
    source_definition_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    mode: Literal["MANUAL", "SCHEDULE_READY"] = "MANUAL"
    since: date
    until: date
    query: str = Field(default="", max_length=150)
    max_pages: int = Field(default=1, ge=1, le=5)
    page_size: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def window(self):
        if self.until < self.since or (self.until - self.since).days > 31:
            raise ValueError("Discovery window must be at most 31 days")
        return self


class DocumentRef(StrictModel):
    external_id: str
    url: str
    metadata: dict[str, str] = Field(default_factory=dict)


class DiscoveryPage(StrictModel):
    documents: list[DocumentRef]
    next_cursor: str | None = None


class Audience(StrictModel):
    code: str
    name: str
    country: str = "ES"
    nace_prefixes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class ScoreWeights(StrictModel):
    audience_relevance: float = 0.30
    financial_value: float = 0.10
    urgency: float = 0.15
    freshness: float = 0.10
    addressable_audience: float = 0.15
    shareability: float = 0.10
    authority_potential: float = 0.08
    brand_strategic_fit: float = 0.02

    @model_validator(mode="after")
    def valid_weights(self):
        values = list(self.model_dump().values())
        if any(v < 0 or v > 1 for v in values) or abs(sum(values) - 1) > 0.00001:
            raise ValueError("Weights must be nonnegative and sum to one")
        return self


class EditorialPolicy(StrictModel):
    schema_version: Literal[1] = 1
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    create_threshold: float = Field(default=45.0, ge=0, le=100)
    freshness_hours: int = Field(default=24, ge=1, le=168)
    content_mix: dict[str, float] = Field(default_factory=lambda: {"useful_opportunity": 1.0})
    repeat_window_days: int = Field(default=14, ge=1, le=90)


class RelevanceScore(StrictModel):
    schema_version: Literal[1] = 1
    eligibility: Literal["POTENTIALLY_ELIGIBLE", "INELIGIBLE", "UNKNOWN"]
    components: dict[str, float]
    weights: ScoreWeights
    final_score: float = Field(ge=0, le=100)
    reasons: list[str]
    scorer_version: str = "deterministic-1"
    semantic_score: float | None = None


class EditorialDecision(StrictModel):
    schema_version: Literal[1] = 1
    decision: Literal["CREATE_CONTENT", "WATCH", "IGNORE", "HUMAN_REVIEW"]
    reasons: list[str]
    recommended_angle: str
    content_history_ids: list[UUID] = Field(default_factory=list)
    mission_objective: str
    content_mix: dict[str, float]


class OpportunityWorkflowRequest(StrictModel):
    influencer_id: UUID
    mission_id: UUID
    audience_segment_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)


class ChangeEvent(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal[
        "NEW_OPPORTUNITY",
        "STATUS_CHANGED",
        "OPENING_DATE_CHANGED",
        "DEADLINE_CHANGED",
        "AMOUNT_CHANGED",
        "FUNDING_PERCENTAGE_CHANGED",
        "ELIGIBILITY_CHANGED",
        "DOCUMENT_CHANGED",
        "APPLICATION_OPENED",
        "APPLICATION_CLOSED",
    ]
    field: str
    old_value: object = None
    new_value: object = None
    materiality: Literal["HIGH", "LOW"]
