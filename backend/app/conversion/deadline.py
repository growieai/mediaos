"""A synchronous socket deadline includes HTTP response headers, body, writes and TLS."""

import socket
import time

import httpcore
import httpx


def _remaining(deadline: float, timeout: float | None) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise httpcore.ReadTimeout("Handoff total deadline exceeded")
    return min(remaining, timeout) if timeout is not None else remaining


class DeadlineStream(httpcore.NetworkStream):
    def __init__(self, stream: httpcore.NetworkStream, deadline: float):
        self.stream, self.deadline = stream, deadline

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            return self.stream.read(max_bytes, _remaining(self.deadline, timeout))
        except (httpcore.TimeoutException, httpcore.NetworkError):
            self.close()
            raise

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        # SyncBackend.write has an inner partial-send loop using one inactivity
        # timeout. Own that loop so a receiver cannot prolong it by reading slowly.
        sock = self.stream.get_extra_info("socket")
        if sock is None:
            self.close()
            raise httpcore.WriteError("A synchronous socket is required")
        try:
            while buffer:
                sock.settimeout(_remaining(self.deadline, timeout))
                sent = sock.send(buffer)
                if sent <= 0:
                    raise httpcore.WriteError("Handoff connection closed")
                buffer = buffer[sent:]
        except (httpcore.TimeoutException, httpcore.NetworkError):
            self.close()
            raise
        except socket.timeout:
            self.close()
            raise httpcore.WriteTimeout("Handoff write timeout") from None
        except OSError:
            self.close()
            raise httpcore.WriteError("Handoff write failed") from None

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            return DeadlineStream(
                self.stream.start_tls(
                    ssl_context, server_hostname, _remaining(self.deadline, timeout)
                ),
                self.deadline,
            )
        except (httpcore.TimeoutException, httpcore.NetworkError):
            self.close()
            raise

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    def close(self):
        self.stream.close()


class DeadlineBackend(httpcore.NetworkBackend):
    def __init__(self, deadline: float, backend=None):
        self.deadline, self.backend = deadline, backend or httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return DeadlineStream(
            self.backend.connect_tcp(
                host, port, _remaining(self.deadline, timeout), local_address, socket_options
            ),
            self.deadline,
        )


class ResponseStream(httpx.SyncByteStream):
    def __init__(self, response):
        self.response = response

    def __iter__(self):
        try:
            yield from self.response.iter_stream()
        except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError):
            self.response.close()
            raise httpx.TransportError("Handoff response failed") from None

    def close(self):
        self.response.close()


class DeadlineHTTPTransport(httpx.BaseTransport):
    def __init__(self, seconds: float = 20, *, backend=None):
        tls_context = httpcore.default_ssl_context()
        self.pool = httpcore.ConnectionPool(
            network_backend=DeadlineBackend(time.monotonic() + seconds, backend),
            ssl_context=tls_context,
            max_connections=1,
            max_keepalive_connections=0,
            http2=False,
            retries=0,
        )

    def handle_request(self, request):
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = self.pool.handle_request(core_request)
        except (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError):
            self.pool.close()
            raise httpx.TransportError("Handoff connection failed") from None
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=ResponseStream(response),
            extensions=response.extensions,
        )

    def close(self):
        self.pool.close()
