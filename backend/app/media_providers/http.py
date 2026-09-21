"""Single-attempt fixed-host HTTP. Never replay an uncertain generation submission."""

import json
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

T = TypeVar("T", bound=BaseModel)


class ProviderError(Exception):
    def __init__(
        self,
        category: str,
        retryable: bool = False,
        request_id: str | None = None,
        retry_after_seconds: int | None = None,
    ):
        self.category, self.retryable, self.request_id = category, retryable, request_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__(category)


def parse(
    schema: type[T], value: Any, request_id: str | None = None, *, ambiguous: bool = False
) -> T:
    try:
        return schema.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise ProviderError(
            "UNKNOWN_OUTCOME" if ambiguous else "INVALID_OUTPUT", request_id=request_id
        ) from None


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", value):
        raise ProviderError("INVALID_REQUEST")
    return value


def credential(value: SecretStr) -> str:
    result = value.get_secret_value()
    if not result or len(result) > 1024 or not result.isascii() or any(c.isspace() for c in result):
        raise ProviderError("AUTHENTICATION")
    return result


class Transport:
    def __init__(self, host: str, transport: httpx.BaseTransport | None = None):
        if host not in {"api.elevenlabs.io", "api.heygen.com", "api.higgsfield.ai"}:
            raise ProviderError("INVALID_REQUEST")
        self.host, self.transport = host, transport

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        *,
        payload: Any = None,
        files: Any = None,
        ambiguous: bool = False,
        limit: int = 1_000_000,
    ) -> tuple[dict[str, Any], str | None]:
        if not path.startswith("/") or any(c in path for c in ("?", "#", "\\", "..")):
            raise ProviderError("INVALID_REQUEST")
        request_id = None
        started = time.monotonic()
        request_headers = httpx.Headers(headers)
        request_headers["Accept-Encoding"] = "identity"
        try:
            with httpx.Client(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(60, connect=10, read=60, write=30, pool=5),
            ) as client:
                with client.stream(
                    method,
                    f"https://{self.host}{path}",
                    headers=request_headers,
                    json=payload,
                    files=files,
                ) as response:
                    raw_id = response.headers.get("request-id") or response.headers.get(
                        "x-correlation-id"
                    )
                    request_id = (
                        raw_id
                        if raw_id
                        and re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", raw_id)
                        and all(raw_id not in value for value in headers.values())
                        else None
                    )
                    status = response.status_code
                    if not 200 <= status < 300:
                        category = {
                            400: "INVALID_REQUEST",
                            401: "AUTHENTICATION",
                            402: "PAYMENT_REQUIRED",
                            403: "PAYMENT_REQUIRED"
                            if self.host == "api.higgsfield.ai"
                            else "AUTHENTICATION",
                            404: "NOT_FOUND",
                            422: "INVALID_REQUEST",
                            429: "RATE_LIMITED",
                        }.get(
                            status,
                            "UNKNOWN_OUTCOME"
                            if ambiguous and status >= 500
                            else "PROVIDER_UNAVAILABLE",
                        )
                        retry_after = response.headers.get("retry-after", "")
                        raise ProviderError(
                            category,
                            status == 429 or (method == "GET" and status >= 500),
                            request_id,
                            min(int(retry_after), 86400)
                            if re.fullmatch(r"[0-9]{1,8}", retry_after)
                            else None,
                        )
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        # A decoder may consume arbitrarily many raw chunks
                        # without yielding bytes to the deadline/size checks.
                        raise ProviderError(
                            "UNKNOWN_OUTCOME" if ambiguous else "INVALID_OUTPUT",
                            request_id=request_id,
                        )
                    body = bytearray()
                    # Check each decoded network chunk; buffering 64 KiB lets a
                    # trickling response evade the total deadline indefinitely.
                    for chunk in response.iter_bytes():
                        if time.monotonic() - started > 120:
                            raise ProviderError(
                                "UNKNOWN_OUTCOME" if ambiguous else "NETWORK",
                                method == "GET",
                                request_id,
                            )
                        body.extend(chunk)
                        if len(body) > limit:
                            raise ProviderError(
                                "UNKNOWN_OUTCOME" if ambiguous else "RESPONSE_TOO_LARGE",
                                request_id=request_id,
                            )
                    try:
                        result = json.loads(body)
                    except (ValueError, UnicodeError, RecursionError):
                        raise ProviderError(
                            "UNKNOWN_OUTCOME" if ambiguous else "INVALID_OUTPUT",
                            request_id=request_id,
                        ) from None
                    if not isinstance(result, dict):
                        raise ProviderError(
                            "UNKNOWN_OUTCOME" if ambiguous else "INVALID_OUTPUT",
                            request_id=request_id,
                        )
                    return result, request_id
        except httpx.HTTPError:
            raise ProviderError(
                "UNKNOWN_OUTCOME" if ambiguous else "NETWORK", method == "GET", request_id
            ) from None
