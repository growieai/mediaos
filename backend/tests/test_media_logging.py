"""HTTP diagnostics must not disclose signed media URLs or credentials."""

import io
import logging

import pytest

from app.observability import configure_logging


@pytest.fixture(scope="session", autouse=True)
def database():
    yield None


def test_http_library_diagnostics_remain_redacted_when_debug_enabled():
    names = ("mediaos", "httpx", "httpcore")
    previous = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in names
    }
    try:
        configure_logging()
        output = io.StringIO()
        for name in ("httpx", "httpcore"):
            logger = logging.getLogger(name)
            logger.handlers[0].setStream(output)
            logger.setLevel(logging.DEBUG)
            logger.warning("https://files.heygen.ai/private.mp4?signature=do-not-log-signed-secret")
            logger.debug("Authorization: Key do-not-log-api-secret")
        logged = output.getvalue()
        assert "httpx" in logged and "httpcore" in logged
        assert "do-not-log" not in logged and "https://" not in logged
    finally:
        for name, (handlers, level, propagate) in previous.items():
            logger = logging.getLogger(name)
            logger.handlers, logger.level, logger.propagate = handlers, level, propagate
