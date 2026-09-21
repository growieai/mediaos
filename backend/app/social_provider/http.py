"""Bounded single-attempt transport. Provider errors never expose response text or URLs."""

import hashlib
import json
import logging
import re
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

_private_http = ContextVar("instagram_private_http", default=False)


class _SuppressPrivateHTTP(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Token-exchange GET URLs contain credentials. Do not let dependency debug
        # or info logging emit any request/response details during this call.
        return not _private_http.get()


for _logger_name in (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
):
    logging.getLogger(_logger_name).addFilter(_SuppressPrivateHTTP())


class SocialProviderError(Exception):
    def __init__(
        self,
        category: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
        retry_after_seconds: int | None = None,
        http_status: int | None = None,
        provider_code: int | None = None,
    ):
        self.category = category
        self.retryable = retryable
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds
        self.http_status = http_status
        self.provider_code = provider_code
        self.max_attempts = 3 if retryable else 1
        super().__init__(category)

    def retry_delay(self, completed_attempts: int) -> int | None:
        """Caller must persist attempts and next-attempt time; transport never sleeps/replays."""
        if not self.retryable or not 1 <= completed_attempts < self.max_attempts:
            return None
        return max(min(2**completed_attempts, 60), self.retry_after_seconds or 0)


def credential(value: SecretStr) -> str:
    if not isinstance(value, SecretStr):
        raise SocialProviderError("AUTHENTICATION")
    result = value.get_secret_value()
    if (
        not 1 <= len(result) <= 8192
        or not result.isascii()
        or any(character.isspace() or ord(character) < 33 for character in result)
    ):
        raise SocialProviderError("AUTHENTICATION")
    return result


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,64}", value):
        raise SocialProviderError("INVALID_REQUEST")
    return value


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def strict_json(raw: bytes) -> dict:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("Non-finite JSON number")

    result = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError("Object expected")
    return result


def sanitize(value: Any, secrets: tuple[str, ...]) -> Any:
    """Retain factual provider data, remove credential-bearing paging URLs and secrets."""
    if isinstance(value, dict):
        return {
            sanitize(key, secrets): "[REDACTED]"
            if key.lower()
            in {"access_token", "client_secret", "authorization", "code", "next", "previous"}
            else sanitize(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


class Response:
    def __init__(self, payload: dict, raw: bytes, request_id: str | None):
        self.payload = payload
        self.response_hash = hashlib.sha256(raw).hexdigest()
        self.request_id = request_id
        self.captured_at = datetime.now(UTC)
        self.redacted = False


class Transport:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def request(
        self,
        method: str,
        host: str,
        path: str,
        *,
        token: SecretStr | None = None,
        params: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        secrets: tuple[str, ...] = (),
        safe_read: bool = False,
    ) -> Response:
        if (
            host not in {"api.instagram.com", "graph.instagram.com"}
            or method not in {"GET", "POST"}
            or not re.fullmatch(r"/[A-Za-z0-9_./]+", path)
            or ".." in path
            or "//" in path
            or (safe_read and method != "GET")
        ):
            raise SocialProviderError("INVALID_REQUEST")
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "MediaOS/1.0 InstagramConnector",
        }
        if token is not None:
            token_value = credential(token)
            headers["Authorization"] = f"Bearer {token_value}"
            secrets = (*secrets, token_value)
        params, form = params or {}, form or {}
        if len(urlencode(params).encode()) > 20000 or len(urlencode(form).encode()) > 100000:
            raise SocialProviderError("INVALID_REQUEST")
        request_id = None
        status = None
        started = time.monotonic()
        private_context = _private_http.set(True)
        try:
            with httpx.Client(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(20, connect=10, read=20, write=20, pool=5),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            ) as client:
                with client.stream(
                    method,
                    f"https://{host}{path}",
                    headers=headers,
                    params=params,
                    data=form if method == "POST" else None,
                ) as response:
                    status = response.status_code
                    candidate = response.headers.get("x-fb-trace-id")
                    request_id = (
                        candidate
                        if candidate
                        and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", candidate)
                        and not any(secret in candidate for secret in secrets)
                        else None
                    )
                    ambiguous = not safe_read
                    if 300 <= status < 400:
                        raise SocialProviderError(
                            "UNKNOWN_OUTCOME" if ambiguous else "REDIRECT_REJECTED",
                            http_status=status,
                        )
                    encoding = response.headers.get("content-encoding", "identity")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                    if encoding != "identity" or (
                        200 <= status < 300 and content_type != "application/json"
                    ):
                        raise SocialProviderError(
                            "UNKNOWN_OUTCOME" if ambiguous else "INVALID_OUTPUT",
                            http_status=status,
                        )
                    length = response.headers.get("content-length", "")
                    if length.isdecimal() and int(length) > 2_000_000:
                        raise SocialProviderError(
                            "UNKNOWN_OUTCOME" if ambiguous else "RESPONSE_TOO_LARGE",
                            http_status=status,
                        )
                    body = bytearray()
                    # Do not buffer many small network chunks before checking the
                    # total deadline while a publication guard holds DB locks.
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if time.monotonic() - started > 45:
                            raise SocialProviderError(
                                "UNKNOWN_OUTCOME" if ambiguous else "NETWORK", retryable=safe_read
                            )
                        if len(body) > 2_000_000:
                            raise SocialProviderError(
                                "UNKNOWN_OUTCOME" if ambiguous else "RESPONSE_TOO_LARGE",
                                http_status=status,
                            )
                    try:
                        payload = strict_json(bytes(body))
                    except (ValueError, UnicodeError, RecursionError):
                        payload = None
                    if status >= 400 or (payload is not None and "error" in payload):
                        error = payload.get("error") if payload else None
                        code = error.get("code") if isinstance(error, dict) else None
                        code = code if type(code) is int and 0 <= code <= 2**31 - 1 else None
                        transient = code in {1, 2} or (
                            isinstance(error, dict) and error.get("is_transient") is True
                        )
                        retry_header = response.headers.get("retry-after", "")
                        retry_after = (
                            min(int(retry_header), 86400)
                            if re.fullmatch(r"[0-9]{1,8}", retry_header)
                            else None
                        )
                        category = (
                            "UNKNOWN_OUTCOME"
                            if not safe_read and (status >= 500 or transient)
                            else "AUTHENTICATION"
                            if status == 401 or code in {102, 190}
                            else "PERMISSION_DENIED"
                            if status == 403 or code in {10, 200}
                            else "RATE_LIMITED"
                            if status == 429 or code in {4, 17, 32, 613}
                            else "NOT_FOUND"
                            if status == 404
                            else "PROVIDER_UNAVAILABLE"
                            if status >= 500 or transient
                            else "INVALID_REQUEST"
                        )
                        raise SocialProviderError(
                            category,
                            retryable=category == "RATE_LIMITED"
                            or (safe_read and category == "PROVIDER_UNAVAILABLE"),
                            request_id=request_id,
                            retry_after_seconds=retry_after,
                            http_status=status,
                            provider_code=code,
                        )
                    if payload is None or not 200 <= status < 300:
                        raise SocialProviderError(
                            "UNKNOWN_OUTCOME" if ambiguous else "INVALID_OUTPUT",
                            request_id=request_id,
                        )
                    return Response(payload, bytes(body), request_id)
        except httpx.HTTPError:
            raise SocialProviderError(
                "NETWORK" if safe_read else "UNKNOWN_OUTCOME",
                retryable=safe_read,
                request_id=request_id,
                http_status=status,
            ) from None
        finally:
            _private_http.reset(private_context)
