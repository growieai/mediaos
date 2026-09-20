"""Bounded, sequential HTTPS transport. External content is data, never instructions."""

import hashlib
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx


class SourceUnavailable(Exception):
    pass


class SourcePolicyError(Exception):
    pass


class SourceNotFound(SourceUnavailable):
    pass


class SourceCoolingDown(SourceUnavailable):
    def __init__(self, retry_at):
        super().__init__("Official source backoff has not elapsed")
        self.retry_at = retry_at


@dataclass(frozen=True)
class FetchResult:
    url: str
    body: str
    media_type: str
    captured_at: datetime
    checksum: str
    is_fixture: bool = False
    status_code: int = 200
    etag: str | None = None


_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}
_guard = threading.Lock()


class OfficialHTTP:
    def __init__(
        self,
        hosts,
        interval=1.5,
        recorder=None,
        cache=None,
        transport=None,
        sleep=time.sleep,
        cooldown=None,
    ):
        self.hosts = frozenset(hosts)
        self.interval = max(1.0, interval)
        self.recorder = recorder or (lambda **kwargs: None)
        self.cache = cache or (lambda url: None)
        self.cooldown = cooldown or (lambda host: None)
        self.sleep = sleep
        self.client = httpx.Client(
            timeout=httpx.Timeout(25.0, connect=10.0),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={
                "User-Agent": "GrowieMediaOS/0.2 (official-source research; sequential bounded requests)",
                "Accept": "application/json, application/xml;q=0.9, text/html;q=0.8",
            },
        )

    def close(self):
        self.client.close()

    def get(self, url: str) -> FetchResult:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname not in self.hosts
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or parts.fragment
        ):
            raise SourcePolicyError("URL is outside the official source allowlist")
        assert parts.hostname is not None
        cached = self.cache(url)
        if cached:
            return cached
        with _guard:
            lock = _locks.setdefault(parts.hostname, threading.Lock())
        for attempt in range(1, 4):
            started = time.monotonic()
            status = None
            retry_delay = 2 ** (attempt - 1)
            try:
                with lock:
                    retry_at = self.cooldown(parts.hostname)
                    if retry_at and retry_at > datetime.now(UTC):
                        raise SourceCoolingDown(retry_at)
                    self.sleep(
                        max(0, self.interval - (time.monotonic() - _last.get(parts.hostname, 0)))
                    )
                    _last[parts.hostname] = time.monotonic()
                    with self.client.stream("GET", url) as response:
                        status = response.status_code
                        if status in (429, 503):
                            value = response.headers.get("retry-after", "")
                            try:
                                retry_delay = max(retry_delay, int(value))
                            except ValueError:
                                if value:
                                    retry_delay = max(
                                        retry_delay,
                                        int(
                                            (
                                                parsedate_to_datetime(value) - datetime.now(UTC)
                                            ).total_seconds()
                                        ),
                                    )
                        response.raise_for_status()
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            data.extend(chunk)
                            if len(data) > 2_000_000:
                                raise SourcePolicyError("Official response exceeds size limit")
                        body = bytes(data).decode("utf-8", errors="strict")
                        result = FetchResult(
                            url=url,
                            body=body,
                            media_type=response.headers.get("content-type", ""),
                            captured_at=datetime.now(UTC),
                            checksum=hashlib.sha256(data).hexdigest(),
                            etag=response.headers.get("etag"),
                        )
                self.recorder(
                    url=url,
                    attempt=attempt,
                    status_code=status,
                    latency_ms=(time.monotonic() - started) * 1000,
                    retry_delay=None,
                    error=None,
                    result=result,
                )
                return result
            except (httpx.HTTPError, UnicodeError, ValueError, SourcePolicyError) as exc:
                retryable = isinstance(
                    exc, (httpx.TimeoutException, httpx.NetworkError)
                ) or status in (429, 500, 502, 503, 504)
                self.recorder(
                    url=url,
                    attempt=attempt,
                    status_code=status,
                    latency_ms=(time.monotonic() - started) * 1000,
                    retry_delay=retry_delay if retryable else None,
                    error=type(exc).__name__,
                    result=None,
                )
                if not retryable or attempt == 3 or retry_delay > 30:
                    # A long Retry-After is persisted; do not retry earlier than requested.
                    if status == 404:
                        raise SourceNotFound("Official resource not found") from None
                    raise SourceUnavailable(type(exc).__name__) from None
                self.sleep(retry_delay)
        raise SourceUnavailable("Attempts exhausted")
