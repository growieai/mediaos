"""One bounded HTTPS operation against a privileged, exact destination configuration."""

import hashlib
import hmac
import ipaddress
import json
import re
import socket
import time
from queue import Empty, Queue
from threading import BoundedSemaphore, Thread
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr, ValidationError

from app.conversion.deadline import DeadlineHTTPTransport
from app.conversion.delivery_schemas import SignedReceipt
from app.db.repository import canonical_hash


class HandoffError(Exception):
    """Only bounded categories escape; never provider text, credentials or contact data."""


_DNS_SLOTS = BoundedSemaphore(4)


def _public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _resolve_public(host: str, timeout: float = 5.0) -> str:
    """DNS may block outside HTTP timeout handling; bound waiting and concurrent resolvers."""
    if not _DNS_SLOTS.acquire(blocking=False):
        raise HandoffError("UNKNOWN_OUTCOME")
    result: Queue = Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            result.put(None)
        finally:
            _DNS_SLOTS.release()

    Thread(target=resolve, daemon=True, name="mediaos-handoff-dns").start()
    try:
        addresses = result.get(timeout=timeout)
    except Empty:
        raise HandoffError("UNKNOWN_OUTCOME") from None
    if not addresses or any(not _public_address(str(row[4][0])) for row in addresses):
        raise HandoffError("INVALID_DESTINATION")
    return str(addresses[0][4][0])


def signing_key(secret: SecretStr) -> bytes:
    value = secret.get_secret_value()
    if not 32 <= len(value) <= 512 or not value.isascii() or any(c.isspace() for c in value):
        raise HandoffError("INVALID_CREDENTIAL")
    return hashlib.sha256(value.encode()).digest()


def signature(payload: dict, secret: SecretStr) -> str:
    return hmac.new(
        signing_key(secret), canonical_hash(payload).encode(), hashlib.sha256
    ).hexdigest()


def validate_endpoint(endpoint: str) -> str:
    if not re.fullmatch(r"https://[a-z0-9][a-z0-9.-]+(?::443)?/[A-Za-z0-9/_-]+", endpoint):
        raise HandoffError("INVALID_DESTINATION")
    parsed = urlsplit(endpoint)
    hostname = parsed.hostname or ""
    if hostname in {"localhost", "metadata.google.internal"} or hostname.endswith(".localhost"):
        raise HandoffError("INVALID_DESTINATION")
    try:
        if not _public_address(hostname):
            raise HandoffError("INVALID_DESTINATION")
    except ValueError:
        pass
    return hostname


def _strict_object(raw: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("Non-finite JSON")

    result = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError("Object required")
    return result


class HandoffTransport:
    def __init__(self, endpoint: str, secret: SecretStr, *, transport=None):
        self.host = validate_endpoint(endpoint)
        self.endpoint, self.secret, self.transport = endpoint.rstrip("/"), secret, transport
        signing_key(secret)

    def exchange(self, payload: dict, *, reconcile: bool = False) -> SignedReceipt:
        payload_hash = canonical_hash(payload)
        delivery_id = payload["delivery_id"]
        endpoint = self.endpoint + (f"/receipts/{delivery_id}" if reconcile else "")
        request_body = (
            {"delivery_id": delivery_id, "payload_hash": payload_hash} if reconcile else payload
        )
        headers = {
            "User-Agent": "MediaOS-ConsentHandoff/1.0",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "X-MediaOS-Signature": signature(request_body, self.secret),
            "X-MediaOS-Payload-Hash": payload_hash,
            "Idempotency-Key": delivery_id,
            "Host": self.host,
        }
        extensions = {}
        try:
            if self.transport is None:
                # Pin one validated public address, preserving TLS hostname verification/SNI.
                ip = _resolve_public(self.host)
                authority = f"[{ip}]" if ":" in ip else ip
                endpoint = f"https://{authority}" + urlsplit(endpoint).path
                extensions = {"sni_hostname": self.host}
            started = time.monotonic()
            with httpx.Client(
                transport=self.transport or DeadlineHTTPTransport(),
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(10, connect=5, pool=2),
            ) as client:
                with client.stream(
                    "GET" if reconcile else "POST",
                    endpoint,
                    headers=headers,
                    json=None if reconcile else {"payload": payload, "payload_hash": payload_hash},
                    extensions=extensions,
                ) as response:
                    if (
                        response.status_code != 200
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        raise HandoffError("UNKNOWN_OUTCOME")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 16384 or time.monotonic() - started > 20:
                            raise HandoffError("UNKNOWN_OUTCOME")
                    signed = SignedReceipt.model_validate_json(
                        json.dumps(_strict_object(bytes(raw))), strict=True
                    )
            receipt = signed.receipt.model_dump(mode="json")
            if (
                str(signed.receipt.delivery_id) != delivery_id
                or signed.receipt.payload_hash != payload_hash
                or not hmac.compare_digest(signature(receipt, self.secret), signed.signature)
                or (signed.receipt.status == "NOT_FOUND" and not reconcile)
                or (signed.receipt.status == "RECEIVED" and payload["operation"] != "SEND")
                or (signed.receipt.status == "REVOKED" and payload["operation"] != "REVOKE")
            ):
                raise HandoffError("UNKNOWN_OUTCOME")
            return signed
        except (httpx.HTTPError, OSError, ValidationError, ValueError, RecursionError):
            raise HandoffError("UNKNOWN_OUTCOME") from None
