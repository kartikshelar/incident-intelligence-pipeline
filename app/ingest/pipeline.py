"""Ingest a single source: fetch -> hash -> detect format -> parse -> persist.

This is the M2 replacement for the worker's M1 no-op `process_job`. Called
with an open transaction (`conn`) so a successful ingest and the job's
`succeeded` transition commit atomically — a worker crash between "wrote
the document" and "marked the job done" cannot happen, because both are
the same commit.

Two hashes, two jobs (ADR-007):

  content_hash  sha256 of the raw bytes. The STORAGE key: it names the
                bytes in `raw_bytes` and is unique. A fetch whose bytes are
                already stored is a no-op before any parsing.
  text_hash     sha256 of the extracted text. The document's IDENTITY and
                the ingest idempotency key: two fetches with the same text
                are the same document, whatever the bytes did.

Re-ingest semantics (DERIVE-07):
  same bytes                 -> no-op, return the existing document
  new bytes, same text       -> the existing document's provenance
                                (final URL, fetched_at, content_hash,
                                raw_bytes) is updated in place; no new row
  new bytes, new text        -> a new document
"""

import dataclasses
import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import Connection, text

from app.ingest.detect import detect_format
from app.ingest.fetch import fetch
from app.ingest.parse import parse
from app.storage import save_raw, storage_backend_name
from app.telemetry.tracing import span


@dataclasses.dataclass(frozen=True)
class IngestResult:
    document_id: uuid.UUID
    content_hash: str
    text_hash: str
    format: str
    title: str | None
    text_chars: int
    # True when no new document row was written (either hash matched).
    was_duplicate: bool
    # True when the bytes were new but the text matched an existing
    # document, whose provenance was updated in place (ADR-007).
    provenance_updated: bool


def text_hash_of(document_text: str) -> str:
    """The document identity hash: sha256 over the UTF-8 extracted text.
    Must agree with migration 0005's SQL backfill
    (`encode(sha256(convert_to(text, 'UTF8')), 'hex')`)."""
    return hashlib.sha256(document_text.encode("utf-8")).hexdigest()


def ingest_source(conn: Connection, *, source_id: uuid.UUID, source_url: str) -> IngestResult:
    """Fetch `source_url`, normalize it, and persist or update a `documents` row.

    Raises `app.ingest.errors.TransientIngestError` or `PermanentIngestError`
    (including its `EmptyExtractionError` subclass) on failure; callers
    (the worker) are expected to route those into the job's retry/dead-letter
    handling per ADR-003 and not swallow them.
    """
    with span("ingest_source", {"source.id": str(source_id), "source.url": source_url}) as outer:
        result = _ingest_source(conn, source_id=source_id, source_url=source_url)
        outer.set_attributes(
            {
                "document.id": str(result.document_id),
                "document.format": result.format,
                "document.was_duplicate": result.was_duplicate,
            }
        )
        return result


def _ingest_source(conn: Connection, *, source_id: uuid.UUID, source_url: str) -> IngestResult:
    with span("fetch", {"source.url": source_url}) as fetch_span:
        fetched = fetch(source_url)
        fetch_span.set_attribute("fetch.bytes", len(fetched.raw_bytes))
    content_hash = hashlib.sha256(fetched.raw_bytes).hexdigest()

    # Cheapest check first: these exact bytes are already stored, so the
    # text is too (the parser is deterministic). Nothing to parse or write.
    same_bytes = conn.execute(
        text(
            "SELECT id, text_hash, format, title, length(text) AS text_chars "
            "FROM documents WHERE content_hash = :hash"
        ),
        {"hash": content_hash},
    ).mappings().fetchone()
    if same_bytes is not None:
        return IngestResult(
            document_id=same_bytes["id"],
            content_hash=content_hash,
            text_hash=same_bytes["text_hash"],
            format=same_bytes["format"],
            title=same_bytes["title"],
            text_chars=same_bytes["text_chars"],
            was_duplicate=True,
            provenance_updated=False,
        )

    fmt = detect_format(
        url=fetched.url, content_type=fetched.content_type, raw_bytes=fetched.raw_bytes
    )
    with span("parse", {"document.format": fmt}) as parse_span:
        parsed = parse(fetched.raw_bytes, fmt=fmt)
        parse_span.set_attribute("parse.text_chars", len(parsed.text))
    text_hash = text_hash_of(parsed.text)
    stored_raw = save_raw(fetched.raw_bytes)
    fetched_at = datetime.now(UTC)

    # New bytes, known text: the same document, re-served with different
    # chrome (nonces, CSP headers — the AWS/GCP case in ADR-007). Keep the
    # row and its extractions; move its provenance to this fetch so
    # content_hash and raw_bytes always describe the bytes actually stored.
    same_text = conn.execute(
        text(
            "SELECT id, format, title, length(text) AS text_chars "
            "FROM documents WHERE text_hash = :hash"
        ),
        {"hash": text_hash},
    ).mappings().fetchone()
    if same_text is not None:
        conn.execute(
            text(
                """
                UPDATE documents
                SET source_url = :source_url,
                    fetched_at = :fetched_at,
                    content_hash = :content_hash,
                    raw_bytes = :raw_bytes,
                    storage_backend = :storage_backend
                WHERE id = :id
                """
            ),
            {
                "id": same_text["id"],
                "source_url": fetched.url,
                "fetched_at": fetched_at,
                "content_hash": content_hash,
                "raw_bytes": stored_raw,
                "storage_backend": storage_backend_name(),
            },
        )
        return IngestResult(
            document_id=same_text["id"],
            content_hash=content_hash,
            text_hash=text_hash,
            format=same_text["format"],
            title=same_text["title"],
            text_chars=same_text["text_chars"],
            was_duplicate=True,
            provenance_updated=True,
        )

    document_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO documents
                (id, source_id, source_url, content_hash, text_hash, format, fetched_at,
                 storage_backend, raw_bytes, text, title)
            VALUES
                (:id, :source_id, :source_url, :content_hash, :text_hash, :format, :fetched_at,
                 :storage_backend, :raw_bytes, :text, :title)
            """
        ),
        {
            "id": document_id,
            "source_id": source_id,
            "source_url": fetched.url,
            "content_hash": content_hash,
            "text_hash": text_hash,
            "format": fmt,
            "fetched_at": fetched_at,
            "storage_backend": storage_backend_name(),
            "raw_bytes": stored_raw,
            "text": parsed.text,
            "title": parsed.title,
        },
    )

    return IngestResult(
        document_id=document_id,
        content_hash=content_hash,
        text_hash=text_hash,
        format=fmt,
        title=parsed.title,
        text_chars=len(parsed.text),
        was_duplicate=False,
        provenance_updated=False,
    )
