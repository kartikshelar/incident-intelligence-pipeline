"""One trace per document, end to end: ingest -> parse -> extract -> route,
with the job claim and every LLM call as spans (PROJECT_BRIEF M6)."""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import Engine

from app.api.main import app
from app.settings import Settings
from app.telemetry import tracing
from app.worker import main as worker
from tests.fake_llm import FakeLLMClient, valid_output_json

client = TestClient(app)

HTML = (
    b"<html><head><title>Outage</title></head>"
    b"<body><article>Detailed incident narrative goes here.</article></body></html>"
)


@pytest.fixture
def spans() -> InMemorySpanExporter:
    tracing.reset_for_tests()
    settings = Settings(llm_provider=None, extraction_model=None, extraction_thinking=None)
    provider = tracing.configure(settings)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    tracing.reset_for_tests()


@respx.mock
def test_ingest_job_produces_a_trace_with_fetch_and_parse_spans(
    engine: Engine, spans: InMemorySpanExporter
) -> None:
    url = "https://example.com/postmortem-otel"
    respx.get(url).mock(
        return_value=httpx.Response(200, content=HTML, headers={"content-type": "text/html"})
    )
    response = client.post("/sources", json={"url": url})
    assert response.status_code == 201

    assert worker.run_once() is True

    names = [s.name for s in spans.get_finished_spans()]
    assert "job.claim" in names
    assert "job.ingest" in names
    assert "ingest_source" in names
    assert "fetch" in names
    assert "parse" in names

    # One trace: every span from this job shares a trace id.
    trace_ids = {s.context.trace_id for s in spans.get_finished_spans()}
    assert len(trace_ids) == 1


@respx.mock
def test_extract_job_produces_llm_call_and_validation_spans(
    engine: Engine, spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://example.com/postmortem-otel-2"
    respx.get(url).mock(
        return_value=httpx.Response(200, content=HTML, headers={"content-type": "text/html"})
    )
    fake = FakeLLMClient([valid_output_json()])
    monkeypatch.setattr(worker, "get_llm_client", lambda: fake)

    client.post("/sources", json={"url": url})
    assert worker.run_once() is True  # ingest
    spans.clear()

    assert worker.run_once() is True  # extract

    names = [s.name for s in spans.get_finished_spans()]
    assert "job.extract" in names
    assert "extract_document" in names
    assert "extract.attempt" in names
    assert "llm.call" in names
    assert "validation" in names
    assert "route_pending" in names

    trace_ids = {s.context.trace_id for s in spans.get_finished_spans()}
    assert len(trace_ids) == 1


@respx.mock
def test_validation_failure_is_recorded_on_its_own_span(
    engine: Engine, spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.fake_llm import invalid_output_json

    url = "https://example.com/postmortem-otel-3"
    respx.get(url).mock(
        return_value=httpx.Response(200, content=HTML, headers={"content-type": "text/html"})
    )
    fake = FakeLLMClient([invalid_output_json(), valid_output_json()])
    monkeypatch.setattr(worker, "get_llm_client", lambda: fake)

    client.post("/sources", json={"url": url})
    assert worker.run_once() is True  # ingest
    spans.clear()
    assert worker.run_once() is True  # extract, with one retry

    validation_spans = [s for s in spans.get_finished_spans() if s.name == "validation"]
    assert len(validation_spans) == 2
    assert validation_spans[0].attributes["validation.ok"] is False
    assert "validation.error" in validation_spans[0].attributes
    assert validation_spans[1].attributes["validation.ok"] is True
