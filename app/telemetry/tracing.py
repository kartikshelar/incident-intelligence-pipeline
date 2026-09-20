"""OpenTelemetry setup: one TracerProvider per process, exporter configurable
via env (APP_OTEL_EXPORTER), console by default so the full trace works with
no external collector (PROJECT_BRIEF M6).

Span shape, one trace per document:

  ingest_source                    app.worker.main.process_ingest
    job.claim                        the claim itself (app.worker.main.run_once)
    fetch
    parse
  extract_document                 app.worker.main.process_extract
    job.claim
    extract.attempt (x attempts)     one per validate-and-retry loop iteration
      llm.call                        the model request (retries included:
                                       attempt 2+ IS the retry, same span kind)
      validation                      schema validation of that attempt's output;
                                       a failure is recorded on this span, not
                                       raised out of it
  route_pending                    app.review.routing (post-extraction pass)

Every span above is created through `span()` below so exporter failures
(a collector that is down, a bad OTLP endpoint) can never fail a job: OTel's
own BatchSpanProcessor already swallows exporter errors, but `configure()`
also wraps provider construction so a bad APP_OTEL_EXPORTER_ENDPOINT fails
at startup with a clear message rather than silently losing every span.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.util._once import Once

from app.settings import Settings

_TRACER_NAME = "incident-intel"

EXPORTERS = ("console", "otlp")

_configured = False


def _build_exporter(settings: Settings) -> SpanExporter:
    if settings.otel_exporter == "console":
        return ConsoleSpanExporter()
    if settings.otel_exporter == "otlp":
        if not settings.otel_exporter_endpoint:
            raise ValueError(
                "APP_OTEL_EXPORTER=otlp requires APP_OTEL_EXPORTER_ENDPOINT "
                "(the collector's OTLP/HTTP traces endpoint)"
            )
        # Imported lazily: the otlp exporter package pulls in extra
        # dependencies that a console-only process (the default) shouldn't
        # need to have working to start.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(endpoint=settings.otel_exporter_endpoint)
    raise ValueError(
        f"APP_OTEL_EXPORTER={settings.otel_exporter!r} is not valid "
        f"(expected one of {', '.join(EXPORTERS)})"
    )


def configure(settings: Settings) -> TracerProvider:
    """Build and register the process-wide TracerProvider. Idempotent: a
    second call returns the already-configured global provider rather than
    stacking a second exporter onto it (worker and API each call this once
    at startup; tests may call it more than once across the suite)."""
    global _configured
    provider = trace.get_tracer_provider()
    if _configured and isinstance(provider, TracerProvider):
        return provider

    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    exporter = _build_exporter(settings)
    # Console output is meant to be read as it happens (the "no collector"
    # path); a background batch would buffer it away from a `docker compose
    # logs` tail. OTLP gets the real batching behavior.
    processor = (
        SimpleSpanProcessor(exporter)
        if settings.otel_exporter == "console"
        else BatchSpanProcessor(exporter)
    )
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _configured = True
    return provider


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def reset_for_tests() -> None:
    """Undo `configure()`'s global registration so a test can install its
    own TracerProvider. OTel's `set_tracer_provider` is a set-once guard
    (`opentelemetry.trace._TRACER_PROVIDER_SET_ONCE`), not just a None
    check, so both that guard and `_configured` must be cleared together —
    production code never calls this; only tests that need span isolation
    per case do (worker and API each configure exactly once, at startup).
    """
    global _configured
    _configured = False
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()


def current_trace_id() -> str | None:
    """The active span's trace id as a 32-hex string, or None outside any
    span — used to stamp the same id onto structured logs (app.telemetry.logctx)."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


@contextlib.contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span]:
    """Start a span, mark it ERROR on any exception (still propagated), and
    always close it. Use for every stage/attempt/call listed in the module
    docstring so the shape is uniform end to end.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as current:
        if attributes:
            current.set_attributes(attributes)
        try:
            yield current
        except Exception as exc:
            current.set_status(Status(StatusCode.ERROR, str(exc)))
            current.record_exception(exc)
            raise
