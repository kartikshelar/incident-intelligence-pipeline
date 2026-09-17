"""Test fixtures.

Tests run against a real Postgres instance per the brief ("Postgres with
Alembic migrations; no SQLite") — but a SEPARATE database from the one the
worker and API use (see tests/__init__.py). The session fixture creates
that database if it does not exist, then rebuilds the schema from
app.db.schema so tests always see the current tables. Each test truncates
the tables it touches so tests stay independent.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, make_url, text

from app.db.engine import get_engine
from app.db.schema import metadata
from app.settings import settings


def _ensure_test_database(url: str) -> None:
    parsed = make_url(url)
    name = parsed.database or ""
    if not name.endswith("_test"):
        raise RuntimeError(
            f"refusing to run tests against database {name!r}: the test database name "
            "must end in '_test' (set APP_TEST_DATABASE_URL). Tests truncate tables."
        )
    # Connect to the server's maintenance database to create ours if needed.
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Engine:
    _ensure_test_database(settings.database_url)
    eng = get_engine()
    # Rebuild from the current models: the test database is disposable, and
    # create_all alone would not alter tables left by an older schema.
    metadata.drop_all(eng)
    metadata.create_all(eng)
    return eng


@pytest.fixture(autouse=True)
def clean_tables(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE field_reviews, extractions, documents, jobs, sources"))
