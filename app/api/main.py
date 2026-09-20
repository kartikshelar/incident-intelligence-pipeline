"""API: register a source URL, see its status (M1); review queue (M4).

No ingestion, parsing, or extraction happens here — the worker does that.
`register_source`'s only job is: write a `sources` row and a `queued` job
row in one transaction, so a worker can pick it up. The M4 review
endpoints live in app/api/review.py (JSON) and app/api/review_ui.py
(server-rendered HTML) and are mounted here, gated by app.api.auth's HTTP
Basic dependency: they are the only endpoints that write gold-set data
(M6 part 2 — see app/api/auth.py for the auth design).
"""

import uuid

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, HttpUrl
from sqlalchemy import text

from app.api import review, review_ui
from app.api.auth import require_review_auth
from app.db.engine import get_engine
from app.queue import enqueue
from app.settings import settings
from app.telemetry.cost import pricing_from_settings
from app.telemetry.logctx import configure_logging
from app.telemetry.metrics import cost_summary, render_metrics
from app.telemetry.tracing import configure as configure_tracing

configure_logging()
configure_tracing(settings)

app = FastAPI(title="Incident Intelligence Pipeline", version="0.1.0")
app.include_router(review.router, dependencies=[Depends(require_review_auth)])
app.include_router(review_ui.router, dependencies=[Depends(require_review_auth)])


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


class CostResponse(BaseModel):
    run_id: str | None
    priced: bool
    total_usd: float | None
    documents_priced: int
    mean_usd_per_document: float | None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    """PROJECT_BRIEF M6: documents by status, jobs by state, queue depth,
    dead-letter count, extraction latency histogram, validation attempts
    histogram, review queue depth, cost per document. Prometheus text
    exposition format (app.telemetry.metrics.render_metrics)."""
    engine = get_engine()
    with engine.connect() as conn:
        body = render_metrics(conn, settings)
    return Response(content=body, media_type="text/plain; version=0.0.4")


@app.get("/cost", response_model=CostResponse)
def cost(run_id: str | None = None) -> CostResponse:
    """Cost per document, queryable per run_id (PROJECT_BRIEF M6): "This
    exists in run reports but not in the running system." Same computation
    scripts/extraction_run.py uses, against the live `extractions` table."""
    engine = get_engine()
    pricing = pricing_from_settings(settings)
    with engine.connect() as conn:
        summary = cost_summary(conn, pricing, run_id=run_id)
    return CostResponse(
        run_id=run_id,
        priced=summary.priced,
        total_usd=summary.total_usd,
        documents_priced=summary.documents_priced,
        mean_usd_per_document=summary.mean_usd_per_document,
    )


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
