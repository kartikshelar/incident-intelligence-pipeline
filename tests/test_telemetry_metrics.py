"""app.telemetry.metrics: /metrics body against real Postgres data, plus
the in-process histograms extract_document feeds."""

import uuid

import pytest
from sqlalchemy import Engine, text

from app.queue import enqueue
from app.settings import Settings
from app.telemetry.cost import Pricing
from app.telemetry.metrics import (
    _Histogram,
    cost_summary,
    extraction_latency_seconds,
    record_extraction,
    render_metrics,
    validation_attempts,
)
from tests.review_support import complete_extraction, insert_document


def _settings(**price: float) -> Settings:
    return Settings(  # type: ignore[call-arg]
        llm_provider=None,
        extraction_model=None,
        extraction_thinking=None,
        **{f"price_{k}_usd_per_mtok": v for k, v in price.items()},
    )


@pytest.fixture(autouse=True)
def _reset_histograms() -> None:
    """Histograms are process-global (module docstring); tests must not
    leak observations between each other."""
    extraction_latency_seconds._bucket_counts = [0] * len(extraction_latency_seconds._buckets)
    extraction_latency_seconds._count = 0
    extraction_latency_seconds._sum = 0.0
    validation_attempts._bucket_counts = [0] * len(validation_attempts._buckets)
    validation_attempts._count = 0
    validation_attempts._sum = 0.0
    yield


def test_histogram_buckets_and_sum() -> None:
    h = _Histogram((1, 5, 10))
    h.observe(0.5)
    h.observe(3)
    h.observe(7)
    h.observe(20)
    lines = h.render("x", "help")
    body = "\n".join(lines)
    assert 'x_bucket{le="1"} 1' in body
    assert 'x_bucket{le="5"} 2' in body
    assert 'x_bucket{le="10"} 3' in body
    assert 'x_bucket{le="+Inf"} 4' in body
    assert "x_sum 30.5" in body
    assert "x_count 4" in body


def test_record_extraction_updates_both_histograms() -> None:
    record_extraction(duration_seconds=2.5, attempts=2)
    assert extraction_latency_seconds._count == 1
    assert extraction_latency_seconds._sum == 2.5
    assert validation_attempts._count == 1
    assert validation_attempts._sum == 2


def test_document_status_counts_include_pending_and_extraction_status(engine: Engine) -> None:
    insert_document(engine, title="pending")
    complete_extraction(engine, document_id=insert_document(engine, title="done"))

    with engine.connect() as conn:
        body = render_metrics(conn, _settings())
    assert 'status="complete"' in body
    assert 'status="pending"' in body


def test_queue_depth_and_dead_letter_count(engine: Engine) -> None:
    with engine.begin() as conn:
        source_id = uuid.uuid4()
        conn.execute(
            text("INSERT INTO sources (id, url) VALUES (:id, 'https://x')"), {"id": source_id}
        )
        enqueue(conn, source_id)
        dead_job = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO jobs (id, source_id, kind, status, attempts) "
                "VALUES (:id, :s, 'ingest', 'dead_letter', 5)"
            ),
            {"id": dead_job, "s": source_id},
        )

    with engine.connect() as conn:
        body = render_metrics(conn, _settings())
    assert 'jobs_total{status="queued"} 1' in body
    assert 'jobs_total{status="dead_letter"} 1' in body
    assert "queue_depth 1" in body
    assert "dead_letter_total 1" in body


def test_review_queue_depth_counts_routed_fields(engine: Engine) -> None:
    complete_extraction(engine, confidence={"trigger": 0.5})
    with engine.begin() as conn:
        from app.review.routing import route_pending

        route_pending(conn, floor=0.7, budget=None)

    with engine.connect() as conn:
        body = render_metrics(conn, _settings())
    assert "review_queue_depth 1" in body


def test_cost_summary_is_unpriced_without_all_four_prices(engine: Engine) -> None:
    complete_extraction(engine)
    with engine.connect() as conn:
        summary = cost_summary(conn, None)
    assert summary.priced is False
    assert summary.total_usd is None


def test_cost_summary_computes_total_and_mean_when_priced(engine: Engine) -> None:
    complete_extraction(engine)
    complete_extraction(engine)
    pricing = Pricing(
        input_usd_per_mtok=3.0,
        output_usd_per_mtok=15.0,
        cache_read_usd_per_mtok=0.3,
        cache_write_usd_per_mtok=3.75,
    )
    with engine.connect() as conn:
        summary = cost_summary(conn, pricing)
    # FakeLLMClient reports 1000 input / 200 output tokens per call.
    expected_per_doc = 1000 * 3.0 / 1_000_000 + 200 * 15.0 / 1_000_000
    assert summary.priced is True
    assert summary.documents_priced == 2
    assert summary.mean_usd_per_document == pytest.approx(expected_per_doc)
    assert summary.total_usd == pytest.approx(expected_per_doc * 2)


def test_cost_summary_scopes_to_run_id(engine: Engine) -> None:
    doc_a = insert_document(engine, title="a")
    doc_b = insert_document(engine, title="b")
    ext_a = complete_extraction(engine, document_id=doc_a)
    complete_extraction(engine, document_id=doc_b)

    with engine.connect() as conn:
        run_id = conn.execute(
            text("SELECT run_id FROM extractions WHERE id=:id"), {"id": ext_a}
        ).scalar_one()
        pricing = Pricing(
            input_usd_per_mtok=3.0,
            output_usd_per_mtok=15.0,
            cache_read_usd_per_mtok=0.3,
            cache_write_usd_per_mtok=3.75,
        )
        summary = cost_summary(conn, pricing, run_id=run_id)
    assert summary.documents_priced == 1


def test_render_metrics_includes_cost_gauges(engine: Engine) -> None:
    complete_extraction(engine)
    settings = _settings(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)
    with engine.connect() as conn:
        body = render_metrics(conn, settings)
    assert "cost_per_document_usd_mean" in body
    assert "cost_total_usd" in body
    assert "cost_documents_priced_total 1" in body


def test_render_metrics_includes_histograms(engine: Engine) -> None:
    record_extraction(duration_seconds=3.0, attempts=1)
    with engine.connect() as conn:
        body = render_metrics(conn, _settings())
    assert "extraction_latency_seconds_bucket" in body
    assert "extraction_validation_attempts_bucket" in body
