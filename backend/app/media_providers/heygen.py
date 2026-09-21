from typing import Literal

import httpx
from pydantic import SecretStr

from .http import ProviderError, Transport, credential, identifier, parse
from .schemas import Identifier, JobStatus, SubmittedJob, UploadResult, WireModel


class Created(WireModel):
    video_id: Identifier


class CreatedEnvelope(WireModel):
    data: Created


class Video(WireModel):
    id: Identifier
    status: Literal["pending", "processing", "completed", "failed"]
    video_url: str | None = None
    duration: float | None = None


class VideoEnvelope(WireModel):
    data: Video


class HeyGen:
    def __init__(self, api_key: SecretStr, *, transport: httpx.BaseTransport | None = None):
        self.api_key, self.http = api_key, Transport("api.heygen.com", transport)

    def headers(self) -> dict[str, str]:
        return {"X-Api-Key": credential(self.api_key)}

    def upload(self, data: bytes, mime: str, idempotency_key: str) -> UploadResult:
        if (
            not data
            or len(data) > 32_000_000
            or mime not in {"image/png", "image/jpeg", "audio/mpeg", "audio/wav"}
        ):
            raise ProviderError("INVALID_REQUEST")
        headers = {**self.headers(), "Idempotency-Key": identifier(idempotency_key)}
        body, rid = self.http.request(
            "POST", "/v3/assets", headers, files={"file": ("input", data, mime)}, ambiguous=True
        )
        # The wire envelope includes a public URL which is intentionally discarded.
        value = body.get("data")
        if isinstance(value, dict):
            value = {k: value[k] for k in ("asset_id", "mime_type", "size_bytes") if k in value}
        result = parse(UploadResult, value, rid, ambiguous=True)
        if result.size_bytes != len(data) or result.mime_type != mime:
            raise ProviderError("UNKNOWN_OUTCOME", request_id=rid)
        return result

    def create(self, image_asset_id: str, audio_asset_id: str) -> SubmittedJob:
        body, rid = self.http.request(
            "POST",
            "/v3/videos",
            self.headers(),
            payload={
                "type": "image",
                "image": {"type": "asset_id", "asset_id": identifier(image_asset_id)},
                "audio_asset_id": identifier(audio_asset_id),
                "resolution": "1080p",
                "aspect_ratio": "9:16",
                "engine": {"type": "avatar_iv"},
            },
            ambiguous=True,
        )
        result = parse(CreatedEnvelope, body, rid, ambiguous=True)
        return SubmittedJob(request_id=result.data.video_id, status="QUEUED")

    def poll(self, video_id: str) -> JobStatus:
        video_id = identifier(video_id)
        body, rid = self.http.request("GET", f"/v3/videos/{video_id}", self.headers())
        result = parse(VideoEnvelope, body, rid).data
        if result.id != video_id:
            raise ProviderError("INVALID_OUTPUT", request_id=rid)
        return parse(
            JobStatus,
            {
                "request_id": video_id,
                "status": {
                    "pending": "QUEUED",
                    "processing": "RUNNING",
                    "completed": "COMPLETED",
                    "failed": "FAILED",
                }[result.status],
                "output_url": result.video_url,
                "duration_seconds": result.duration,
                "error_category": "GENERATION_FAILED" if result.status == "failed" else None,
            },
            rid,
        )
