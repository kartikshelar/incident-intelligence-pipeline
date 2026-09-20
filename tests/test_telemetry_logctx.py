"""app.telemetry.logctx: JSON formatting and trace correlation."""

import json
import logging

from app.settings import Settings
from app.telemetry import tracing
from app.telemetry.logctx import JSONFormatter


def _record(level: int = logging.INFO, msg: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formats_as_json_with_expected_fields() -> None:
    payload = json.loads(JSONFormatter().format(_record(msg="hi", job_id="abc")))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["message"] == "hi"
    assert payload["job_id"] == "abc"
    assert "timestamp" in payload
    assert payload["correlation_id"] is None  # no active span


def test_correlation_id_matches_the_active_trace(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tracing.reset_for_tests()
    settings = Settings(llm_provider=None, extraction_model=None, extraction_thinking=None)
    tracing.configure(settings)

    with tracing.span("unit.test") as s:
        payload = json.loads(JSONFormatter().format(_record()))
        assert payload["correlation_id"] == format(s.get_span_context().trace_id, "032x")

    tracing.reset_for_tests()


def test_reserved_fields_are_not_duplicated() -> None:
    payload = json.loads(JSONFormatter().format(_record()))
    # 'name' is a reserved LogRecord attribute (the logger name) surfaced
    # under our own 'logger' key, not passed through verbatim a second time.
    assert "name" not in payload


def test_exception_info_is_included() -> None:
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(JSONFormatter().format(record))
    assert "kaboom" in payload["exception"]
