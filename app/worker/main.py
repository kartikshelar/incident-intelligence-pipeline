"""Worker: claim a job, run the stage it names, mark it succeeded or failed.

Two job kinds (M3):
  ingest   fetch -> parse -> `documents` row, then enqueue an `extract`
           job for that document in the same transaction (ADR-003 §2).
  extract  `documents` row -> LLM -> validated `extractions` row.

Failure routing is stage-agnostic: every stage raises a subclass of
app.errors.TransientJobError (requeue) or PermanentJobError (dead_letter),
and `run_once` maps those onto ADR-003's state machine. Anything else is
treated as transient — retry rather than silently give up on an unexpected
bug — and is logged with a traceback.
"""

import logging
import time
from functools import lru_cache

from sqlalchemy import Connection, text

from app.db.engine import get_engine
from app.errors import PermanentJobError, TransientJobError
from app.extract.llm import LLMClient, build_llm_client
from app.extract.pipeline import extract_document
from app.ingest.pipeline import ingest_source
from app.queue import Job, claim_one, enqueue, mark_failed, mark_succeeded
from app.review.routing import route_pending
from app.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")


@lru_cache
def get_llm_client() -> LLMClient:
    """One client per process, for whichever provider/model is configured.
    Raises PermanentExtractionError if provider, model, or credentials are
    missing — surfaced per job, on the extractions row."""
    return build_llm_client(settings)


def process_ingest(conn: Connection, job: Job) -> None:
    source_url = conn.execute(
        text("SELECT url FROM sources WHERE id = :id"), {"id": job.source_id}
    ).scalar_one()

    result = ingest_source(conn, source_id=job.source_id, source_url=source_url)
    if result.provenance_updated:
        # ADR-007: new bytes, same text. The document keeps its id and
        # its extractions; only where-it-came-from moved.
        logger.info(
            "job %s: text_hash %s already ingested as document %s; bytes changed "
            "(content_hash now %s), provenance updated in place",
            job.id,
            result.text_hash,
            result.document_id,
            result.content_hash,
        )
    elif result.was_duplicate:
        logger.info(
            "job %s: content_hash %s already ingested as document %s (idempotent no-op)",
            job.id,
            result.content_hash,
            result.document_id,
        )
    else:
        logger.info(
            "job %s: ingested document %s (%s, %d chars, title=%r)",
            job.id,
            result.document_id,
            result.format,
            result.text_chars,
            result.title,
        )

    # Always enqueue, even for a duplicate document: extract_document is
    # itself idempotent, so this costs one no-op job at most, and it means
    # a document whose earlier extraction dead-lettered gets another go
    # when its source is re-registered.
    extract_job_id = enqueue(conn, job.source_id, kind="extract", document_id=result.document_id)
    logger.info("job %s: enqueued extract job %s", job.id, extract_job_id)


def process_extract(conn: Connection, job: Job) -> None:
    assert job.document_id is not None  # guaranteed by jobs_extract_has_document
    outcome = extract_document(
        conn,
        document_id=job.document_id,
        client=get_llm_client(),
        max_attempts=settings.extraction_max_attempts,
    )
    if outcome.was_duplicate:
        logger.info(
            "job %s: document %s already has extraction %s (idempotent no-op)",
            job.id,
            job.document_id,
            outcome.extraction_id,
        )
    else:
        logger.info(
            "job %s: extraction %s complete for document %s in %d attempt(s)",
            job.id,
            outcome.extraction_id,
            job.document_id,
            outcome.attempts,
        )
        # M4 (ADR-010): a routing pass in the same transaction, so the new
        # fields are in the queue the moment the extraction is visible.
        # The pass is global — it ranks every unreviewed field below the
        # floor, not just this document's — because the budget is a cap on
        # the reviewer's outstanding work, not a per-document quota.
        routing = route_pending(
            conn, floor=settings.review_confidence_floor, budget=settings.review_budget
        )
        logger.info(
            "job %s: routed %d field(s) for review (floor=%.2f, budget=%s, eligible=%d, "
            "outstanding before=%d)",
            job.id,
            len(routing.routed),
            routing.floor,
            "uncapped" if routing.budget is None else routing.budget,
            routing.eligible,
            routing.outstanding_before,
        )


def process_job(conn: Connection, job: Job) -> None:
    if job.kind == "ingest":
        process_ingest(conn, job)
    elif job.kind == "extract":
        process_extract(conn, job)
    else:  # unreachable given jobs_kind_valid; fail loudly rather than succeed silently
        raise PermanentJobError(f"unknown job kind {job.kind!r}")


def run_once() -> bool:
    """Claim and process a single job. Returns True if a job was claimed."""
    engine = get_engine()
    with engine.begin() as conn:
        job = claim_one(conn)
        if job is None:
            return False

        logger.info("claimed %s job %s (attempt %d)", job.kind, job.id, job.attempts)
        try:
            process_job(conn, job)
        except PermanentJobError as exc:
            logger.warning("job %s permanently failed: %s", job.id, exc)
            mark_failed(conn, job.id, error=str(exc), permanent=True)
            return True
        except TransientJobError as exc:
            logger.warning("job %s transiently failed: %s", job.id, exc)
            mark_failed(conn, job.id, error=str(exc), permanent=False)
            return True
        except Exception as exc:  # noqa: BLE001 - failures must be recorded, not swallowed
            logger.exception("job %s failed with an unexpected error", job.id)
            mark_failed(conn, job.id, error=str(exc), permanent=False)
            return True

        mark_succeeded(conn, job.id)
        logger.info("job %s succeeded", job.id)

    return True


def main() -> None:
    logger.info("worker starting, poll interval=%ss", settings.worker_poll_interval_seconds)
    while True:
        claimed = run_once()
        if not claimed:
            time.sleep(settings.worker_poll_interval_seconds)


if __name__ == "__main__":
    main()
