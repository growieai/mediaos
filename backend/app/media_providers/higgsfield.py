from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx
from pydantic import SecretStr, ValidationError

from .http import ProviderError, Transport, credential, identifier, parse
from .schemas import (
    CostEstimate,
    Identifier,
    JobStatus,
    KlingInput,
    SubmittedJob,
    WanInput,
    WireModel,
)

KLING = "kling-video/v3.0/pro/image-to-video"
WAN = "wan/v2.7/image-to-video"
STATES = {
    "queued": "QUEUED",
    "in_progress": "RUNNING",
    "completed": "COMPLETED",
    "failed": "FAILED",
    "nsfw": "BLOCKED",
    "canceled": "CANCELED",
}


class Video(WireModel):
    url: str


class Job(WireModel):
    request_id: Identifier
    status: Literal["queued", "in_progress", "completed", "failed", "nsfw", "canceled"]
    video: Video | None = None


class Estimate(WireModel):
    credits: str
    usd: str


class Higgsfield:
    def __init__(
        self,
        key_id: SecretStr,
        key_secret: SecretStr,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.key_id, self.key_secret = key_id, key_secret
        self.http = Transport("api.higgsfield.ai", transport)

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Key {credential(self.key_id)}:{credential(self.key_secret)}"}

    def parameters(self, model: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if model not in (KLING, WAN):
            raise ProviderError("INVALID_REQUEST")
        try:
            return (
                (KlingInput if model == KLING else WanInput)
                .model_validate(parameters)
                .model_dump(exclude_none=True)
            )
        except (ValidationError, ValueError, TypeError):
            raise ProviderError("INVALID_REQUEST") from None

    def estimate(self, model: str, parameters: dict[str, Any]) -> CostEstimate:
        payload = self.parameters(model, parameters)
        body, rid = self.http.request("POST", f"/estimate/{model}", self.headers(), payload=payload)
        result = parse(Estimate, body, rid)
        try:
            values = {"credits": Decimal(result.credits), "usd": Decimal(result.usd)}
        except InvalidOperation:
            raise ProviderError("INVALID_OUTPUT", request_id=rid) from None
        return parse(CostEstimate, values, rid)

    def submit(self, model: str, parameters: dict[str, Any]) -> SubmittedJob:
        payload = self.parameters(model, parameters)
        body, rid = self.http.request(
            "POST", f"/{model}", self.headers(), payload=payload, ambiguous=True
        )
        result = parse(Job, body, rid, ambiguous=True)
        if result.status not in ("queued", "in_progress"):
            raise ProviderError("UNKNOWN_OUTCOME", request_id=result.request_id)
        return parse(
            SubmittedJob,
            {"request_id": result.request_id, "status": STATES[result.status]},
            rid,
            ambiguous=True,
        )

    def poll(self, request_id: str) -> JobStatus:
        request_id = identifier(request_id)
        body, rid = self.http.request("GET", f"/requests/{request_id}/status", self.headers())
        result = parse(Job, body, rid)
        if result.request_id != request_id:
            raise ProviderError("INVALID_OUTPUT", request_id=rid)
        return parse(
            JobStatus,
            {
                "request_id": request_id,
                "status": STATES[result.status],
                "output_url": result.video.url if result.video else None,
                "error_category": "GENERATION_FAILED"
                if result.status == "failed"
                else "MODERATION_BLOCKED"
                if result.status == "nsfw"
                else None,
            },
            rid,
        )
