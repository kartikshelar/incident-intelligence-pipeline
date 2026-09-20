"""app.telemetry.tracing: exporter selection, span shape, error status."""

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.settings import Settings
from app.telemetry import tracing


@pytest.fixture(autouse=True)
def _reset_global_tracer_provider() -> None:
    """Each test gets its own TracerProvider: app.telemetry.tracing.configure
    is normally idempotent per process (worker/API call it once at startup),
    but tests need a fresh one per case to inspect only their own spans."""
    tracing.reset_for_tests()
    yield
    tracing.reset_for_tests()


def _settings(**overrides: object) -> Settings:
    return Settings(llm_provider=None, extraction_model=None, extraction_thinking=None, **overrides)  # type: ignore[arg-type]


def _configure_in_memory() -> InMemorySpanExporter:
    """Configure a real provider, then swap its processor for one backed by
    an in-memory exporter so tests can inspect finished spans directly."""
    settings = _settings(otel_exporter="console")
    provider = tracing.configure(settings)
    exporter = InMemorySpanExporter()
    assert isinstance(provider, TracerProvider)
    provider._active_span_processor._span_processors = ()  # drop the console processor
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def test_console_exporter_is_the_default() -> None:
    settings = _settings()
    assert settings.otel_exporter == "console"
    provider = tracing.configure(settings)
    processor = provider._active_span_processor._span_processors[0]
    assert isinstance(processor.span_exporter, ConsoleSpanExporter)


def test_configure_is_idempotent_within_a_process() -> None:
    settings = _settings()
    first = tracing.configure(settings)
    second = tracing.configure(settings)
    assert first is second
    assert len(first._active_span_processor._span_processors) == 1


def test_otlp_exporter_requires_an_endpoint() -> None:
    settings = _settings(otel_exporter="otlp", otel_exporter_endpoint=None)
    with pytest.raises(ValueError, match="APP_OTEL_EXPORTER_ENDPOINT"):
        tracing.configure(settings)


def test_unknown_exporter_is_a_configuration_error() -> None:
    settings = _settings(otel_exporter="carrier-pigeon")
    with pytest.raises(ValueError, match="carrier-pigeon"):
        tracing.configure(settings)


def test_span_records_attributes_and_closes() -> None:
    exporter = _configure_in_memory()
    with tracing.span("unit.test", {"a": 1}) as s:
        s.set_attribute("b", "two")

    (finished,) = exporter.get_finished_spans()
    assert finished.name == "unit.test"
    assert finished.attributes["a"] == 1
    assert finished.attributes["b"] == "two"
    assert finished.status.status_code == StatusCode.UNSET


def test_span_marks_error_status_on_exception_and_still_propagates() -> None:
    exporter = _configure_in_memory()
    with pytest.raises(RuntimeError, match="boom"):
        with tracing.span("unit.failing"):
            raise RuntimeError("boom")

    (finished,) = exporter.get_finished_spans()
    assert finished.status.status_code == StatusCode.ERROR
    assert finished.events[0].name == "exception"


def test_nested_spans_share_one_trace_id() -> None:
    exporter = _configure_in_memory()
    with tracing.span("outer"):
        with tracing.span("inner"):
            pass

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert spans["outer"].context.trace_id == spans["inner"].context.trace_id
    assert spans["inner"].parent.span_id == spans["outer"].context.span_id


def test_current_trace_id_matches_the_active_span() -> None:
    _configure_in_memory()
    assert tracing.current_trace_id() is None
    with tracing.span("outer") as s:
        trace_id = tracing.current_trace_id()
        assert trace_id == format(s.get_span_context().trace_id, "032x")
    assert tracing.current_trace_id() is None
