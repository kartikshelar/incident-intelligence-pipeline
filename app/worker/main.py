"""M1 worker: claim a job, do nothing intelligent with it, mark it succeeded.

This exists to prove the skeleton end to end: docker compose up cold ->
register a source URL -> row in Postgres -> worker picks it up -> status
transitions. No fetching, parsing, or extraction — that's M2/M3. Any real
processing step will replace `process_job` below without touching the
claim/commit/retry loop around it.
"""

import logging
import time

from sqlalchemy import Connection

from app.db.engine import get_engine
from app.queue import Job, claim_one, mark_failed, mark_succeeded
from app.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")


def process_job(conn: Connection, job: Job) -> None:
    """M1 placeholder for real work. Always succeeds.

    Replaced by real ingest/parse/extract logic in later milestones. Kept
    as a separate function so that swap is a one-function change, not a
    rewrite of claim/commit/retry handling.
    """
    logger.info("processing job %s for source %s (no-op in M1)", job.id, job.source_id)


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
        except Exception as exc:  # noqa: BLE001 - failures must be recorded, not swallowed
            logger.exception("job %s failed", job.id)
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
