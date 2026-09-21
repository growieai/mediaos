import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
FIELDS = (
    "tenant_id",
    "workflow_run_id",
    "skill_run_id",
    "ingestion_run_id",
    "attempt",
    "state",
    "error_category",
)
ACCESS_LOGGERS = ("uvicorn.access", "gunicorn.access")
HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"}


def safe_access_fields(record: logging.LogRecord) -> dict:
    """Read method/status only; never interpolate request lines, paths, queries or headers."""
    if record.name not in ACCESS_LOGGERS:
        return {}
    method, status = None, None
    if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
        # Uvicorn: client, method, full_path, HTTP version, status.
        method, status = record.args[1], record.args[4]
    elif record.name == "gunicorn.access" and isinstance(record.args, dict):
        # Gunicorn atoms contain the entire request line and arbitrary headers.
        method, status = record.args.get("m"), record.args.get("s")
    result: dict[str, str | int] = {}
    if isinstance(method, str) and method in HTTP_METHODS:
        result["http_method"] = method
    if isinstance(status, str) and len(status) == 3 and status.isascii() and status.isdecimal():
        status = int(status)
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    return result


class SafeJsonFormatter(logging.Formatter):
    def format(self, record):
        # Deliberate allowlist: no request headers, raw payload, credentials or exception text.
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.msg if record.name == "mediaos" else record.name,
            "correlation_id": request_id.get(),
        }
        payload.update({field: getattr(record, field, None) for field in FIELDS})
        payload.update(safe_access_fields(record))
        return json.dumps(payload)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logging.getLogger("mediaos").handlers = [handler]
    logging.getLogger("mediaos").setLevel(logging.INFO)
    logging.getLogger("mediaos").propagate = False
    # Provider/CDN URLs may carry signed query credentials. Third-party HTTP
    # diagnostics use the same allowlisted formatter, even if debug is enabled.
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.WARNING)
        logger.propagate = False
    # Optional server access logs must not disclose OAuth codes, verification
    # tokens or signed media capabilities embedded in incoming URLs. Preserve
    # safe method/status and normal structured context without request targets.
    for name in ACCESS_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
