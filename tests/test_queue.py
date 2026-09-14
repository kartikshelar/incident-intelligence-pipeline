"""Tests for the ADR-003 queue implementation.

Covers exactly what M1 promises: enqueue -> claim -> succeed/fail, SKIP
LOCKED concurrency (two claimants never get the same job), the visibility
timeout reclaim path, and the dead-letter transition after max attempts.
"""

import uuid

from sqlalchemy import Engine, text

from app.queue import claim_one, enqueue, mark_failed, mark_succeeded
from app.settings import settings


def _insert_source(engine: Engine) -> uuid.UUID:
    source_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO sources (id, url) VALUES (:id, :url)"),
            {"id": source_id, "url": "https://example.com/postmortem"},
        )
    return source_id


def test_enqueue_then_claim_transitions_to_running(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        job_id = enqueue(conn, source_id)

    with engine.begin() as conn:
        job = claim_one(conn)

    assert job is not None
    assert job.id == job_id
    assert job.status == "running"
    assert job.attempts == 1
    assert job.claimed_at is not None


def test_claim_returns_none_when_no_jobs_available(engine: Engine) -> None:
    with engine.begin() as conn:
        job = claim_one(conn)
    assert job is None


def test_claimed_job_is_not_claimed_again(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        enqueue(conn, source_id)

    with engine.begin() as conn:
        first = claim_one(conn)
    with engine.begin() as conn:
        second = claim_one(conn)

    assert first is not None
    assert second is None


def test_skip_locked_lets_concurrent_claimants_get_different_jobs(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        job_a = enqueue(conn, source_id)
        job_b = enqueue(conn, source_id)

    # Simulate two workers claiming concurrently: hold worker A's
    # transaction open (row locked) while worker B claims. SKIP LOCKED
    # means B must get the other job, not block or fail.
    with engine.connect() as conn_a:
        with conn_a.begin():
            claimed_a = claim_one(conn_a)
            assert claimed_a is not None

            with engine.begin() as conn_b:
                claimed_b = claim_one(conn_b)

    assert claimed_b is not None
    assert {claimed_a.id, claimed_b.id} == {job_a, job_b}


def test_mark_succeeded_sets_terminal_status(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        enqueue(conn, source_id)
    with engine.begin() as conn:
        job = claim_one(conn)
        assert job is not None
        mark_succeeded(conn, job.id)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, succeeded_at FROM jobs WHERE id=:id"), {"id": job.id}
        ).mappings().fetchone()
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["succeeded_at"] is not None


def test_transient_failure_requeues_job(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        enqueue(conn, source_id)
    with engine.begin() as conn:
        job = claim_one(conn)
        assert job is not None
        mark_failed(conn, job.id, error="fetch timeout", permanent=False)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, last_error FROM jobs WHERE id=:id"), {"id": job.id}
        ).mappings().fetchone()
    assert row is not None
    assert row["status"] == "queued"
    assert row["last_error"] == "fetch timeout"

    # And it can be claimed again.
    with engine.begin() as conn:
        reclaimed = claim_one(conn)
    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.attempts == 2


def test_permanent_failure_goes_straight_to_dead_letter(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        enqueue(conn, source_id)
    with engine.begin() as conn:
        job = claim_one(conn)
        assert job is not None
        mark_failed(conn, job.id, error="404 from source URL", permanent=True)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM jobs WHERE id=:id"), {"id": job.id}
        ).mappings().fetchone()
    assert row is not None
    assert row["status"] == "dead_letter"


def test_transient_failure_goes_to_dead_letter_after_max_attempts(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        enqueue(conn, source_id)

    for _ in range(settings.job_max_attempts):
        with engine.begin() as conn:
            job = claim_one(conn)
            assert job is not None
            mark_failed(conn, job.id, error="transient", permanent=False)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, attempts FROM jobs WHERE id=:id"), {"id": job.id}
        ).mappings().fetchone()
    assert row is not None
    assert row["attempts"] == settings.job_max_attempts
    assert row["status"] == "dead_letter"


def test_stuck_running_job_is_reclaimed_after_visibility_timeout(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        job_id = enqueue(conn, source_id)

    # Simulate a worker that claimed the job a long time ago and died
    # before committing succeeded/failed.
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET status='running', "
                "claimed_at = now() - make_interval(secs => :elapsed), attempts = 1 "
                "WHERE id = :id"
            ),
            {"id": job_id, "elapsed": settings.job_visibility_timeout_seconds + 60},
        )

    with engine.begin() as conn:
        reclaimed = claim_one(conn)

    assert reclaimed is not None
    assert reclaimed.id == job_id
    assert reclaimed.attempts == 2


def test_running_job_within_visibility_timeout_is_not_reclaimed(engine: Engine) -> None:
    source_id = _insert_source(engine)
    with engine.begin() as conn:
        job_id = enqueue(conn, source_id)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE jobs SET status='running', claimed_at = now(), attempts = 1 "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )

    with engine.begin() as conn:
        result = claim_one(conn)

    assert result is None
