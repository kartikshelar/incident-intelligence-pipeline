"""M6 part 2: load test against the real pipeline, with a stubbed model.

PROJECT_BRIEF M6: "measure p50/p95 extraction latency and documents/hour
against the real pipeline. Use a stubbed model client for throughput so
the test measures the system, not the API."

What is real here: Postgres (SKIP LOCKED queue, ADR-003), the worker's
claim/process/succeed loop (app.worker.main), ingest (fetch -> detect ->
parse -> persist, app.ingest.pipeline) against real fixture documents from
the spike corpus (real HTML/PDF/markdown bytes, so parsing cost is real),
and extraction's validate-and-retry loop (app.extract.extractor) and
persistence (app.extract.pipeline). What is stubbed: the two things that
would otherwise make this a network benchmark rather than a system
benchmark — the Anthropic API call (a fast, deterministic, always-valid
in-process stub) and outbound HTTP fetch (app.ingest.fetch.fetch is
monkeypatched to return local fixture bytes instead of hitting the real
internet; respx was tried first but its default router state is not
visible across the worker threads this script uses for concurrency, per
respx's own thread-locality — a plain monkeypatch has no such limit).

This intentionally reuses tests/fake_llm.py rather than re-deriving a
schema-valid record: the record shape is nontrivial (app/extract/schema.py)
and the fake is already the one place it is kept correct. Consequently
this script only runs where `tests/` is on the image — Dockerfile.dev, not
the production Dockerfile — same as pytest itself.

Concurrency: N worker "threads" each loop `run_once()` in their own
connection, exactly like N `python -m app.worker.main` processes would
against the same Postgres — SKIP LOCKED is what makes that safe. Real
deployment (render.yaml) runs one worker process; this script defaults to
1 but accepts more to show the queue scales with workers, since that is
the actual claim ADR-003 makes.

Safety rail: refuses to run against any database whose name does not end
in `_loadtest`, so a misconfigured APP_DATABASE_URL can never point this
at the dev or CI database — same discipline as tests/__init__.py's `_test`
suffix, a different suffix so the two never collide if run concurrently.

    APP_DATABASE_URL     must end in _loadtest (default: a local Postgres
                         database `incident_intel_loadtest`)
    DOCUMENTS            number of documents to push through (default 60)
    WORKERS              concurrent worker threads (default 1)
    OUT                  where to write the JSON report
                         (default spike/load_test_report.json)

Usage: python -m scripts.load_test
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, make_url, text

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://postgres:postgres@localhost:5432/incident_intel_loadtest"
)

FIXTURES: tuple[tuple[str, str, str], ...] = (
    # (path under spike/raw, content-type, url the fixture is served at)
    ("cloudflare_2025-11-18.html", "text/html", "https://loadtest.local/cloudflare"),
    ("gitlab_2017-01-31.html", "text/html", "https://loadtest.local/gitlab"),
    ("aws_2025-10-20.html", "text/html", "https://loadtest.local/aws"),
    ("datadog_2023-03-08.html", "text/html", "https://loadtest.local/datadog"),
    ("gcp_2025-06-12.html", "text/html", "https://loadtest.local/gcp"),
    ("github_2018-10-21.html", "text/html", "https://loadtest.local/github"),
    ("roblox_2021-10-28.html", "text/html", "https://loadtest.local/roblox"),
    ("slack_2022-02-22.html", "text/html", "https://loadtest.local/slack"),
    ("crowdstrike_2024-07-19.pdf", "application/pdf", "https://loadtest.local/crowdstrike"),
    ("k8s_2019-02-08.md", "text/markdown", "https://loadtest.local/k8s"),
)


def _ensure_database(url: str) -> None:
    parsed = make_url(url)
    name = parsed.database or ""
    if not name.endswith("_loadtest"):
        raise RuntimeError(
            f"refusing to load-test against database {name!r}: the database name must "
            "end in '_loadtest' (set APP_DATABASE_URL). This script truncates its tables."
        )
    admin = create_engine(parsed.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()


def _reset_schema(engine: Engine) -> None:
    from app.db.schema import metadata

    metadata.drop_all(engine)
    metadata.create_all(engine)


def _register_sources(engine: Engine, count: int) -> list[uuid.UUID]:
    from app.queue import enqueue

    job_ids = []
    with engine.begin() as conn:
        for i in range(count):
            fixture_url = FIXTURES[i % len(FIXTURES)][2] + f"?doc={i}"
            source_id = uuid.uuid4()
            conn.execute(
                text("INSERT INTO sources (id, url) VALUES (:id, :url)"),
                {"id": source_id, "url": fixture_url},
            )
            job_ids.append(enqueue(conn, source_id))
    return job_ids


def _with_marker(raw: bytes, content_type: str, i: int) -> bytes:
    """Append a per-document marker that survives app.ingest.parse's
    normalization for the given content type, so every document's
    text_hash differs (ADR-007's identity hash) even though only 10
    distinct fixture files exist.

    HTML needs the marker inside whichever node _parse_html.py's candidate
    selection (largest of every <article>/<main>, else <body>) picks — an
    HTML comment never survives (BeautifulSoup's get_text() skips comment
    nodes), and a <p> inserted only once before </body> is invisible
    whenever an <article>/<main> elsewhere in the document is selected
    instead. Inserting into every <article>, <main>, AND <body> tag present
    guarantees whichever one the parser picks carries the marker too.
    """
    marker_text = f"Load test document marker {i}."
    if content_type == "text/html":
        marker = f"<p>{marker_text}</p>".encode()
        html = raw
        for open_tag in (b"<article", b"<main", b"<body"):
            # Insert right after the end of the opening tag (its closing
            # '>'), for every occurrence, so nested/duplicate candidates
            # all carry the marker regardless of which one wins.
            out = bytearray()
            pos = 0
            while True:
                idx = html.find(open_tag, pos)
                if idx == -1:
                    out += html[pos:]
                    break
                tag_end = html.find(b">", idx)
                if tag_end == -1:
                    out += html[pos:]
                    break
                out += html[pos : tag_end + 1]
                out += marker
                pos = tag_end + 1
            html = bytes(out)
        return html
    if content_type == "application/pdf":
        # Trailing bytes after %%EOF are invisible to pypdf's extracted
        # text (verified directly); there is no cheap way to inject a new
        # visible page without reconstructing the PDF, so every copy of
        # the PDF fixture shares one text_hash and, after the first, is an
        # idempotent provenance-update no-op (ADR-007) rather than a new
        # document. Documented, not fixed: still exercises the real
        # ingest path end to end, just not as N distinct extractions.
        return raw
    return raw + f"\n\n{marker_text}\n".encode()


def _fixture_bodies(document_count: int) -> dict[str, tuple[bytes, str | None]]:
    """URL -> (raw bytes, content-type) for every document this run will
    register, built from real fixture files under spike/raw/."""
    bodies: dict[str, tuple[bytes, str | None]] = {}
    for i in range(document_count):
        filename, content_type, url = FIXTURES[i % len(FIXTURES)]
        raw = (Path("spike/raw") / filename).read_bytes()
        bodies[f"{url}?doc={i}"] = (_with_marker(raw, content_type, i), content_type)
    return bodies


def _patch_fetch(bodies: dict[str, tuple[bytes, str | None]]) -> None:
    """Replace app.ingest.pipeline's `fetch` with an in-process lookup
    against `bodies`, instead of a real (or respx-mocked) HTTP call.

    Patches the name in app.ingest.pipeline's own namespace, since that
    module did `from app.ingest.fetch import fetch` — rebinding
    app.ingest.fetch.fetch would not affect the reference pipeline.py
    already holds. A plain function swap has no thread-locality issue,
    unlike respx's default router state (see module docstring).
    """
    from app.ingest import pipeline
    from app.ingest.fetch import FetchedDocument

    def fake_fetch(url: str) -> FetchedDocument:
        raw, content_type = bodies[url]
        return FetchedDocument(url=url, content_type=content_type, raw_bytes=raw)

    pipeline.fetch = fake_fetch  # type: ignore[assignment]


def _instrument_extract_latency() -> list[float]:
    """Wrap app.worker.main.process_extract to record real Python-level
    wall-clock duration per call, into the returned (shared, thread-safe by
    GIL-protected list.append) list.

    jobs.claimed_at/succeeded_at (what scripts/latency_report.py uses
    against the real run) cannot be used here: both columns are set via
    SQL `now()` inside ONE transaction (app.worker.main.run_once wraps
    claim-through-succeed in a single `engine.begin()`), and Postgres'
    `now()` returns the transaction's start time on every call within it —
    confirmed by inspection (every row's claimed_at exactly equals its own
    succeeded_at). That is a property of measuring through the database's
    own clock, not a bug in the claim/succeed logic; timing the Python call
    directly sidesteps it.
    """
    from app.worker import main as worker

    durations: list[float] = []
    original = worker.process_extract

    def timed(conn: Any, job: Any) -> None:
        started = time.monotonic()
        try:
            original(conn, job)
        finally:
            durations.append(time.monotonic() - started)

    worker.process_extract = timed  # type: ignore[assignment]
    return durations


def _run_workers(*, worker_count: int, stop: threading.Event) -> None:
    from app.worker import main as worker

    def loop() -> None:
        idle = 0
        while not stop.is_set():
            claimed = worker.run_once()
            idle = 0 if claimed else idle + 1
            if not claimed:
                if idle > 20:  # ~2s of nothing left at this poll interval; done
                    return
                time.sleep(0.1)

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(worker_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=600)


def _percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def main() -> None:
    database_url = os.environ.get("APP_DATABASE_URL", DEFAULT_DATABASE_URL)
    os.environ["APP_DATABASE_URL"] = database_url
    # Extraction needs *a* configured provider/model/thinking to pass
    # app.settings' validation; the stub client below is what actually
    # answers, so these values are never sent anywhere.
    os.environ.setdefault("APP_LLM_PROVIDER", "anthropic")
    os.environ.setdefault("APP_EXTRACTION_MODEL", "load-test-stub")
    os.environ.setdefault("APP_EXTRACTION_THINKING", "disabled")
    os.environ.setdefault("ANTHROPIC_API_KEY", "unused-load-test-key")
    os.environ.setdefault("APP_OTEL_EXPORTER", "console")

    document_count = int(os.environ.get("DOCUMENTS", "60"))
    worker_count = int(os.environ.get("WORKERS", "1"))
    out_path = Path(os.environ.get("OUT", "spike/load_test_report.json"))

    _ensure_database(database_url)

    from app.db.engine import get_engine
    from app.worker import main as worker
    from tests.fake_llm import FakeLLMClient, valid_output_json

    engine = get_engine()
    _reset_schema(engine)

    # One stub client per worker thread would each get their own run_id;
    # a single shared instance keeps every extraction under one run_id,
    # like one `python -m app.worker.main` process would.
    stub = FakeLLMClient([valid_output_json() for _ in range(document_count)])
    worker.get_llm_client.cache_clear()
    worker.get_llm_client = lambda: stub  # type: ignore[assignment]

    print(
        f"load test: {document_count} documents, {worker_count} worker thread(s), "
        f"stubbed LLM client, stubbed HTTP fetch"
    )

    _patch_fetch(_fixture_bodies(document_count))
    latencies = _instrument_extract_latency()
    job_ids = _register_sources(engine, document_count)

    stop = threading.Event()
    wall_start = time.monotonic()
    _run_workers(worker_count=worker_count, stop=stop)
    wall_seconds = time.monotonic() - wall_start

    with engine.connect() as conn:
        job_rows = conn.execute(
            text("SELECT kind, status, count(*) AS n FROM jobs GROUP BY kind, status")
        ).all()
        extraction_count = conn.execute(
            text("SELECT count(*) FROM extractions WHERE status = 'complete'")
        ).scalar_one()

    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)
    docs_per_hour = extraction_count / wall_seconds * 3600 if wall_seconds > 0 else float("nan")

    report: dict[str, Any] = {
        "rendered_at": datetime.now(UTC).isoformat(),
        "kind": "stubbed_throughput",
        "note": (
            "Model client and outbound HTTP are stubbed (see module docstring); this "
            "measures the queue/worker/Postgres system's own overhead, not the "
            "Anthropic API's response time. Real end-to-end latency against the live "
            "API is reported separately from spike/extraction_run_12.json — see "
            "scripts/latency_report.py and the README's load numbers section."
        ),
        "documents": document_count,
        "worker_threads": worker_count,
        "wall_seconds": wall_seconds,
        "extractions_completed": extraction_count,
        "documents_per_hour": docs_per_hour,
        "extraction_latency_seconds": {
            "n": len(latencies),
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
            "mean": statistics.mean(latencies) if latencies else None,
            "p50": p50,
            "p95": p95,
        },
        "jobs_by_kind_status": {
            f"{r.kind}:{r.status}": r.n for r in job_rows
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print(f"documents/hour (stubbed): {docs_per_hour:.1f}")
    print(f"extraction latency p50={p50:.4f}s p95={p95:.4f}s (n={len(latencies)})")
    print(f"jobs: {report['jobs_by_kind_status']}")
    print(f"report written to {out_path}")
    del job_ids  # only used to size the registration loop above


if __name__ == "__main__":
    main()
