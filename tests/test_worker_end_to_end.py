"""End-to-end test: register a source URL -> row in Postgres -> worker
claims it -> fetches + parses it (M2) -> status transitions to succeeded,
with a document row to show for it.

This is the acceptance test for M1's queue lifecycle *and* M2's ingest
step. It does not go through the API process or a real container; it calls
the same functions the API and worker containers call, against the same
database, which is sufficient to prove the data flow without requiring a
running docker-compose stack inside the test process itself. HTTP is
mocked (respx) — no real network calls.
"""

import uuid

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.main import app
from app.ingest.errors import EmptyExtractionError
from app.worker.main import run_once

client = TestClient(app)


@respx.mock
def test_registered_source_flows_through_to_succeeded_with_a_document(engine: Engine) -> None:
    url = "https://example.com/postmortem"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"<html><head><title>Outage</title></head>"
            b"<body><article>Detailed incident narrative goes here.</article></body></html>",
            headers={"content-type": "text/html"},
        )
    )

    response = client.post("/sources", json={"url": url})
    assert response.status_code == 201
    job_id = uuid.UUID(response.json()["job_id"])

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM jobs WHERE id=:id"), {"id": job_id}
        ).scalar_one()
    assert status == "queued"

    claimed = run_once()
    assert claimed is True

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status, attempts, succeeded_at FROM jobs WHERE id=:id"), {"id": job_id}
        ).mappings().fetchone()

    assert status is not None
    assert status["status"] == "succeeded"
    assert status["attempts"] == 1
    assert status["succeeded_at"] is not None

    with engine.connect() as conn:
        doc = conn.execute(
            text("SELECT format, title, text FROM documents")
        ).mappings().fetchone()
    assert doc is not None
    assert doc["format"] == "html"
    assert doc["title"] == "Outage"
    assert "Detailed incident narrative" in doc["text"]


def test_run_once_returns_false_when_queue_is_empty(engine: Engine) -> None:
    claimed = run_once()
    assert claimed is False


@respx.mock
def test_permanent_fetch_failure_sends_job_to_dead_letter(engine: Engine) -> None:
    url = "https://example.com/gone"
    respx.get(url).mock(return_value=httpx.Response(404))

    response = client.post("/sources", json={"url": url})
    job_id = uuid.UUID(response.json()["job_id"])

    claimed = run_once()
    assert claimed is True

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, last_error FROM jobs WHERE id=:id"), {"id": job_id}
        ).mappings().fetchone()
    assert row is not None
    assert row["status"] == "dead_letter"
    assert "404" in row["last_error"] or "permanent" in row["last_error"].lower()


@respx.mock
def test_transient_fetch_failure_requeues_job(engine: Engine) -> None:
    url = "https://example.com/flaky"
    respx.get(url).mock(return_value=httpx.Response(503))

    response = client.post("/sources", json={"url": url})
    job_id = uuid.UUID(response.json()["job_id"])

    claimed = run_once()
    assert claimed is True

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, attempts FROM jobs WHERE id=:id"), {"id": job_id}
        ).mappings().fetchone()
    assert row is not None
    assert row["status"] == "queued"
    assert row["attempts"] == 1


@respx.mock
def test_empty_extraction_is_a_hard_failure_not_a_partial_record(engine: Engine) -> None:
    """PROJECT_BRIEF M2: 'A silent empty extraction is a HARD FAILURE, not
    a partial record.' Verified at the worker level: the job goes to
    dead_letter (permanent), and no `documents` row is written for it.
    """
    url = "https://example.com/blank"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"<html><head><title>Blank</title></head><body></body></html>",
            headers={"content-type": "text/html"},
        )
    )

    response = client.post("/sources", json={"url": url})
    job_id = uuid.UUID(response.json()["job_id"])

    claimed = run_once()
    assert claimed is True

    with engine.connect() as conn:
        job_row = conn.execute(
            text("SELECT status FROM jobs WHERE id=:id"), {"id": job_id}
        ).mappings().fetchone()
        doc_count = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()

    assert job_row is not None
    assert job_row["status"] == "dead_letter"
    assert doc_count == 0


def test_empty_extraction_error_is_exported_for_callers() -> None:
    # Sanity check that the error type worker.main relies on is importable
    # from its documented location (app.ingest.errors), matching the class
    # hierarchy asserted in test_parse.py.
    assert issubclass(EmptyExtractionError, Exception)
