from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class WorkflowStatus(StrEnum):
    DETECTED = "DETECTED"
    RESEARCHING = "RESEARCHING"
    VERIFIED = "VERIFIED"
    SELECTED = "SELECTED"
    BRIEFED = "BRIEFED"
    SCRIPTED = "SCRIPTED"
    QA = "QA"
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"


class SourceInput(BaseModel):
    source_id: str
    title: str
    source_url: HttpUrl | None = None
    source_type: Literal["official", "secondary", "manual_test"] = "manual_test"
    market: str = "ES"
    raw_text: str


class VerifiedFact(BaseModel):
    claim: str
    evidence: str
    source_id: str
    confidence: float = Field(ge=0, le=1)


class ResearchPack(BaseModel):
    research_pack_id: str
    topic: str
    market: str = "ES"
    audience: list[str]
    verified_facts: list[VerifiedFact]
    uncertain_facts: list[str] = Field(default_factory=list)
    implications: list[str]
    confidence: float = Field(ge=0, le=1)
    publishable: bool


class ContentBrief(BaseModel):
    brief_id: str
    research_pack_id: str
    influencer_id: str
    franchise: str
    audience: list[str]
    objective: str
    hook: str
    angle: str
    growie_association_level: int = Field(ge=0, le=5)
    format: Literal["carousel", "reel", "story", "post"] = "carousel"
    cta: str


class CarouselSlide(BaseModel):
    index: int
    headline: str
    body: str


class CarouselDraft(BaseModel):
    content_id: str
    brief_id: str
    caption: str
    slides: list[CarouselSlide]
    disclosure: str


class QAReport(BaseModel):
    status: Literal["APPROVE", "REVISE", "BLOCK"]
    fact_score: float = Field(ge=0, le=1)
    brand_score: float = Field(ge=0, le=1)
    policy_score: float = Field(ge=0, le=1)
    reasons: list[str]
    required_changes: list[str] = Field(default_factory=list)


class DemoRun(BaseModel):
    tenant_id: str
    influencer_id: str
    status: WorkflowStatus
    research: ResearchPack
    brief: ContentBrief
    draft: CarouselDraft
    qa: QAReport
