# Incident Intelligence Pipeline

Ingests public engineering postmortems, extracts a structured incident
record from each, routes low-confidence fields to human review, and
measures its own extraction quality against a frozen hand-labeled gold set.

See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the full contract, milestone
plan, and open [DERIVE] decisions. Architectural decisions are recorded in
[`docs/adr/`](docs/adr/).

## Status: M2 — Ingest & parse

Still no extraction and no LLM calls — that's M3. A registered source URL
is now actually fetched and normalized: the worker downloads it, detects
whether it's markdown, HTML, or a PDF, converts it to plain text with
provenance (source URL, fetch time, content hash), and writes a
`documents` row. Re-ingesting content that hasn't changed is a no-op, not
a duplicate.

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

`queued` -> `running` -> `succeeded`, with a new row in `documents`
(title, normalized text, format, content_hash, fetched_at). A 404/403/etc.
sends the job straight to `dead_letter` (no retry wasted on a URL that
doesn't exist); a timeout or 5xx requeues it. An extraction that produces
empty text is treated as a **hard failure** (`dead_letter`, no `documents`
row written) rather than a partial record — see
[`spike/FINDINGS.md`](spike/FINDINGS.md) §2 item 1.

## Parsing

`app/ingest/parse.py` normalizes markdown/HTML/PDF to text. Every fix in
it traces to a numbered finding in [`spike/FINDINGS.md`](spike/FINDINGS.md)
§2, discovered by running the M0 spike's throwaway extractor against a
real 10-document, 5-format corpus (`spike/raw/`):

- longest `<article>`/`<main>` candidate, not the first (GitHub's first
  `<article>` is an author-bio card; Slack's is a related-post card)
- title from `<title>`/`og:title` metadata, never from body text (the
  `<h1>` is often inside a stripped `<header>`)
- truncate at the first related-post/next-post/newsletter marker, so a
  different incident's date or headline can't leak into the text
- strip PDF running-header/footer furniture (`Page 4 of 12  2024-08-06`)
  that pypdf otherwise injects mid-sentence

`tests/test_parse.py` runs the parser against all 10 documents in
`spike/raw/` and asserts each of the above by name.

## Storage

Raw bytes and normalized text both live in `documents.raw_bytes` /
`documents.text` in Postgres for now, not MinIO — the brief's object-store
requirement (§9) is deferred to a later milestone. `app/storage/` is a
narrow interface (`save_raw`) so that swap touches one module, not the
ingest pipeline; `documents.storage_backend` already records which backend
wrote a given row.

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
`documents`/`jobs`/`sources` between tests. HTTP fetches are mocked with
`respx` in unit/integration tests; `tests/test_parse.py` reads real bytes
from `spike/raw/` but performs no network I/O itself.
