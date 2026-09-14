"""M1 API: register a source URL, see its status.

No ingestion, parsing, or extraction happens here or anywhere yet — that's
M2/M3. This endpoint's only job is: write a `sources` row and a `queued`
job row in one transaction, so a worker can pick it up.
"""

import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl
from sqlalchemy import text

from app.db.engine import get_engine
from app.queue import enqueue

app = FastAPI(title="Incident Intelligence Pipeline", version="0.1.0")


class RegisterSourceRequest(BaseModel):
    url: HttpUrl


class RegisterSourceResponse(BaseModel):
    source_id: uuid.UUID
    job_id: uuid.UUID
    status: str


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    source_id: uuid.UUID
    status: str
    attempts: int


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sources", response_model=RegisterSourceResponse, status_code=201)
def register_source(body: RegisterSourceRequest) -> RegisterSourceResponse:
    source_id = uuid.uuid4()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO sources (id, url) VALUES (:id, :url)"),
            {"id": source_id, "url": str(body.url)},
        )
        # Same transaction as the source insert, per ADR-003 §2: the job
        # can never point at a source that failed to commit.
        job_id = enqueue(conn, source_id)

    return RegisterSourceResponse(source_id=source_id, job_id=job_id, status="queued")


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: uuid.UUID) -> JobStatusResponse:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, source_id, status, attempts FROM jobs WHERE id=:id"),
            {"id": job_id},
        ).mappings().fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="job not found")

    return JobStatusResponse(
        job_id=row["id"], source_id=row["source_id"], status=row["status"], attempts=row["attempts"]
    )
