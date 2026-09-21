"""Strict contracts for silent, visibly labelled local concept previews."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.models.schemas import StrictModel

PathText = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SceneSpec(StrictModel):
    kind: Literal["PORTRAIT", "QUESTION", "CLOSING"]
    duration_seconds: int = Field(ge=3, le=8)
    kicker: str = Field(min_length=1, max_length=45)
    headline: str = Field(min_length=1, max_length=80)
    supporting: str = Field(min_length=1, max_length=100)
    caption: str = Field(min_length=1, max_length=220)

    @field_validator("kicker", "headline", "supporting", "caption")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Preview text must not be blank")
        return value


class ConceptSpec(StrictModel):
    schema_version: Literal[1] = 1
    preview_only: Literal[True] = True
    publishable: Literal[False] = False
    audio: Literal["NONE"] = "NONE"
    identity_status: Literal["PROPOSED"] = "PROPOSED"
    subject_name: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=100)
    disclosure: str = Field(min_length=1, max_length=160)
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920
    fps: Literal[24] = 24
    portrait_path: PathText
    regular_font_path: PathText
    bold_font_path: PathText
    scenes: list[SceneSpec] = Field(min_length=3, max_length=8)

    @field_validator("schema_version", "width", "height", "fps", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Preview numeric constants must be integers")
        return value

    @field_validator("preview_only", "publishable", mode="before")
    @classmethod
    def exact_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("Preview markers must be booleans")
        return value

    @field_validator(
        "subject_name",
        "title",
        "disclosure",
        "portrait_path",
        "regular_font_path",
        "bold_font_path",
    )
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Preview text and paths must not be blank")
        return value

    @model_validator(mode="after")
    def bounded_duration(self):
        if sum(scene.duration_seconds for scene in self.scenes) > 30:
            raise ValueError("A concept preview must not exceed 30 seconds")
        return self


class PreviewManifest(StrictModel):
    schema_version: Literal[1] = 1
    preview_only: Literal[True] = True
    publishable: Literal[False] = False
    audio: Literal["NONE"] = "NONE"
    identity_status: Literal["PROPOSED"] = "PROPOSED"
    provider: Literal["deterministic-local"] = "deterministic-local"
    model: None = None
    cost: Literal[0] = 0
    renderer_version: Literal["pillow-concept-v1"] = "pillow-concept-v1"
    pillow_version: str = Field(min_length=1, max_length=100)
    spec: ConceptSpec
    input_sha256: dict[str, Digest]
    ffmpeg_version: str = Field(min_length=1, max_length=1000)
    output_sha256: Digest
    output_filename: Literal["video.mp4"] = "video.mp4"
    frame_count: int = Field(ge=216, le=720)
    duration_seconds: int = Field(ge=9, le=30)

    @field_validator("schema_version", "cost", mode="before")
    @classmethod
    def exact_integer(cls, value):
        return ConceptSpec.exact_integer(value)

    @field_validator("preview_only", "publishable", mode="before")
    @classmethod
    def exact_boolean(cls, value):
        return ConceptSpec.exact_boolean(value)

    @model_validator(mode="after")
    def exact_render_contract(self):
        duration = sum(scene.duration_seconds for scene in self.spec.scenes)
        if self.duration_seconds != duration or self.frame_count != duration * self.spec.fps:
            raise ValueError(
                "Manifest duration and frame count must match the concept specification"
            )
        if set(self.input_sha256) != {
            "spec",
            "portrait_path",
            "regular_font_path",
            "bold_font_path",
        }:
            raise ValueError("Manifest must pin the exact specification, portrait and font inputs")
        return self
