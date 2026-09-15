"""Extract one document: load text -> run the loop -> persist an
`extractions` row, complete or failed.

Called with an open transaction (`conn`), like ingest_source: the
extractions row and the job's status transition commit together. On
failure this module writes the failed row *and then re-raises*, so the
worker still routes the job (requeue / dead_letter) — the row is the
record, the exception is the routing. Nothing is swallowed.

Consequence for callers: catch the exception INSIDE the transaction (as
app.worker.main.run_once does) and commit. Letting it escape
`engine.begin()` rolls the failed row back along with everything else.

Idempotent under at-least-once delivery (ADR-003 §4): a document that
already has a `complete` row for this schema version, provider and model
is a no-op. Failed rows never block a retry; they accumulate, one per
attempt-set, which is the audit trail.

Provider and model are taken from the client (`client.provider`,
`client.model`) — the configured identity, so idempotency keys on what
was asked for, not on whatever string the API echoes back (that is kept
per attempt in `attempt_log`).

Title (FINDINGS §4.7): if the parser found a metadata title, it wins over
whatever the model wrote, and title_source says so.
"""

from __future__ import annotations

import dataclasses
import json
import uuid

from sqlalchemy import Connection, text

from app.extract.derive import derive_durations
from app.extract.errors import ExtractionError, TransientExtractionError
from app.extract.extractor import Attempt, extract
from app.extract.llm import LLMClient
from app.extract.schema import SCHEMA_VERSION


@dataclasses.dataclass(frozen=True)
class ExtractOutcome:
    extraction_id: uuid.UUID
    document_id: uuid.UUID
    was_duplicate: bool
    attempts: int


class DocumentNotFoundError(LookupError):
    pass


def extract_document(
    conn: Connection,
    *,
    document_id: uuid.UUID,
    client: LLMClient,
    max_attempts: int,
) -> ExtractOutcome:
    provider, model = client.provider, client.model
    existing = (
        conn.execute(
            text(
                "SELECT id, attempts FROM extractions "
                "WHERE document_id = :document_id AND schema_version = :schema_version "
                "AND provider = :provider AND model = :model AND status = 'complete'"
            ),
            {
                "document_id": document_id,
                "schema_version": SCHEMA_VERSION,
                "provider": provider,
                "model": model,
            },
        )
        .mappings()
        .fetchone()
    )
    if existing is not None:
        return ExtractOutcome(
            extraction_id=existing["id"],
            document_id=document_id,
            was_duplicate=True,
            attempts=existing["attempts"],
        )

    doc = (
        conn.execute(
            text("SELECT source_url, title, text FROM documents WHERE id = :id"),
            {"id": document_id},
        )
        .mappings()
        .fetchone()
    )
    if doc is None:
        raise DocumentNotFoundError(f"document {document_id} does not exist")

    try:
        result = extract(
            document_text=doc["text"],
            document_title=doc["title"],
            source_url=doc["source_url"],
            client=client,
            max_attempts=max_attempts,
        )
    except ExtractionError as exc:
        kind = "transient" if isinstance(exc, TransientExtractionError) else "permanent"
        _insert_failed(
            conn,
            document_id=document_id,
            provider=provider,
            model=model,
            error=exc,
            error_kind=kind,
            attempts=exc.attempts,
        )
        raise
    except Exception as exc:  # noqa: BLE001 - record it, then let the worker route it
        _insert_failed(
            conn,
            document_id=document_id,
            provider=provider,
            model=model,
            error=exc,
            error_kind="unexpected",
            attempts=[],
        )
        raise

    record = result.output.record
    if doc["title"]:
        record = record.model_copy(
            update={"title": doc["title"], "title_source": "document_metadata"}
        )

    extraction_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO extractions
                (id, document_id, schema_version, provider, model, status, record,
                 per_field_confidence, confidence_source, derived, attempts,
                 attempt_log, usage, error, error_kind)
            VALUES
                (:id, :document_id, :schema_version, :provider, :model, 'complete', :record,
                 :per_field_confidence, 'self_report', :derived, :attempts,
                 :attempt_log, :usage, NULL, NULL)
            """
        ),
        {
            "id": extraction_id,
            "document_id": document_id,
            "schema_version": SCHEMA_VERSION,
            "provider": provider,
            "model": model,
            "record": record.model_dump_json(),
            "per_field_confidence": result.output.confidence.model_dump_json(),
            "derived": json.dumps(derive_durations(record)),
            "attempts": len(result.attempts),
            "attempt_log": _attempt_log_json(result.attempts),
            "usage": json.dumps(result.usage_totals),
        },
    )
    return ExtractOutcome(
        extraction_id=extraction_id,
        document_id=document_id,
        was_duplicate=False,
        attempts=len(result.attempts),
    )


def _insert_failed(
    conn: Connection,
    *,
    document_id: uuid.UUID,
    provider: str,
    model: str,
    error: BaseException,
    error_kind: str,
    attempts: list[Attempt],
) -> None:
    usage: dict[str, int] = {}
    for attempt in attempts:
        for key, value in attempt.usage.items():
            usage[key] = usage.get(key, 0) + value
    conn.execute(
        text(
            """
            INSERT INTO extractions
                (id, document_id, schema_version, provider, model, status, record,
                 per_field_confidence, confidence_source, derived, attempts,
                 attempt_log, usage, error, error_kind)
            VALUES
                (:id, :document_id, :schema_version, :provider, :model, 'failed', NULL,
                 NULL, 'self_report', NULL, :attempts,
                 :attempt_log, :usage, :error, :error_kind)
            """
        ),
        {
            "id": uuid.uuid4(),
            "document_id": document_id,
            "schema_version": SCHEMA_VERSION,
            "provider": provider,
            "model": model,
            "attempts": len(attempts),
            "attempt_log": _attempt_log_json(attempts),
            "usage": json.dumps(usage),
            "error": f"{type(error).__name__}: {error}",
            "error_kind": error_kind,
        },
    )


def _attempt_log_json(attempts: list[Attempt]) -> str:
    return json.dumps([attempt.to_json() for attempt in attempts])
