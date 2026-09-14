"""Tests for the M1 API: register a source, check job status."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_register_source_creates_source_and_queued_job(engine: Engine) -> None:
    response = client.post("/sources", json={"url": "https://example.com/postmortem.html"})
    assert response.status_code == 201

    body = response.json()
    assert body["status"] == "queued"
    source_id = uuid.UUID(body["source_id"])
    job_id = uuid.UUID(body["job_id"])

    with engine.connect() as conn:
        source_row = conn.execute(
            text("SELECT url FROM sources WHERE id=:id"), {"id": source_id}
        ).mappings().fetchone()
        job_row = conn.execute(
            text("SELECT status, source_id FROM jobs WHERE id=:id"), {"id": job_id}
        ).mappings().fetchone()

    assert source_row is not None
    assert source_row["url"] == "https://example.com/postmortem.html"
    assert job_row is not None
    assert job_row["status"] == "queued"
    assert job_row["source_id"] == source_id


def test_register_source_rejects_invalid_url() -> None:
    response = client.post("/sources", json={"url": "not-a-url"})
    assert response.status_code == 422


def test_get_job_status(engine: Engine) -> None:
    register = client.post("/sources", json={"url": "https://example.com/a"})
    job_id = register.json()["job_id"]

    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == "queued"
    assert body["attempts"] == 0


def test_get_job_status_404_for_unknown_job() -> None:
    response = client.get(f"/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
