"""End-to-end test: register a source URL -> row in Postgres -> worker
claims the ingest job -> fetches + parses it (M2) -> enqueues an extract
job -> worker claims that -> validated extraction row (M3), with job status
transitions at each step.

This is the acceptance test for M1's queue lifecycle, M2's ingest step and
M3's extraction step. It does not go through the API process or a real
container; it calls the same functions the API and worker containers call,
against the same database, which is sufficient to prove the data flow
without requiring a running docker-compose stack inside the test process
itself. HTTP is mocked (respx) and the LLM is a scripted fake — no real
network calls, no API key.
"""

import uuid

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.main import app
from app.extract.errors import PermanentExtractionError, TransientExtractionError
from app.ingest.errors import EmptyExtractionError
from app.worker import main as worker
from tests.fake_llm import FakeLLMClient, invalid_output_json, valid_output_json

client = TestClient(app)

HTML = (
    b"<html><head><title>Outage</title></head>"
    b"<body><article>Detailed incident narrative goes here.</article></body></html>"
)


def _register(url: str) -> uuid.UUID:
    response = client.post("/sources", json={"url": url})
    assert response.status_code == 201
    return uuid.UUID(response.json()["job_id"])


def _job(engine: Engine, job_id: uuid.UUID) -> dict:
    with engine.connect() as conn:
        row = (
            conn.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id})
            .mappings()
            .fetchone()
        )
    assert row is not None
    return dict(row)


def _jobs_of_kind(engine: Engine, kind: str) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text("SELECT * FROM jobs WHERE kind=:kind ORDER BY created_at"), {"kind": kind}
            ).mappings()
        ]


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    fake = FakeLLMClient([valid_output_json()])
    monkeypatch.setattr(worker, "get_llm_client", lambda: fake)
    return fake


@respx.mock
def test_registered_source_flows_through_ingest_and_extract(
    engine: Engine, llm: FakeLLMClient
) -> None:
    url = "https://example.com/postmortem"
    respx.get(url).mock(
        return_value=httpx.Response(200, content=HTML, headers={"content-type": "text/html"})
    )

    ingest_job_id = _register(url)
    assert _job(engine, ingest_job_id)["status"] == "queued"
    assert _job(engine, ingest_job_id)["kind"] == "ingest"

    # 1. ingest
    assert worker.run_once() is True
    ingest_job = _job(engine, ingest_job_id)
    assert ingest_job["status"] == "succeeded"
    assert ingest_job["attempts"] == 1
    assert ingest_job["succeeded_at"] is not None

    with engine.connect() as conn:
        doc = (
            conn.execute(text("SELECT id, format, title, text FROM documents"))
            .mappings()
            .fetchone()
        )
    assert doc is not None
    assert doc["format"] == "html"
    assert doc["title"] == "Outage"
    assert "Detailed incident narrative" in doc["text"]

    # The extract job was enqueued by the ingest job, in its transaction.
    extract_jobs = _jobs_of_kind(engine, "extract")
    assert len(extract_jobs) == 1
    assert extract_jobs[0]["status"] == "queued"
    assert extract_jobs[0]["document_id"] == doc["id"]
    assert llm.calls == []

    # 2. extract
    assert worker.run_once() is True
    assert _job(engine, extract_jobs[0]["id"])["status"] == "succeeded"
    assert len(llm.calls) == 1
    assert "Detailed incident narrative" in llm.calls[0]["messages"][0]["content"]

    with engine.connect() as conn:
        extraction = (
            conn.execute(
                text(
                    "SELECT status, record, per_field_confidence FROM extractions "
                    "WHERE document_id=:d"
                ),
                {"d": doc["id"]},
            )
            .mappings()
            .fetchone()
        )
    assert extraction is not None
    assert extraction["status"] == "complete"
    assert extraction["record"]["title"] == "Outage"  # metadata title wins
    assert extraction["record"]["title_source"] == "document_metadata"
    assert extraction["record"]["detection_method"] == "monitoring"
    assert set(extraction["per_field_confidence"]) == set(extraction["record"])

    # M4: every field has a review state, and none is below the floor
    # (the fixture reports 0.9 everywhere), so the queue stays empty.
    with engine.connect() as conn:
        states = [
            r[0] for r in conn.execute(text("SELECT review_state FROM field_reviews"))
        ]
    assert len(states) == 23
    assert set(states) == {"unreviewed"}

    # 3. nothing left
    assert worker.run_once() is False


def test_run_once_returns_false_when_queue_is_empty(engine: Engine) -> None:
    assert worker.run_once() is False


@respx.mock
def test_permanent_fetch_failure_sends_job_to_dead_letter(engine: Engine) -> None:
    url = "https://example.com/gone"
    respx.get(url).mock(return_value=httpx.Response(404))
    job_id = _register(url)

    assert worker.run_once() is True
    row = _job(engine, job_id)
    assert row["status"] == "dead_letter"
    assert "404" in row["last_error"] or "permanent" in row["last_error"].lower()
    assert _jobs_of_kind(engine, "extract") == []  # no document, no extract job


@respx.mock
def test_transient_fetch_failure_requeues_job(engine: Engine) -> None:
    url = "https://example.com/flaky"
    respx.get(url).mock(return_value=httpx.Response(503))
    job_id = _register(url)

    assert worker.run_once() is True
    row = _job(engine, job_id)
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
    job_id = _register(url)

    assert worker.run_once() is True
    with engine.connect() as conn:
        doc_count = conn.execute(text("SELECT count(*) FROM documents")).scalar_one()
    assert _job(engine, job_id)["status"] == "dead_letter"
    assert doc_count == 0


def test_empty_extraction_error_is_exported_for_callers() -> None:
    assert issubclass(EmptyExtractionError, Exception)


# --- M3: extraction failures route through the same state machine ------------


def _ingest_then_claim_extract(engine: Engine, url: str) -> uuid.UUID:
    respx.get(url).mock(
        return_value=httpx.Response(200, content=HTML, headers={"content-type": "text/html"})
    )
    _register(url)
    assert worker.run_once() is True  # ingest
    (extract_job,) = _jobs_of_kind(engine, "extract")
    return extract_job["id"]


@respx.mock
def test_schema_validation_exhausted_dead_letters_and_records_every_attempt(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLLMClient([invalid_output_json()] * 3)
    monkeypatch.setattr(worker, "get_llm_client", lambda: fake)
    monkeypatch.setattr(worker.settings, "extraction_max_attempts", 3)
    extract_job_id = _ingest_then_claim_extract(engine, "https://example.com/pm-invalid")

    assert worker.run_once() is True
    job = _job(engine, extract_job_id)
    assert job["status"] == "dead_letter"
    assert "SchemaValidationExhaustedError" not in job["last_error"]  # str(exc), not repr
    assert "all 3 attempts" in job["last_error"]

    with engine.connect() as conn:
        row = (
            conn.execute(text("SELECT status, error_kind, attempts FROM extractions"))
            .mappings()
            .fetchone()
        )
    assert row is not None
    assert (row["status"], row["error_kind"], row["attempts"]) == ("failed", "permanent", 3)


@respx.mock
def test_transient_llm_failure_requeues_and_records(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLLMClient([TransientExtractionError("rate limited (429)"), valid_output_json()])
    monkeypatch.setattr(worker, "get_llm_client", lambda: fake)
    extract_job_id = _ingest_then_claim_extract(engine, "https://example.com/pm-429")

    assert worker.run_once() is True
    job = _job(engine, extract_job_id)
    assert job["status"] == "queued"
    assert job["attempts"] == 1
    assert "429" in job["last_error"]

    # Retry succeeds; both outcomes are on the extractions table.
    assert worker.run_once() is True
    assert _job(engine, extract_job_id)["status"] == "succeeded"
    with engine.connect() as conn:
        statuses = [
            r[0] for r in conn.execute(text("SELECT status FROM extractions ORDER BY created_at"))
        ]
    assert statuses == ["failed", "complete"]


@respx.mock
def test_unconfigured_llm_client_dead_letters_with_a_recorded_reason(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_client() -> FakeLLMClient:
        raise PermanentExtractionError("anthropic client not configured: no API key")

    monkeypatch.setattr(worker, "get_llm_client", _no_client)
    extract_job_id = _ingest_then_claim_extract(engine, "https://example.com/pm-nokey")

    assert worker.run_once() is True
    job = _job(engine, extract_job_id)
    assert job["status"] == "dead_letter"
    assert "not configured" in job["last_error"]
