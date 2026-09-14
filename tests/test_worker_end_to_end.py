"""End-to-end M1 test: register a source URL -> row in Postgres -> worker
claims it -> status transitions to succeeded.

This is the acceptance test for M1's "done when" clause in the brief. It
does not go through the API process or a real container; it calls the same
functions the API and worker containers call, against the same database,
which is sufficient to prove the skeleton's data flow without requiring a
running docker-compose stack inside the test process itself.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.main import app
from app.worker.main import run_once

client = TestClient(app)


def test_registered_source_flows_through_to_succeeded(engine: Engine) -> None:
    response = client.post("/sources", json={"url": "https://example.com/postmortem"})
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


def test_run_once_returns_false_when_queue_is_empty(engine: Engine) -> None:
    claimed = run_once()
    assert claimed is False
