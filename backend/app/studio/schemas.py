from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.models.schemas import StrictModel

Label = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Objective = Annotated[str, StringConstraints(min_length=1, max_length=500)]
CategoryID = Literal[
    "business", "beauty", "food", "fitness", "technology", "travel", "education", "lifestyle"
]


class OnboardingDraftRequest(StrictModel):
    category_id: CategoryID
    language: Literal["en", "es"]
    tone: Literal["CLEAR", "WARM", "BOLD"]
    name: Label | None = None
    audience: list[Label] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def bounded_plain_text(self):
        values = [*self.audience, *([self.name] if self.name is not None else [])]
        if any(
            value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)
            for value in values
        ):
            raise ValueError("Studio text must be trimmed plain text without control characters")
        if len(self.audience) != len(set(self.audience)):
            raise ValueError("Audience labels must be unique")
        return self


class OnboardingSuggestion(StrictModel):
    id: Literal["practical", "explainer", "community"]
    label: Label
    name: Label
    audience: list[Label] = Field(min_length=1, max_length=8)
    objective: Annotated[str, StringConstraints(min_length=10, max_length=500)]


class OnboardingDrafts(StrictModel):
    schema_version: Literal[1] = 1
    provider: Literal["mock"] = "mock"
    mode: Literal["MOCK"] = "MOCK"
    cost: Literal[0] = 0
    notice: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    suggestions: list[OnboardingSuggestion] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def distinct_directions(self):
        if {item.id for item in self.suggestions} != {"practical", "explainer", "community"}:
            raise ValueError("Draft directions must be distinct")
        return self


class CreateInfluencer(StrictModel):
    idempotency_key: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    name: Label
    category_id: CategoryID
    language: Literal["en", "es"]
    tone: Literal["CLEAR", "WARM", "BOLD"]
    audience: list[Label] = Field(min_length=1, max_length=8)
    objective: Objective

    @model_validator(mode="after")
    def bounded_plain_text(self):
        values = [self.name, self.objective, *self.audience]
        if any(
            value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)
            for value in values
        ):
            raise ValueError("Studio text must be trimmed plain text without control characters")
        if len(self.audience) != len(set(self.audience)):
            raise ValueError("Audience labels must be unique")
        return self


class ContentCategory(StrictModel):
    id: CategoryID
    name: str
    description: str
    accent: str


class CategoryCatalog(StrictModel):
    schema_version: Literal[1] = 1
    categories: list[ContentCategory]


class StudioInfluencer(StrictModel):
    id: UUID
    name: str
    category_id: CategoryID | None
    language: str
    tone: str
    audience: list[str]
    objective: str
    mission_id: UUID
    influencer_version_id: UUID
    character_config_version_id: UUID
    visual_config_version_id: UUID | None
    opportunity_discovery: bool
    portrait_available: bool
    created_at: datetime


class InfluencerCatalog(StrictModel):
    schema_version: Literal[1] = 1
    creation_enabled: bool
    influencers: list[StudioInfluencer]


class StudioWorkflow(StrictModel):
    id: UUID
    influencer_id: UUID
    mission_id: UUID
    state: str
    title: str
    created_at: datetime
    updated_at: datetime
    asset_version_id: UUID | None
    research_version_id: UUID | None
    qa_report_id: UUID | None
    source_snapshot_id: UUID | None


class StudioCounts(StrictModel):
    influencers: int
    workflow_runs: int
    awaiting_approval: int


class StudioCapabilities(StrictModel):
    creator_creation_enabled: bool
    social_connect_enabled: bool
    social_publish_enabled: bool
    ai_mode: Literal["mock", "configured"]


class StudioOverview(StrictModel):
    schema_version: Literal[1] = 1
    tenant_id: UUID
    tenant_name: str
    counts: StudioCounts
    capabilities: StudioCapabilities
    workflows: list[StudioWorkflow]
