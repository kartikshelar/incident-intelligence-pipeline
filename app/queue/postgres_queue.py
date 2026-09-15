"""Postgres SKIP LOCKED queue implementation. Implements ADR-003.

Do not call anything in this module from outside `app.queue` except through
the re-exports in `app/queue/__init__.py`. The claim query is raw SQL,
verbatim from ADR-003 §4, by design — not ORM-generated — because the ADR
requires it and because a hand-auditable query is the whole point of
choosing this queue over a dedicated broker.
"""

import dataclasses
import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import Connection, text

from app.settings import settings

JobKind = Literal["ingest", "extract"]

# Verbatim from docs/adr/003-queue.md §4. Do not "clean up" the formatting
# without checking it still matches the ADR — the ADR is the spec here.
_CLAIM_SQL = text(
    """
    UPDATE jobs SET status='running', claimed_at=now(), attempts=attempts+1
    WHERE id = (
        SELECT id FROM jobs
        WHERE status='queued'
           OR (status='running'
               AND claimed_at < now() - make_interval(secs => :visibility_timeout_seconds))
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING *;
    """
)


@dataclasses.dataclass(frozen=True)
class Job:
    id: uuid.UUID
    source_id: uuid.UUID
    kind: str
    document_id: uuid.UUID | None
    status: str
    attempts: int
    created_at: datetime
    claimed_at: datetime | None
    succeeded_at: datetime | None
    last_error: str | None


def enqueue(
    conn: Connection,
    source_id: uuid.UUID,
    *,
    kind: JobKind = "ingest",
    document_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a queued job for `source_id`.

    `kind="extract"` requires `document_id` (the table's check constraint
    enforces it). Callers are responsible for running this in the same
    transaction as the write that created `source_id` / `document_id`, per
    ADR-003 §2 ("the job row and the incident record can be committed in
    the same Postgres transaction").
    """
    if (kind == "extract") != (document_id is not None):
        raise ValueError("document_id is required for extract jobs and forbidden otherwise")
    job_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO jobs (id, source_id, kind, document_id, status, attempts) "
            "VALUES (:id, :source_id, :kind, :document_id, 'queued', 0)"
        ),
        {"id": job_id, "source_id": source_id, "kind": kind, "document_id": document_id},
    )
    return job_id


def claim_one(conn: Connection) -> Job | None:
    """Atomically claim the oldest available job, if any.

    A single statement performs the claim, the visibility-timeout reclaim,
    and acts as the stuck-job reaper (ADR-003 §4) — no separate reaper
    process. Must be called within an open transaction that the caller
    commits (or rolls back) once the claimed job's outcome is known;
    holding the transaction open is what keeps the row locked from other
    workers until this worker finishes with it or crashes.
    """
    row = (
        conn.execute(
            _CLAIM_SQL,
            {"visibility_timeout_seconds": settings.job_visibility_timeout_seconds},
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        return None
    return Job(**dict(row))


def mark_succeeded(conn: Connection, job_id: uuid.UUID) -> None:
    conn.execute(
        text("UPDATE jobs SET status='succeeded', succeeded_at=now() WHERE id=:id"),
        {"id": job_id},
    )


def mark_failed(conn: Connection, job_id: uuid.UUID, *, error: str, permanent: bool) -> None:
    """Record a failed attempt.

    `permanent=True` sends the job straight to `dead_letter` (ADR-003 §4:
    e.g. a 404 from the source URL). `permanent=False` is a transient
    failure (e.g. a fetch timeout): the job returns to `queued` unless it
    has exhausted `settings.job_max_attempts`, in which case it also goes
    to `dead_letter` rather than retrying forever.
    """
    if permanent:
        conn.execute(
            text("UPDATE jobs SET status='dead_letter', last_error=:error WHERE id=:id"),
            {"id": job_id, "error": error},
        )
        return

    row = (
        conn.execute(text("SELECT attempts FROM jobs WHERE id=:id"), {"id": job_id})
        .mappings()
        .fetchone()
    )
    attempts = row["attempts"] if row else 0

    next_status = "dead_letter" if attempts >= settings.job_max_attempts else "queued"
    conn.execute(
        text("UPDATE jobs SET status=:status, last_error=:error WHERE id=:id"),
        {"id": job_id, "status": next_status, "error": error},
    )
