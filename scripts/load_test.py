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

Two concurrency modes, MODE env var:

  threads (default)  N worker threads in ONE process, sharing ONE
                      SQLAlchemy connection pool (app.db.engine.get_engine()
                      is a process-wide @lru_cache). Cheap to run, but not
                      what a real deployment does, and the README's
                      Limitations section already records that this mode's
                      own numbers should not be read as evidence about real
                      multi-process scaling.
  processes           N separate OS processes, each with its own engine,
                      its own connection pool, its own patched fetch/LLM
                      stub — nothing shared except the one Postgres
                      database, exactly like N `python -m app.worker.main`
                      processes (what render.yaml / heroku.yml actually
                      run) or N Heroku/Render worker dynos. This is the
                      mode that actually tests ADR-003 §2's claim: that
                      `SELECT ... FOR UPDATE SKIP LOCKED` lets workers
                      claim jobs concurrently without blocking each other
                      or double-claiming. It instruments the claim itself
                      (app.worker.main.claim_one, patched per subprocess)
                      to count claim attempts that found the queue
                      non-empty but still returned nothing — contention,
                      not just "the queue ran dry" — and to detect any job
                      id returned by more than one claim across every
                      process, which SKIP LOCKED must never allow.

Safety rail: refuses to run against any database whose name does not end
in `_loadtest`, so a misconfigured APP_DATABASE_URL can never point this
at the dev or CI database — same discipline as tests/__init__.py's `_test`
suffix, a different suffix so the two never collide if run concurrently.

    APP_DATABASE_URL     must end in _loadtest (default: a local Postgres
                         database `incident_intel_loadtest`)
    DOCUMENTS            number of documents to push through (default 60)
    WORKERS              concurrent workers, threads or processes per MODE
                         (default 1)
    MODE                 "threads" (default) or "processes"
    OUT                  where to write the JSON report
                         (default spike/load_test_report.json)

Usage: python -m scripts.load_test
"""

from __future__ import annotations

import json
import multiprocessing
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


# ---------------------------------------------------------------------
# Process mode: N separate OS processes, each with its own engine/pool,
# nothing shared but Postgres. See module docstring.
# ---------------------------------------------------------------------


def _patch_claim_with_contention_tracking(
    *, empty_while_queued: list[int], claimed_ids: list[str]
) -> None:
    """Wrap app.worker.main.claim_one (the name that module imported from
    app.queue, so this is the same pattern _patch_fetch uses on
    app.ingest.pipeline.fetch) so every claim attempt is observable:

    - empty_while_queued: incremented, in the SAME transaction as the
      claim, whenever `status='queued'` rows existed at the moment of the
      attempt but the claim still returned nothing. That is the SKIP
      LOCKED contention signature this load test exists to measure —
      distinct from the ordinary "queue is empty, stop polling" case,
      which this never counts (checked before the claim, so a genuinely
      empty queue contributes nothing here).
    - claimed_ids: every job id any successful claim in this PROCESS
      returned, appended in order. The orchestrator merges every
      process's list afterward and checks for an id appearing twice
      anywhere — across all processes, not just within one — which SKIP
      LOCKED must never allow.
    """
    from app.worker import main as worker

    original_claim_one = worker.claim_one

    def instrumented_claim_one(conn: Any) -> Any:
        queued_before = conn.execute(
            text("SELECT count(*) FROM jobs WHERE status = 'queued'")
        ).scalar_one()
        job = original_claim_one(conn)
        if job is None:
            if queued_before > 0:
                empty_while_queued.append(1)
        else:
            claimed_ids.append(str(job.id))
        return job

    worker.claim_one = instrumented_claim_one  # type: ignore[assignment]


def _subprocess_worker_entry(
    *,
    database_url: str,
    document_count: int,
    fixture_bodies: dict[str, tuple[bytes, str | None]],
    result_queue: multiprocessing.Queue[dict[str, Any]],
) -> None:
    """Entry point for one worker PROCESS. Runs in a fresh Python
    interpreter (multiprocessing 'spawn' — the only start method on
    Windows, and what this repo assumes rather than relying on 'fork'
    semantics it would only have on Linux) — nothing from the parent
    process's monkeypatches, engine, or imports carries over. Every setup
    step _patch_fetch/main() does in the single-process/threaded path is
    repeated here, independently, which is the point: this is what a real
    second worker process actually looks like.
    """
    os.environ["APP_DATABASE_URL"] = database_url
    os.environ.setdefault("APP_LLM_PROVIDER", "anthropic")
    os.environ.setdefault("APP_EXTRACTION_MODEL", "load-test-stub")
    os.environ.setdefault("APP_EXTRACTION_THINKING", "disabled")
    os.environ.setdefault("ANTHROPIC_API_KEY", "unused-load-test-key")
    os.environ.setdefault("APP_OTEL_EXPORTER", "console")

    from app.worker import main as worker
    from tests.fake_llm import FakeLLMClient, valid_output_json

    # Each process gets its own stub client and therefore its own run_id
    # (tests.fake_llm.FakeLLMClient's default). That is correct, not a
    # limitation: every extract job is claimed by exactly one process
    # (that is the property under test), so no document is ever extracted
    # by two different run_ids — app.extract.pipeline's idempotency check
    # never has two processes racing to write the same row.
    stub = FakeLLMClient([valid_output_json() for _ in range(document_count)])
    worker.get_llm_client.cache_clear()
    worker.get_llm_client = lambda: stub  # type: ignore[assignment]

    _patch_fetch(fixture_bodies)

    empty_while_queued: list[int] = []
    claimed_ids: list[str] = []
    _patch_claim_with_contention_tracking(
        empty_while_queued=empty_while_queued, claimed_ids=claimed_ids
    )
    latencies = _instrument_extract_latency()

    idle = 0
    while True:
        claimed = worker.run_once()
        idle = 0 if claimed else idle + 1
        if not claimed:
            if idle > 20:  # ~2s of nothing left at this poll interval; done
                break
            time.sleep(0.1)

    result_queue.put(
        {
            "claimed_ids": claimed_ids,
            "empty_while_queued": len(empty_while_queued),
            "latencies": latencies,
        }
    )


def _run_worker_processes(
    *,
    worker_count: int,
    database_url: str,
    document_count: int,
    fixture_bodies: dict[str, tuple[bytes, str | None]],
) -> tuple[list[float], int, list[str]]:
    """Spawns worker_count separate processes, each running
    _subprocess_worker_entry, and merges their results.

    Returns (all_extract_latencies, total_empty_while_queued,
    all_claimed_job_ids) — the last one still possibly containing
    duplicates, which is exactly what the caller checks for.
    """
    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue[dict[str, Any]] = ctx.Queue()
    procs = [
        ctx.Process(
            target=_subprocess_worker_entry,
            kwargs={
                "database_url": database_url,
                "document_count": document_count,
                "fixture_bodies": fixture_bodies,
                "result_queue": result_queue,
            },
        )
        for _ in range(worker_count)
    ]
    for p in procs:
        p.start()

    results = [result_queue.get(timeout=600) for _ in procs]
    for p in procs:
        p.join(timeout=600)

    all_latencies: list[float] = []
    total_empty_while_queued = 0
    all_claimed_ids: list[str] = []
    for r in results:
        all_latencies.extend(r["latencies"])
        total_empty_while_queued += r["empty_while_queued"]
        all_claimed_ids.extend(r["claimed_ids"])
    return all_latencies, total_empty_while_queued, all_claimed_ids


def _find_duplicate_claims(claimed_ids: list[str]) -> list[str]:
    """Every job id that appears more than once across every process's
    claimed_ids list, sorted for a stable report. SKIP LOCKED's guarantee
    is that this is always empty; a non-empty result means two workers
    claimed the same row, which ADR-003 says cannot happen."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for jid in claimed_ids:
        if jid in seen:
            duplicates.add(jid)
        else:
            seen.add(jid)
    return sorted(duplicates)


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
    mode = os.environ.get("MODE", "threads")
    if mode not in ("threads", "processes"):
        raise SystemExit(f"MODE={mode!r} is not valid (expected 'threads' or 'processes')")
    out_path = Path(os.environ.get("OUT", "spike/load_test_report.json"))

    _ensure_database(database_url)

    from app.db.engine import get_engine

    engine = get_engine()
    _reset_schema(engine)

    fixture_bodies = _fixture_bodies(document_count)
    unit = "process(es)" if mode == "processes" else "thread(s)"
    concurrency_label = f"{worker_count} worker {unit}"
    print(
        f"load test: {document_count} documents, {concurrency_label}, MODE={mode}, "
        f"stubbed LLM client, stubbed HTTP fetch"
    )

    job_ids = _register_sources(engine, document_count)

    claim_contention: dict[str, Any] | None = None
    if mode == "processes":
        wall_start = time.monotonic()
        latencies, empty_while_queued, claimed_ids = _run_worker_processes(
            worker_count=worker_count,
            database_url=database_url,
            document_count=document_count,
            fixture_bodies=fixture_bodies,
        )
        wall_seconds = time.monotonic() - wall_start

        # SKIP LOCKED's whole guarantee, checked directly: no job id may be
        # returned by more than one successful claim, anywhere, across any
        # number of concurrent processes.
        duplicate_claims = _find_duplicate_claims(claimed_ids)
        claim_contention = {
            "claim_attempts_empty_while_queued": empty_while_queued,
            "total_successful_claims": len(claimed_ids),
            "jobs_claimed_more_than_once": duplicate_claims,
            "skip_locked_holds": len(duplicate_claims) == 0,
        }
    else:
        from app.worker import main as worker
        from tests.fake_llm import FakeLLMClient, valid_output_json

        # One stub client per worker thread would each get their own
        # run_id; a single shared instance keeps every extraction under
        # one run_id, like one `python -m app.worker.main` process would.
        # (Process mode can't share a Python object across processes and
        # doesn't need to — see _subprocess_worker_entry.)
        stub = FakeLLMClient([valid_output_json() for _ in range(document_count)])
        worker.get_llm_client.cache_clear()
        worker.get_llm_client = lambda: stub  # type: ignore[assignment]

        _patch_fetch(fixture_bodies)
        latencies = _instrument_extract_latency()

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
        "mode": mode,
        "note": (
            "Model client and outbound HTTP are stubbed (see module docstring); this "
            "measures the queue/worker/Postgres system's own overhead, not the "
            "Anthropic API's response time. Real end-to-end latency against the live "
            "API is reported separately from spike/extraction_run_12.json — see "
            "scripts/latency_report.py and the README's load numbers section. "
            + (
                "mode=threads: N threads share ONE connection pool in ONE process — "
                "not what a real deployment does; see the README's Limitations."
                if mode == "threads"
                else "mode=processes: N separate OS processes, each with its own "
                "connection pool, nothing shared but Postgres — the same shape as "
                "N real worker processes/dynos, and the mode that actually tests "
                "ADR-003's SKIP LOCKED concurrency claim (see claim_contention)."
            )
        ),
        "documents": document_count,
        "worker_count": worker_count,
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
    if claim_contention is not None:
        report["claim_contention"] = claim_contention

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print(f"documents/hour (stubbed): {docs_per_hour:.1f}")
    print(f"extraction latency p50={p50:.4f}s p95={p95:.4f}s (n={len(latencies)})")
    print(f"jobs: {report['jobs_by_kind_status']}")
    if claim_contention is not None:
        print(
            f"claim contention: {claim_contention['claim_attempts_empty_while_queued']} "
            f"empty-while-queued attempt(s) out of "
            f"{claim_contention['total_successful_claims']} successful claims; "
            f"SKIP LOCKED holds: {claim_contention['skip_locked_holds']}"
            + (
                ""
                if claim_contention["skip_locked_holds"]
                else f" — DUPLICATE CLAIMS: {claim_contention['jobs_claimed_more_than_once']}"
            )
        )
    print(f"report written to {out_path}")
    del job_ids  # only used to size the registration loop above


if __name__ == "__main__":
    main()
