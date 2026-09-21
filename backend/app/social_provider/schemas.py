"""Typed Instagram transport contracts, independent of workflow and tenant policy."""

import ipaddress
import re
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

Identifier = Annotated[str, Field(pattern=r"^[0-9]{1,64}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, le=9007199254740991)]
Scope = Literal[
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_comments",
    "instagram_business_manage_insights",
]
MetricName = Literal["reach", "saved", "shares", "comments", "follows", "views", "likes"]
T = TypeVar("T")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class WireModel(BaseModel):
    """Provider extras stay in provenance; only declared fields enter logic."""

    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False)


def public_https(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            not isinstance(value, str)
            or len(value) > 8192
            or re.search(r"[\s\x00-\x1f\x7f]", value)
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
            or "." not in parsed.hostname
            or parsed.hostname.endswith((".localhost", ".local", ".internal", ".test"))
        ):
            raise ValueError("Public HTTPS URL required")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", parsed.hostname
            ):
                raise ValueError("Public hostname required") from None
            return value
        raise ValueError("IP literals are not accepted")
    except (TypeError, AttributeError):
        raise ValueError("Public HTTPS URL required") from None


class InstagramSettings(Contract):
    api_version: str = Field(pattern=r"^v[0-9]{1,3}\.0$")
    app_id: Identifier
    app_secret: SecretStr = Field(repr=False)
    redirect_uri: str
    media_host_allowlist: tuple[str, ...] = ()

    _redirect = field_validator("redirect_uri")(public_https)

    @field_validator("media_host_allowlist")
    @classmethod
    def hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 10 or len(set(values)) != len(values):
            raise ValueError("At most ten distinct exact media hosts required")
        for host in values:
            if host != host.lower() or urlsplit(public_https(f"https://{host}/")).hostname != host:
                raise ValueError("Exact lowercase media host required")
        return values


class Observation(Contract, Generic[T]):
    schema_version: Literal[1] = 1
    provider: Literal["META_INSTAGRAM"] = "META_INSTAGRAM"
    api_version: str
    endpoint: str
    captured_at: datetime
    payload: T
    raw: dict = Field(repr=False)
    raw_hash: Digest
    response_hash: Digest
    redacted: bool
    request_id: str | None = None


class TokenGrant(Contract):
    access_token: SecretStr = Field(repr=False)
    user_id: Identifier | None = None
    expires_in: int | None = Field(default=None, gt=0, le=31536000)
    token_type: str | None = Field(default=None, max_length=64)
    permissions: list[str] = Field(default_factory=list, max_length=30)
    response_hash: Digest
    request_id: str | None = None


class Profile(Contract):
    id: Identifier
    username: str = Field(min_length=1, max_length=64)
    account_type: Literal["BUSINESS", "MEDIA_CREATOR", "CREATOR"]


class CreatedObject(WireModel):
    id: Identifier


class ContainerStatus(WireModel):
    id: Identifier
    status_code: Literal["EXPIRED", "ERROR", "FINISHED", "IN_PROGRESS", "PUBLISHED"]
    status: str | None = Field(default=None, max_length=5000)


class Media(WireModel):
    id: Identifier
    media_type: Literal["IMAGE", "VIDEO", "CAROUSEL_ALBUM"]
    caption: str | None = Field(default=None, max_length=10000)
    permalink: str | None = Field(default=None, max_length=8192)
    timestamp: str = Field(min_length=1, max_length=64)
    username: str | None = Field(default=None, max_length=64)
    owner: CreatedObject | None = None
    like_count: Count | None = None
    comments_count: Count | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_has_timezone(cls, value: str) -> str:
        if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("Provider timestamp must include a timezone")
        return value


class Comment(WireModel):
    id: Identifier
    text: str = Field(max_length=10000)
    timestamp: str | None = Field(default=None, max_length=64)
    username: str | None = Field(default=None, max_length=64)
    parent_id: Identifier | None = None
    media: CreatedObject | None = None


class Page(Contract, Generic[T]):
    data: list[T] = Field(max_length=100)
    after: str | None = Field(default=None, max_length=4096)
    has_more: bool


class Insights(Contract):
    """Only exact lifetime scalar counters are normalized; unavailable stays null."""

    media_id: Identifier
    metrics: dict[str, Count | None]
    definitions: dict[str, str]


class CommentEvent(Contract):
    schema_version: Literal[1] = 1
    account_id: Identifier
    comment_id: Identifier
    media_id: Identifier
    text: str = Field(max_length=10000)
    sender_id: Identifier | None = None
    username: str | None = Field(default=None, max_length=64)
    occurred_at: datetime
    event_hash: Digest


class CommentWebhook(Contract):
    schema_version: Literal[1] = 1
    raw_hash: Digest
    events: list[CommentEvent] = Field(max_length=500)
    ignored_changes: int = Field(ge=0)
