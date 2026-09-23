"""Deterministic typed transport fixtures; no government/provider/destination network."""

import hashlib
import json
import socket
import time
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

import httpcore
import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.conversion.deadline import DeadlineHTTPTransport
from app.conversion.delivery_schemas import BusinessIdentityInput, SignedReceipt
from app.conversion.transport import (
    HandoffError,
    HandoffTransport,
    _resolve_public,
    signature,
    validate_endpoint,
)
from app.db.repository import canonical_hash


@pytest.fixture(scope="session", autouse=True)
def database():
    """Override the destructive integration-only fixture."""


SECRET = SecretStr("test-only-receipt-secret-never-use-in-production")


def identity_payload(**changes):
    return {
        "schema_version": 1,
        "idempotency_key": str(uuid4()),
        "business_reference": "business-1",
        "legal_name": "Example Test Business",
        "country": "ES",
        "registry_reference": "registry:123",
        "evidence_origin": "internal:reviewed-registration",
        "evidence_sha256": hashlib.sha256(b"test identity evidence").hexdigest(),
        "mode": "MANUAL",
        "retain_until": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        **changes,
    }


def signed_response(payload, status="RECEIVED"):
    receipt = {
        "schema_version": 1,
        "delivery_id": payload["delivery_id"],
        "payload_hash": canonical_hash(payload),
        "status": status,
        "receipt_reference": "test-receipt:1",
        "audit_completed": False,
    }
    return {"receipt": receipt, "signature": signature(receipt, SECRET)}


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.org/handoff",
        "https://127.0.0.1/handoff",
        "https://localhost/handoff",
        "https://example.org/handoff?secret=x",
        "https://user:pass@example.org/handoff",
        "https://example.org:8080/handoff",
        "https://example.org/../internal",
        "https://[::1]/handoff",
        "https://example.org/handoff#fragment",
    ],
)
def test_destination_rejects_unsafe_addresses(endpoint):
    with pytest.raises(HandoffError):
        validate_endpoint(endpoint)


@pytest.mark.parametrize(
    "changes",
    [
        {"registry_reference": "person@example.org"},
        {"mode": "VERIFIED"},
        {"country": "Spain"},
        {"legal_name": " "},
        {"evidence_sha256": "invented"},
        {"retain_until": "2026-09-23T00:00:00"},
    ],
)
def test_identity_inputs_fail_closed(changes):
    with pytest.raises(ValidationError):
        BusinessIdentityInput.model_validate_json(
            json.dumps(identity_payload(**changes)), strict=True
        )


def test_signed_transport_uses_exact_payload_and_never_leaks_key():
    payload = {
        "schema_version": 1,
        "delivery_id": str(uuid4()),
        "operation": "SEND",
        "subject_reference": "opaque:subject",
    }
    seen = []

    def handler(request):
        seen.append(request)
        assert json.loads(request.content) == {
            "payload": payload,
            "payload_hash": canonical_hash(payload),
        }
        assert request.headers["X-MediaOS-Signature"] == signature(payload, SECRET)
        assert SECRET.get_secret_value() not in str(request.headers) + request.content.decode()
        return httpx.Response(200, json=signed_response(payload))

    result = HandoffTransport(
        "https://example.org/handoff", SECRET, transport=httpx.MockTransport(handler)
    ).exchange(payload)
    assert result.receipt.status == "RECEIVED" and not result.receipt.audit_completed
    assert len(seen) == 1 and seen[0].method == "POST"


@pytest.mark.parametrize(
    "failure",
    [
        "signature",
        "payload_hash",
        "delivery_id",
        "audit",
        "redirect",
        "timeout",
        "oversize",
        "duplicate",
        "not_found",
    ],
)
def test_uncertain_or_mismatched_receipt_never_replays(failure):
    payload = {"delivery_id": str(uuid4()), "operation": "SEND"}
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private server text")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
        if failure == "oversize":
            return httpx.Response(200, content=b"a" * 16385)
        if failure == "duplicate":
            return httpx.Response(200, content=b'{"signature":"x","signature":"y"}')
        value = signed_response(payload)
        if failure == "signature":
            value["signature"] = "0" * 64
        else:
            value["receipt"][
                {
                    "payload_hash": "payload_hash",
                    "delivery_id": "delivery_id",
                    "audit": "audit_completed",
                    "not_found": "status",
                }[failure]
            ] = {
                "payload_hash": "0" * 64,
                "delivery_id": str(uuid4()),
                "audit": True,
                "not_found": "NOT_FOUND",
            }[failure]
        return httpx.Response(200, json=value)

    with pytest.raises(HandoffError, match="UNKNOWN_OUTCOME"):
        HandoffTransport(
            "https://example.org/handoff", SECRET, transport=httpx.MockTransport(handler)
        ).exchange(payload)
    assert len(calls) == 1


def test_reconciliation_is_signed_get_and_not_found_is_not_delivery():
    payload = {"delivery_id": str(uuid4()), "operation": "SEND"}

    def handler(request):
        assert request.method == "GET" and not request.content
        assert request.url.path.endswith("/receipts/" + payload["delivery_id"])
        return httpx.Response(200, json=signed_response(payload, "NOT_FOUND"))

    result = HandoffTransport(
        "https://example.org/handoff", SECRET, transport=httpx.MockTransport(handler)
    ).exchange(payload, reconcile=True)
    assert result.receipt.status == "NOT_FOUND"
    assert SignedReceipt.model_validate_json(result.model_dump_json(), strict=True) == result


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "169.254.169.254", "224.0.0.1", "192.168.0.1", "::1"]
)
def test_public_dns_resolution_rejects_any_private_or_special_address(monkeypatch, address):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", (address, 443))]
    )
    with pytest.raises(HandoffError, match="INVALID_DESTINATION"):
        _resolve_public("example.org")


def test_dns_wait_is_bounded_without_starting_an_http_request(monkeypatch):
    release = Event()

    def resolve(*args, **kwargs):
        release.wait(2)
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    try:
        with pytest.raises(HandoffError, match="UNKNOWN_OUTCOME"):
            _resolve_public("example.org", timeout=0.01)
    finally:
        release.set()


def test_total_http_deadline_closes_socket_even_while_headers_keep_arriving():
    client_socket, server_socket = socket.socketpair()
    socket_closed, request_seen = Event(), Event()
    server_socket.settimeout(2)

    class TestStream(httpcore.NetworkStream):
        def read(self, max_bytes, timeout=None):
            client_socket.settimeout(timeout)
            try:
                return client_socket.recv(max_bytes)
            except socket.timeout:
                raise httpcore.ReadTimeout("Test read deadline") from None

        def start_tls(self, *args, **kwargs):
            # Socket pair is deliberately a local offline fixture, not real TLS.
            return self

        def get_extra_info(self, info):
            return client_socket if info == "socket" else None

        def close(self):
            socket_closed.set()
            client_socket.close()

    class TestBackend(httpcore.NetworkBackend):
        calls = 0

        def connect_tcp(self, *args, **kwargs):
            self.calls += 1
            return TestStream()

    def server():
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                data += server_socket.recv(4096)
            assert data.startswith(b"POST ")
            request_seen.set()
            # An inactivity timeout alone never fires: each byte arrives quickly.
            for byte in b"HTTP/1.1 200 OK\r\nX-Trickle: " + b"a" * 2000:
                server_socket.sendall(bytes([byte]))
                time.sleep(0.01)
        except OSError:
            pass
        finally:
            server_socket.close()

    backend = TestBackend()
    transport = DeadlineHTTPTransport(seconds=1, backend=backend)
    worker = Thread(target=server, daemon=True)
    worker.start()
    start = time.monotonic()
    try:
        with pytest.raises(HandoffError, match="UNKNOWN_OUTCOME"):
            HandoffTransport(
                "https://example.org/handoff",
                SECRET,
                transport=transport,
            ).exchange({"delivery_id": str(uuid4()), "operation": "SEND"})
        assert request_seen.is_set() and socket_closed.is_set()
        assert time.monotonic() - start < 3
        assert backend.calls == 1
    finally:
        client_socket.close()
        server_socket.close()
        worker.join(2)
    assert not worker.is_alive()
