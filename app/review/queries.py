"""Read side of the review queue.

Presentation order of the queue (`next_for_review`, `list_queue`): fields
the reviewer has not skipped come first, by ascending confidence — the
routing rank. A skipped field goes to the back, ordered by when it was
skipped, so "skip" means "show me everything else first" without changing
the field's rank or state. Reviewed fields are not in the queue.

`list_reviewed` is the closed loop (ADR-010 §6): every human decision,
joined to the extraction and document it was made on, so a gold-set
builder can take corrections (and accepted values) as labels keyed on
`text_hash` — the document's identity under ADR-007 — rather than on a
row id that a re-ingest could change.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from app.review.fields import FieldReview, FieldReviewNotFoundError

# Skipped fields last (NULLS FIRST puts never-skipped ones ahead), never-
# skipped ones by routing rank, skipped ones by when they were skipped.
_QUEUE_ORDER = (
    "ORDER BY fr.last_skipped_at ASC NULLS FIRST, fr.confidence ASC, "
    "fr.created_at ASC, fr.field ASC"
)

_QUEUE_SELECT = """
    SELECT fr.id, fr.extraction_id, fr.field, fr.confidence, fr.model_value,
           fr.review_state, fr.routed_at, fr.skip_count, fr.last_skipped_at,
           e.document_id, e.schema_version, e.run_id, d.title AS document_title,
           d.source_url
    FROM field_reviews fr
    JOIN extractions e ON e.id = fr.extraction_id
    JOIN documents d ON d.id = e.document_id
"""


@dataclasses.dataclass(frozen=True)
class QueuedField:
    id: uuid.UUID
    extraction_id: uuid.UUID
    document_id: uuid.UUID
    field: str
    confidence: float
    model_value: Any
    review_state: str
    routed_at: datetime | None
    skip_count: int
    last_skipped_at: datetime | None
    schema_version: str
    run_id: str
    document_title: str | None
    source_url: str


def list_queue(conn: Connection, *, limit: int = 100, offset: int = 0) -> list[QueuedField]:
    rows = conn.execute(
        text(
            _QUEUE_SELECT
            + " WHERE fr.review_state = 'routed' "
            + _QUEUE_ORDER
            + " LIMIT :limit OFFSET :offset"
        ),
        {"limit": limit, "offset": offset},
    ).mappings()
    return [QueuedField(**dict(r)) for r in rows]


def next_for_review(conn: Connection) -> QueuedField | None:
    items = list_queue(conn, limit=1)
    return items[0] if items else None


def queue_size(conn: Connection) -> int:
    return int(
        conn.execute(
            text("SELECT count(*) FROM field_reviews WHERE review_state = 'routed'")
        ).scalar_one()
    )


@dataclasses.dataclass(frozen=True)
class ReviewItem:
    """One field with everything a reviewer needs to judge it."""

    review: FieldReview
    document_id: uuid.UUID
    document_title: str | None
    source_url: str
    document_text: str
    schema_version: str
    run_id: str
    record: dict[str, Any]


def load_for_review(conn: Connection, field_review_id: uuid.UUID) -> ReviewItem:
    row = (
        conn.execute(
            text(
                """
                SELECT fr.*, e.document_id, e.schema_version, e.run_id, e.record,
                       d.title AS document_title, d.source_url, d.text AS document_text
                FROM field_reviews fr
                JOIN extractions e ON e.id = fr.extraction_id
                JOIN documents d ON d.id = e.document_id
                WHERE fr.id = :id
                """
            ),
            {"id": field_review_id},
        )
        .mappings()
        .fetchone()
    )
    if row is None:
        raise FieldReviewNotFoundError(f"field review {field_review_id} does not exist")
    data = dict(row)
    extra = {
        key: data.pop(key)
        for key in (
            "document_id",
            "schema_version",
            "run_id",
            "record",
            "document_title",
            "source_url",
            "document_text",
        )
    }
    return ReviewItem(review=FieldReview(**data), **extra)


@dataclasses.dataclass(frozen=True)
class ReviewedField:
    """A human decision, with enough provenance to be a gold-set label."""

    id: uuid.UUID
    extraction_id: uuid.UUID
    document_id: uuid.UUID
    text_hash: str
    source_url: str
    document_title: str | None
    schema_version: str
    provider: str
    model: str
    thinking: str
    run_id: str
    field: str
    confidence: float
    model_value: Any
    decision: str
    corrected_value: Any
    reviewer: str
    reviewer_note: str | None
    reviewed_at: datetime
    routed_at: datetime | None


def list_reviewed(
    conn: Connection,
    *,
    decision: str | None = "corrected",
    field: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[ReviewedField]:
    """Reviewed fields, newest decision first. `decision` filters to
    'corrected' (default — the corrections) or 'accepted'; None returns
    both, which is the full human-labelled set."""
    clauses = ["fr.review_state = 'reviewed'"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if decision is not None:
        clauses.append("fr.decision = :decision")
        params["decision"] = decision
    if field is not None:
        clauses.append("fr.field = :field")
        params["field"] = field
    rows = conn.execute(
        text(
            """
            SELECT fr.id, fr.extraction_id, e.document_id, d.text_hash, d.source_url,
                   d.title AS document_title, e.schema_version, e.provider, e.model,
                   e.thinking, e.run_id, fr.field, fr.confidence, fr.model_value,
                   fr.decision, fr.corrected_value, fr.reviewer, fr.reviewer_note,
                   fr.reviewed_at, fr.routed_at
            FROM field_reviews fr
            JOIN extractions e ON e.id = fr.extraction_id
            JOIN documents d ON d.id = e.document_id
            WHERE """
            + " AND ".join(clauses)
            + " ORDER BY fr.reviewed_at DESC, fr.field ASC LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings()
    return [ReviewedField(**dict(r)) for r in rows]
