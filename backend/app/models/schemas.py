from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkflowStatus(StrEnum):
    CREATED = "CREATED"
    SOURCE_CAPTURED = "SOURCE_CAPTURED"
    RESEARCHING = "RESEARCHING"
    RESEARCH_COMPLETE = "RESEARCH_COMPLETE"
    BRIEFING = "BRIEFING"
    BRIEF_COMPLETE = "BRIEF_COMPLETE"
    CONTENT_GENERATING = "CONTENT_GENERATING"
    CONTENT_COMPLETE = "CONTENT_COMPLETE"
    QA_RUNNING = "QA_RUNNING"
    REVISION_REQUIRED = "REVISION_REQUIRED"
    BLOCKED = "BLOCKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    FAILED = "FAILED"


class GrantFields(StrictModel):
    amount: str | None = None
    currency: str | None = None
    eligible_geography: list[str] = Field(default_factory=list)
    eligible_business_type: list[str] = Field(default_factory=list)
    opening_date: date | None = None
    deadline: date | None = None
    required_evidence: list[str] = Field(default_factory=list)
    eligibility_status: Literal["VERIFIED", "UNCLEAR"] = "UNCLEAR"
    field_evidence: dict[str, str] = Field(default_factory=dict)


class EvidenceInput(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    statement: ShortText
    fact_type: Literal["GENERAL", "GRANT"] = "GENERAL"
    grant: GrantFields | None = None

    @model_validator(mode="after")
    def grant_classification(self):
        if self.grant is not None and self.fact_type != "GRANT":
            raise ValueError("Grant metadata requires GRANT classification")
        return self


class SourceInput(StrictModel):
    source_type: Literal["MANUAL", "OFFICIAL", "SECONDARY"] = "MANUAL"
    origin: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    canonical_url: str | None = None
    title: ShortText
    publisher: ShortText
    raw_content: Annotated[str, StringConstraints(min_length=1, max_length=100000)]
    captured_at: datetime
    classification: Literal["PRIMARY", "SECONDARY", "INTERNAL"] = "PRIMARY"
    is_fixture: bool
    metadata: dict[str, str] = Field(default_factory=dict)
    evidence: list[EvidenceInput] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def spans(self):
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
        seen: set[tuple[int, int]] = set()
        for fact in self.evidence:
            span = (fact.start, fact.end)
            if span in seen:
                raise ValueError("duplicate evidence span")
            seen.add(span)
            if not 0 <= fact.start < fact.end <= len(self.raw_content):
                raise ValueError("evidence span must be inside raw_content")
            if not fact.statement.strip():
                raise ValueError("evidence statement cannot be whitespace only")
            if self.raw_content[fact.start : fact.end].strip(" ") != fact.statement:
                raise ValueError("statement must exactly match the source evidence span")
        return self


class CreateRun(StrictModel):
    influencer_id: UUID
    mission_id: UUID
    idempotency_key: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    source: SourceInput


class CharacterConfig(StrictModel):
    schema_version: Literal[1] = 1
    persona: str
    voice: str
    visual_policy: str
    brand_policy: str
    language: str
    audience: list[str]
    franchise: str
    objective: str
    tone: str
    disclosure: str
    headline: str
    cta: str
    cta_type: Literal["SAVE", "READ_SOURCE", "NONE"] = "READ_SOURCE"
    brand_association_level: int = Field(ge=0, le=5)
    max_brand_association_level: int = Field(ge=0, le=5)
    min_slides: int = Field(ge=1, le=20)
    max_slides: int = Field(ge=1, le=20)
    creative_allowlist: list[str]


class Fact(StrictModel):
    id: UUID
    source_snapshot_id: UUID
    start: int
    end: int
    statement: str
    verification_status: Literal["VERIFIED", "UNVERIFIED"]
    confidence: float = Field(ge=0, le=1)
    fact_type: Literal["GENERAL", "GRANT"]
    grant: GrantFields | None = None

    @model_validator(mode="after")
    def grant_classification(self):
        if self.grant is not None and self.fact_type != "GRANT":
            raise ValueError("Grant metadata requires GRANT classification")
        return self


class OpportunityResearch(StrictModel):
    schema_version: Literal[1] = 1
    opportunity_id: UUID
    opportunity_version_id: UUID
    canonical_external_id: str
    audience_segment_id: UUID
    relevance_score_id: UUID
    editorial_decision_id: UUID
    source_snapshot_ids: list[UUID]
    opportunity_fact_ids: list[UUID]
    uncertainty: list[str]
    conflict_ids: list[UUID]
    opening_date: date | None = None
    closing_date: date | None = None
    fresh_until: datetime
    recommended_angle: str


class ResearchPack(StrictModel):
    schema_version: Literal[1] = 1
    facts: list[Fact]
    verification_status: Literal["VERIFIED", "UNVERIFIED"]
    opportunity_context: OpportunityResearch | None = None


class ContentBrief(StrictModel):
    schema_version: Literal[1] = 1
    allowed_fact_ids: list[UUID]
    audience: list[str]
    franchise: str
    format: Literal["CAROUSEL"] = "CAROUSEL"
    objective: str
    tone: str
    cta_type: str
    brand_association_level: int = Field(ge=0, le=5)
    constraints: dict[str, int]


class TextBlock(StrictModel):
    kind: Literal["FACT", "CREATIVE"]
    text: Annotated[str, StringConstraints(max_length=5000)]
    fact_ids: list[UUID] = Field(default_factory=list)


class CarouselSlide(StrictModel):
    index: int = Field(ge=1)
    headline: TextBlock
    body: TextBlock


class CarouselDraft(StrictModel):
    schema_version: Literal[1] = 1
    type: Literal["CAROUSEL"] = "CAROUSEL"
    influencer_version_id: UUID
    language: str
    brand_association_level: int = Field(ge=0, le=5)
    slides: list[CarouselSlide] = Field(min_length=1, max_length=20)
    caption: TextBlock
    cta: TextBlock
    disclosure: str


class QAFinding(StrictModel):
    code: str
    category: Literal["EVIDENCE", "CONTENT", "WORKFLOW"]
    severity: Literal["BLOCKED", "REVISION_REQUIRED"]
    message: str


class QAReport(StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["PASS", "REVISION_REQUIRED", "BLOCKED"]
    findings: list[QAFinding]


class ApprovalInput(StrictModel):
    asset_version_id: UUID
    research_version_id: UUID
    qa_report_id: UUID
    comment: Annotated[str, StringConstraints(max_length=2000)] | None = None


class VerificationInput(StrictModel):
    decision: Literal["VERIFIED", "REJECTED"]
    comment: ShortText


class RunResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    state: WorkflowStatus
    source_snapshot_id: UUID | None
    research_version_id: UUID | None
    brief_id: UUID | None
    asset_version_id: UUID | None
    qa_report_id: UUID | None
    created_at: datetime
    updated_at: datetime
