"""GET /metrics and GET /cost, through the real FastAPI app (PROJECT_BRIEF M6)."""

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.main import app
from tests.review_support import complete_extraction

client = TestClient(app)


def test_metrics_endpoint_returns_prometheus_text(engine: Engine) -> None:
    complete_extraction(engine)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "documents_total" in body
    assert "jobs_total" in body
    assert "queue_depth" in body
    assert "dead_letter_total" in body
    assert "review_queue_depth" in body
    assert "extraction_latency_seconds_bucket" in body
    assert "extraction_validation_attempts_bucket" in body
    assert "cost_per_document_usd_mean" in body


def test_cost_endpoint_is_unpriced_without_price_env(engine: Engine) -> None:
    complete_extraction(engine)
    response = client.get("/cost")
    assert response.status_code == 200
    body = response.json()
    assert body["priced"] is False
    assert body["total_usd"] is None


def test_cost_endpoint_prices_when_configured(engine: Engine, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.api import main as api_main

    monkeypatch.setattr(api_main.settings, "price_input_usd_per_mtok", 3.0)
    monkeypatch.setattr(api_main.settings, "price_output_usd_per_mtok", 15.0)
    monkeypatch.setattr(api_main.settings, "price_cache_read_usd_per_mtok", 0.3)
    monkeypatch.setattr(api_main.settings, "price_cache_write_usd_per_mtok", 3.75)
    complete_extraction(engine)

    response = client.get("/cost")
    body = response.json()
    assert body["priced"] is True
    assert body["documents_priced"] == 1
    assert body["mean_usd_per_document"] > 0


def test_cost_endpoint_accepts_run_id_query_param(engine: Engine) -> None:
    response = client.get("/cost", params={"run_id": "does-not-exist"})
    assert response.status_code == 200
    assert response.json()["run_id"] == "does-not-exist"
    assert response.json()["documents_priced"] == 0
