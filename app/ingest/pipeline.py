"""Ingest a single source: fetch -> hash -> detect format -> parse -> persist.

This is the M2 replacement for the worker's M1 no-op `process_job`. Called
with an open transaction (`conn`) so a successful ingest and the job's
`succeeded` transition commit atomically — a worker crash between "wrote
the document" and "marked the job done" cannot happen, because both are
the same commit.

Idempotent on re-ingest (PROJECT_BRIEF M2): content_hash is a unique
constraint on `documents`. Re-ingesting a source whose content hasn't
changed hits that constraint and this module treats it as success without
writing a duplicate row, rather than erroring.
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


@dataclasses.dataclass(frozen=True)
class IngestResult:
    document_id: uuid.UUID
    content_hash: str
    format: str
    title: str | None
    text_chars: int
    was_duplicate: bool


def ingest_source(conn: Connection, *, source_id: uuid.UUID, source_url: str) -> IngestResult:
    """Fetch `source_url`, normalize it, and persist a `documents` row.

    Raises `app.ingest.errors.TransientIngestError` or `PermanentIngestError`
    (including its `EmptyExtractionError` subclass) on failure; callers
    (the worker) are expected to route those into the job's retry/dead-letter
    handling per ADR-003 and not swallow them.
    """
    fetched = fetch(source_url)
    content_hash = hashlib.sha256(fetched.raw_bytes).hexdigest()

    existing = conn.execute(
        text("SELECT id, format, title, text FROM documents WHERE content_hash = :hash"),
        {"hash": content_hash},
    ).mappings().fetchone()
    if existing is not None:
        return IngestResult(
            document_id=existing["id"],
            content_hash=content_hash,
            format=existing["format"],
            title=existing["title"],
            text_chars=len(existing["text"]),
            was_duplicate=True,
        )

    fmt = detect_format(
        url=fetched.url, content_type=fetched.content_type, raw_bytes=fetched.raw_bytes
    )
    parsed = parse(fetched.raw_bytes, fmt=fmt)
    stored_raw = save_raw(fetched.raw_bytes)

    document_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO documents
                (id, source_id, source_url, content_hash, format, fetched_at,
                 storage_backend, raw_bytes, text, title)
            VALUES
                (:id, :source_id, :source_url, :content_hash, :format, :fetched_at,
                 :storage_backend, :raw_bytes, :text, :title)
            """
        ),
        {
            "id": document_id,
            "source_id": source_id,
            "source_url": fetched.url,
            "content_hash": content_hash,
            "format": fmt,
            "fetched_at": datetime.now(UTC),
            "storage_backend": storage_backend_name(),
            "raw_bytes": stored_raw,
            "text": parsed.text,
            "title": parsed.title,
        },
    )

    return IngestResult(
        document_id=document_id,
        content_hash=content_hash,
        format=fmt,
        title=parsed.title,
        text_chars=len(parsed.text),
        was_duplicate=False,
    )
