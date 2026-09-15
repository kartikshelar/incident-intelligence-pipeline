"""Test package. Imported by pytest before conftest.py, so this is where the
test database is pinned BEFORE app.settings is imported anywhere.

Tests truncate and recreate tables. They must never run against the
database the worker/API use (docker compose's `incident_intel`): the
worker would claim test jobs and truncation would erase real extraction
rows — both happened on 2026-09-15. So the test process always overrides
APP_DATABASE_URL with APP_TEST_DATABASE_URL (default: the same compose
Postgres, database `incident_intel_test`), and conftest refuses any name
that does not end in `_test`.
"""

import os

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:5432/incident_intel_test"
)

os.environ["APP_DATABASE_URL"] = os.environ.get("APP_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
