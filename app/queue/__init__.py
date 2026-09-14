"""Narrow queue interface (ADR-003 §4).

This is the only module in the codebase allowed to know that jobs are
implemented as Postgres rows claimed with `SKIP LOCKED`. API handlers and
worker business logic call `enqueue()` / `claim_one()` / `mark_succeeded()`
/ `mark_failed()` and must not otherwise touch the `jobs` table.

If DERIVE-02 is ever revisited (see ADR-003 §5 — sustained throughput,
delayed jobs, multiple priority classes), only this module changes.
"""

from app.queue.postgres_queue import (
    Job,
    claim_one,
    enqueue,
    mark_failed,
    mark_succeeded,
)

__all__ = ["Job", "claim_one", "enqueue", "mark_failed", "mark_succeeded"]
