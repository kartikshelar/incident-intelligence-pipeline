# Incident Intelligence Pipeline

Ingests public engineering postmortems, extracts a structured incident
record from each, routes low-confidence fields to human review, and
measures its own extraction quality against a frozen hand-labeled gold set.

See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the full contract, milestone
plan, and open [DERIVE] decisions. Architectural decisions are recorded in
[`docs/adr/`](docs/adr/).

## Status: M1 — Skeleton

No intelligence yet. This milestone proves the shape of the system: a
source URL can be registered, it becomes a row in Postgres, a worker
claims it through the queue, and its status transitions to `succeeded`.
Ingestion, parsing, and extraction are M2/M3.

```
docker compose up
```

runs cold on a clean machine: Postgres starts, a one-shot `migrate`
service applies Alembic migrations, then the API and worker start.

Register a source:

```
curl -X POST http://localhost:8000/sources \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/some-postmortem"}'
```

Response includes `job_id`; check its status:

```
curl http://localhost:8000/jobs/<job_id>
```

Watch it flip from `queued` to `succeeded` (the worker polls once a
second in this milestone; M1 does no real work on the job).

## Queue

Implemented per [ADR-003](docs/adr/003-queue.md): Postgres `SELECT ... FOR
UPDATE SKIP LOCKED`, no Redis/Celery. The entire queue implementation is
isolated behind `app/queue/` — nothing outside that package touches the
`jobs` table directly. See the ADR for the job lifecycle and the claim
query.

## Development

```
pip install -e ".[dev]"
docker compose up -d postgres
alembic upgrade head
export APP_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/incident_intel
pytest
ruff check .
mypy app
```

Tests run against real Postgres (no SQLite, per the brief) and truncate
`jobs`/`sources` between tests.
