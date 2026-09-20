"""Structured JSON logs (PROJECT_BRIEF M6), correlated with the active
OTel trace.

`correlation_id` is the active span's trace id (app.telemetry.tracing.
current_trace_id) when one exists, so a log line and the trace it happened
inside can be joined on one field; outside any span (process startup, the
worker's idle-poll log) it is null rather than a fabricated id.

`configure_logging()` replaces the ad-hoc `logging.basicConfig(...)` calls
in app.worker.main with one JSON formatter used everywhere, so a line looks
like:

  {"timestamp": "...", "level": "INFO", "logger": "worker", "message": "...",
   "correlation_id": "4bf9...", "job_id": "...", ...extra fields}

Call sites pass extra fields the normal `logging` way
(`logger.info("...", extra={"job_id": job.id})`); anything under
`_RESERVED` (the standard LogRecord attributes) is never duplicated into
the JSON body.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.telemetry.tracing import current_trace_id

# Standard attributes every LogRecord already carries — excluded from the
# "extra fields" pass-through so e.g. `logger.info(..., extra={"module": x})`
# can't silently shadow the record's own `module`.
_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": current_trace_id(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger. Idempotent — safe to
    call once per process (worker/API entrypoints) even if a test harness
    already touched logging config."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
