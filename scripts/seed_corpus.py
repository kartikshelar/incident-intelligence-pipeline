"""Seed a running instance with the 30-document corpus, read-only in the
sense that matters: extraction is REPLAYED from the frozen
spike/extraction_run_12.json report, never re-called against the LLM, so
running this against a public deployment costs no API credits and is
byte-for-byte the same record every time.

Ingest still runs for real (fetch -> detect -> parse -> persist,
app.ingest.pipeline), because that is what makes the seeded documents'
`text` real and the demo honest — a fabricated `documents.text` would make
the review UI show source context that was never actually derived from
the cited URL. Of the 30 corpus documents, 10 (the M0 subset, A-J) have
their raw bytes saved locally under spike/raw/ and are served from there,
no network; the other 20 (K-AD, the gold-set expansion corpus) were never
saved locally and are fetched once, for real, over HTTP — free, since only
ingest touches the network here, never the model.

Idempotent (ADR-007 + app.extract.pipeline's own duplicate check): running
this twice against the same database re-fetches nothing new and inserts
no duplicate rows. Safe to run on every deploy (render.yaml's release
phase does exactly that).

No queue jobs are ever enqueued (see _seed_source_and_document): this
script writes `documents` and `extractions` rows directly, so nothing it
does is later picked up and re-run for real by a live worker process. That
matters — an `extract` job left `queued` would eventually be claimed and
would call the real Anthropic API, spending money on data this script
already wrote for free.

    APP_DATABASE_URL   the instance's database (no special suffix
                       required, unlike scripts/load_test.py — this is
                       meant to run against the real deployed database)
    MANIFEST           default spike/corpus_manifest.json
    RUN_REPORT         default spike/extraction_run_12.json

Usage: python -m scripts.seed_corpus
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from app.db.engine import get_engine
from app.extract.derive import derive_durations
from app.extract.schema import SCHEMA_VERSION, ExtractionOutput
from app.ingest import pipeline as ingest_pipeline
from app.ingest.fetch import FetchedDocument
from app.ingest.fetch import fetch as real_fetch
from app.review.fields import create_field_reviews
from app.review.routing import route_pending
from app.settings import settings


def _local_fetch(url: str, *, local_bodies: dict[str, tuple[bytes, str]]) -> FetchedDocument:
    if url in local_bodies:
        raw, content_type = local_bodies[url]
        return FetchedDocument(url=url, content_type=content_type, raw_bytes=raw)
    return real_fetch(url)


_CONTENT_TYPES = {
    "blog_html": "text/html",
    "vendor_post_event_summary_html": "text/html",
    "status_page_html": "text/html",
    "mailing_list_html": "text/html",
    "markdown_in_repo": "text/markdown",
    "pdf": "application/pdf",
}


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["documents"]


def _local_bodies(manifest: list[dict[str, Any]]) -> dict[str, tuple[bytes, str]]:
    bodies: dict[str, tuple[bytes, str]] = {}
    for doc in manifest:
        raw_file = doc.get("raw_file")
        if not raw_file:
            continue
        content_type = _CONTENT_TYPES.get(doc["format"], "text/html")
        bodies[doc["url"]] = (Path(raw_file).read_bytes(), content_type)
    return bodies


def _seed_source_and_document(conn: Connection, url: str) -> uuid.UUID:
    """Register + ingest one source for real, returning the document id.
    Idempotent: re-registering a URL whose content is already ingested
    (ADR-007) does not create a duplicate document.

    Deliberately does NOT enqueue an ingest or extract job: this script
    calls app.ingest.pipeline.ingest_source directly and replays the
    extraction itself, so a job for either stage would just sit `queued`
    for a live worker to pick up later — for `extract`, that means a real
    Anthropic API call the moment a worker claims it, silently spending
    money for data this script already wrote for free. Confirmed the hard
    way: an earlier version of this script did enqueue jobs, and the
    compose worker started claiming and completing them within seconds.
    """
    existing = conn.execute(
        text("SELECT id FROM sources WHERE url = :url"), {"url": url}
    ).scalar()
    if existing is not None:
        source_id = existing
    else:
        source_id = uuid.uuid4()
        conn.execute(
            text("INSERT INTO sources (id, url) VALUES (:id, :url)"), {"id": source_id, "url": url}
        )

    result = ingest_pipeline.ingest_source(conn, source_id=source_id, source_url=url)
    return result.document_id


def _replay_extraction(
    conn: Connection, *, document_id: uuid.UUID, report_doc: dict[str, Any]
) -> None:
    """Insert the extraction row exactly as spike/extraction_run_12.json
    recorded it, if this document/schema/provider/model/thinking/run
    combination is not already present (same idempotency key
    app.extract.pipeline.extract_document uses)."""
    ext = report_doc["extraction"]
    if ext["status"] != "complete":
        return  # nothing to replay; a failed row from a real run is not seed data

    existing = conn.execute(
        text(
            "SELECT id FROM extractions WHERE document_id = :document_id "
            "AND schema_version = :schema_version AND provider = :provider "
            "AND model = :model AND thinking = :thinking AND run_id = :run_id "
            "AND status = 'complete'"
        ),
        {
            "document_id": document_id,
            "schema_version": ext["schema_version"],
            "provider": ext["provider"],
            "model": ext["model"],
            "thinking": ext["thinking"],
            "run_id": ext["run_id"],
        },
    ).scalar()
    if existing is not None:
        return

    # Validated against the CURRENT schema, not trusted blindly: if the
    # stored record no longer satisfies app.extract.schema.ExtractionOutput
    # (a schema version bump since the report was written), this raises
    # loudly instead of writing a row the rest of the app cannot read back.
    output = ExtractionOutput.model_validate(
        {"record": ext["record"], "confidence": ext["per_field_confidence"]}
    )
    if ext["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError(
            f"extraction_run_12.json was written at schema {ext['schema_version']!r}, "
            f"but app.extract.schema.SCHEMA_VERSION is now {SCHEMA_VERSION!r}. Re-run "
            "extraction (scripts/extraction_run.py) against the current schema and point "
            "RUN_REPORT at the new report before seeding."
        )

    extraction_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO extractions
                (id, document_id, schema_version, provider, model, thinking, run_id, status,
                 record, per_field_confidence, confidence_source, derived, attempts,
                 attempt_log, usage, error, error_kind)
            VALUES
                (:id, :document_id, :schema_version, :provider, :model, :thinking, :run_id,
                 'complete', :record, :per_field_confidence, 'self_report', :derived, :attempts,
                 :attempt_log, :usage, NULL, NULL)
            """
        ),
        {
            "id": extraction_id,
            "document_id": document_id,
            "schema_version": ext["schema_version"],
            "provider": ext["provider"],
            "model": ext["model"],
            "thinking": ext["thinking"],
            "run_id": ext["run_id"],
            "record": output.record.model_dump_json(),
            "per_field_confidence": output.confidence.model_dump_json(),
            "derived": json.dumps(derive_durations(output.record)),
            "attempts": ext["validation_attempts"],
            "attempt_log": json.dumps(ext["attempt_log"]),
            "usage": json.dumps(ext["usage"]),
        },
    )
    create_field_reviews(
        conn, extraction_id=extraction_id, record=output.record, confidence=output.confidence
    )


def main() -> None:
    manifest_path = Path(os.environ.get("MANIFEST", "spike/corpus_manifest.json"))
    report_path = Path(os.environ.get("RUN_REPORT", "spike/extraction_run_12.json"))

    manifest = _load_manifest(manifest_path)
    report = json.loads(report_path.read_text())
    report_by_id = {d["id"]: d for d in report["documents"]}
    local_bodies = _local_bodies(manifest)

    def patched_fetch(url: str) -> FetchedDocument:
        return _local_fetch(url, local_bodies=local_bodies)

    ingest_pipeline.fetch = patched_fetch  # type: ignore[assignment]

    engine = get_engine()
    ingested, replayed, skipped = 0, 0, 0
    for doc in manifest:
        report_doc = report_by_id.get(doc["id"])
        if report_doc is None:
            skipped += 1
            print(f"skip {doc['id']}: not present in {report_path}")
            continue

        with engine.begin() as conn:
            document_id = _seed_source_and_document(conn, doc["url"])
            ingested += 1
            _replay_extraction(conn, document_id=document_id, report_doc=report_doc)
            replayed += 1
        print(f"seeded {doc['id']} ({doc['org']}): document {document_id}")

    # Same routing pass the worker runs after every real extraction
    # (app.worker.main.process_extract, ADR-010): without it the seeded
    # review queue is always empty, which would make the review UI look
    # broken on a fresh deploy rather than showing what it is for.
    with engine.begin() as conn:
        routing = route_pending(
            conn, floor=settings.review_confidence_floor, budget=settings.review_budget
        )

    print(
        f"done: {ingested} document(s) ingested, {replayed} extraction(s) replayed, "
        f"{skipped} skipped (not in {report_path.name}), "
        f"{len(routing.routed)} field(s) routed for review"
    )


if __name__ == "__main__":
    main()
