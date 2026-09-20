from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from app.models.schemas import StrictModel, TextBlock

Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
NonEmptyText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class VisualPalette(StrictModel):
    background: Color = "#F6F2EA"
    text: Color = "#182820"
    accent: Color = "#234D3C"


class VisualConfig(StrictModel):
    schema_version: Literal[1] = 1
    template_version: Literal["editorial-v1"] = "editorial-v1"
    display_name: NonEmptyText
    required_disclosure: NonEmptyText
    regular_font_path: NonEmptyText
    bold_font_path: NonEmptyText
    palette: VisualPalette = Field(default_factory=VisualPalette)
    width: Literal[1080] = 1080
    height: Literal[1350] = 1350
    margin: int = Field(default=72, ge=64, le=96)
    headline_font_size: int = Field(default=58, ge=48, le=72)
    body_font_size: int = Field(default=38, ge=34, le=48)
    cta_font_size: int = Field(default=32, ge=30, le=40)
    disclosure_font_size: int = Field(default=26, ge=26, le=32)
    identity_font_size: int = Field(default=32, ge=28, le=40)
    minimum_contrast_ratio: float = Field(default=4.5, ge=4.5, le=7.0)

    @model_validator(mode="after")
    def nonblank_identity(self):
        if not self.display_name.strip() or not self.required_disclosure.strip():
            raise ValueError("Display name and disclosure must not be whitespace only")
        return self


class RenderFinding(StrictModel):
    code: str
    severity: Literal["REVISION_REQUIRED", "BLOCKED"]
    field_path: str
    message: str
    slide_index: int | None = None


class TextLine(StrictModel):
    start: int
    end: int
    text: str
    hard_break: bool = False


class TextCoverage(StrictModel):
    field_path: str
    text: str
    text_sha256: str
    fact_ids: list[str] = Field(default_factory=list)
    placement: Literal["IMAGE", "CAPTION"] = "IMAGE"
    lines: list[TextLine] = Field(default_factory=list)
    exact_coverage: bool
    font_size: int | None = None
    bounds: tuple[int, int, int, int] | None = None


class RenderedSlide(StrictModel):
    index: int
    filename: str | None
    sha256: str | None
    width: Literal[1080] = 1080
    height: Literal[1350] = 1350
    text_coverage: list[TextCoverage]
    overflow: bool


class RenderManifest(StrictModel):
    schema_version: Literal[1] = 1
    renderer_version: Literal["pillow-editorial-v1"] = "pillow-editorial-v1"
    pillow_version: str
    status: Literal["PASS", "REVISION_REQUIRED", "BLOCKED"]
    draft_sha256: str
    visual_config_sha256: str
    font_sha256: dict[str, str]
    reference_sha256: str | None = None
    influencer_version_id: str
    language: str
    caption: TextBlock
    caption_coverage: TextCoverage
    slides: list[RenderedSlide]
    findings: list[RenderFinding]
