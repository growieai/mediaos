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
        return json.dumps(payload)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logging.getLogger("mediaos").handlers = [handler]
    logging.getLogger("mediaos").setLevel(logging.INFO)
    logging.getLogger("mediaos").propagate = False
