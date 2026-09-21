"""No database: production access logging must not expose OAuth or capability URLs."""

import io
import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.observability import SafeJsonFormatter, configure_logging, request_id


def test_real_creator_emits_structured_completion(monkeypatch):
    from app.ai import runtime

    @contextmanager
    def saved_transaction():
        yield

    connection = SimpleNamespace(begin=saved_transaction, execute=lambda *args: None)
    monkeypatch.setattr(
        runtime, "Repository", lambda *args: SimpleNamespace(require=lambda role: None)
    )
    configure_logging()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeJsonFormatter())
    monkeypatch.setattr(runtime.log, "handlers", [handler])
    correlation = request_id.set("correlation-test")
    try:
        runtime._finish(
            connection,
            "tenant-test",
            "secret-not-logged",
            {
                "id": "attempt-test",
                "workflow_run_id": "workflow-test",
                "skill_run_id": "skill-test",
                "attempt": 2,
            },
            error="RATE_LIMIT",
            retry=True,
            delay=3,
        )
    finally:
        request_id.reset(correlation)
    result = json.loads(stream.getvalue())
    assert result["event"] == "text_ai_finished"
    assert result["tenant_id"] == "tenant-test"
    assert result["workflow_run_id"] == "workflow-test"
    assert result["skill_run_id"] == "skill-test"
    assert result["attempt"] == 2 and result["state"] == "RATE_LIMIT"
    assert result["correlation_id"] == "correlation-test"
    assert "secret-not-logged" not in stream.getvalue()


@pytest.fixture(scope="session", autouse=True)
def database():
    """Override the integration-only schema reset for these pure logging tests."""


@pytest.mark.parametrize(
    "path",
    [
        "/v1/social/oauth/callback?code=private-oauth-code&state=private-state",
        "/v1/social/webhook?hub.verify_token=private-verification-token&hub.challenge=123",
        "/v1/social/media/private-signed-capability",
        "/v1/workflow-runs/private-id?unexpected=private-query",
    ],
)
def test_uvicorn_access_excludes_secret_request_targets(path):
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("private-client-ip", "GET", path, "1.1", 200),
        None,
    )
    record.tenant_id = "tenant-reference"
    record.workflow_run_id = "workflow-reference"
    record.skill_run_id = "skill-reference"
    record.attempt = 2
    correlation = request_id.set("request-correlation")
    try:
        rendered = SafeJsonFormatter().format(record)
    finally:
        request_id.reset(correlation)
    assert "private" not in rendered and path not in rendered
    assert json.loads(rendered) == {
        "timestamp": json.loads(rendered)["timestamp"],
        "level": "INFO",
        "event": "uvicorn.access",
        "correlation_id": "request-correlation",
        "tenant_id": "tenant-reference",
        "workflow_run_id": "workflow-reference",
        "skill_run_id": "skill-reference",
        "ingestion_run_id": None,
        "attempt": 2,
        "state": None,
        "error_category": None,
        "http_method": "GET",
        "http_status": 200,
    }


def test_gunicorn_atoms_cannot_leak_query_headers_or_request_line():
    atoms = {
        "m": "POST",
        "s": "403",
        "r": "POST /callback?code=private-code HTTP/1.1",
        "U": "/social/media/private-capability",
        "q": "?state=private-state",
        "{authorization}i": "Bearer private-token",
        "h": "private-client-ip",
    }
    record = logging.LogRecord(
        "gunicorn.access", logging.INFO, "", 0, "%(r)s %(s)s", (atoms,), None
    )
    rendered = SafeJsonFormatter().format(record)
    assert "private" not in rendered and "Bearer" not in rendered
    result = json.loads(rendered)
    assert result["http_method"] == "POST" and result["http_status"] == 403


@pytest.mark.parametrize(
    "method,status", [("GET private-secret", 200), ("GET", "200 private-secret"), ("GET", True)]
)
def test_access_fields_are_allowlisted_not_arbitrary_strings(method, status):
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "private-secret",
        ("ip", method, "private-url", "1.1", status),
        None,
    )
    result = json.loads(SafeJsonFormatter().format(record))
    assert "private" not in json.dumps(result)
    assert result.get("http_method") in (None, "GET")
    assert result.get("http_status") in (None, 200)


def test_configure_logging_sanitizes_enabled_access_log_handlers_and_exceptions():
    names = ("mediaos", "httpx", "httpcore", "uvicorn.access", "gunicorn.access")
    previous = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in names
    }
    output = io.StringIO()
    try:
        configure_logging()
        for name in ("uvicorn.access", "gunicorn.access"):
            logger = logging.getLogger(name)
            logger.handlers[0].setStream(output)
            assert not logger.propagate
            try:
                raise RuntimeError("private-stack-trace")
            except RuntimeError:
                logger.exception("private-secret %s", "private-secret-arg")
        logged = output.getvalue()
        assert "uvicorn.access" in logged and "gunicorn.access" in logged
        assert "private" not in logged and "Traceback" not in logged
    finally:
        for name, (handlers, level, propagate) in previous.items():
            logger = logging.getLogger(name)
            logger.handlers, logger.level, logger.propagate = handlers, level, propagate
