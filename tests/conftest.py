"""Test fixtures.

Tests run against a real Postgres instance per the brief ("Postgres with
Alembic migrations; no SQLite"). Point APP_DATABASE_URL at a test database
(docker-compose's `postgres` service works — see README) before running
pytest. Each test truncates the tables it touches so tests stay independent
without needing a fresh database per run.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from app.db.engine import get_engine
from app.db.schema import metadata


@pytest.fixture(scope="session")
def engine() -> Engine:
    eng = get_engine()
    # Ensure schema exists even if the caller didn't run `alembic upgrade
    # head` first (e.g. a fresh CI container). Idempotent.
    metadata.create_all(eng)
    return eng


@pytest.fixture(autouse=True)
def clean_tables(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE documents, jobs, sources"))
