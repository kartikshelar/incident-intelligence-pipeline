"""Worker: claim a job, ingest the source it points to, mark it succeeded.

M2 replaces M1's no-op `process_job` with a real fetch -> parse -> persist
step (app.ingest.pipeline.ingest_source). No extraction, no LLM calls —
that's M3. `process_job` stays a separate function so *that* swap will
again be a one-function change, not a rewrite of claim/commit/retry.
"""

import logging
import time

from sqlalchemy import Connection, text

from app.db.engine import get_engine
from app.ingest.errors import PermanentIngestError, TransientIngestError
from app.ingest.pipeline import ingest_source
from app.queue import Job, claim_one, mark_failed, mark_succeeded
from app.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")


def process_job(conn: Connection, job: Job) -> None:
    """Ingest the source this job points to.

    Raises `TransientIngestError` / `PermanentIngestError` on failure;
    `run_once` maps those onto ADR-003's `queued` vs `dead_letter` retry
    semantics. Any other exception is treated as transient (conservative
    default: retry rather than silently give up on an unexpected bug).
    """
    source_url = conn.execute(
        text("SELECT url FROM sources WHERE id = :id"), {"id": job.source_id}
    ).scalar_one()

    result = ingest_source(conn, source_id=job.source_id, source_url=source_url)
    if result.was_duplicate:
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


def run_once() -> bool:
    """Claim and process a single job. Returns True if a job was claimed."""
    engine = get_engine()
    with engine.begin() as conn:
        job = claim_one(conn)
        if job is None:
            return False

        logger.info("claimed job %s (attempt %d)", job.id, job.attempts)
        try:
            process_job(conn, job)
        except PermanentIngestError as exc:
            logger.warning("job %s permanently failed: %s", job.id, exc)
            mark_failed(conn, job.id, error=str(exc), permanent=True)
            return True
        except TransientIngestError as exc:
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
