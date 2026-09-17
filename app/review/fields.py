"""One `field_reviews` row per (complete extraction, top-level field).

Creation happens inside the extraction transaction (app/extract/pipeline.py
calls `create_field_reviews` right after inserting the complete row), so a
complete extraction can never exist without its 23 review states, and a
rolled-back extraction leaves no orphan states behind.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, text

from app.extract.schema import RECORD_FIELDS, FieldConfidence, IncidentRecord


class ReviewError(Exception):
    """Base for review-state errors the API maps to 4xx responses."""


class FieldReviewNotFoundError(ReviewError, LookupError):
    pass


@dataclasses.dataclass(frozen=True)
class FieldReview:
    id: uuid.UUID
    extraction_id: uuid.UUID
    field: str
    confidence: float
    model_value: Any
    review_state: str
    routed_at: datetime | None
    skip_count: int
    last_skipped_at: datetime | None
    decision: str | None
    corrected_value: Any
    reviewer: str | None
    reviewer_note: str | None
    reviewed_at: datetime | None
    created_at: datetime


def create_field_reviews(
    conn: Connection,
    *,
    extraction_id: uuid.UUID,
    record: IncidentRecord,
    confidence: FieldConfidence,
) -> int:
    """Insert one `unreviewed` row per top-level record field. Returns the
    number of rows written (always len(RECORD_FIELDS))."""
    record_json = record.model_dump(mode="json")
    confidence_json = confidence.model_dump()
    rows = [
        {
            "id": uuid.uuid4(),
            "extraction_id": extraction_id,
            "field": field,
            "confidence": confidence_json[field],
            # json.dumps so a None field is stored as JSON null, not SQL NULL.
            "model_value": json.dumps(record_json[field]),
        }
        for field in RECORD_FIELDS
    ]
    conn.execute(
        text(
            "INSERT INTO field_reviews (id, extraction_id, field, confidence, model_value) "
            "VALUES (:id, :extraction_id, :field, :confidence, CAST(:model_value AS jsonb))"
        ),
        rows,
    )
    return len(rows)


_SELECT = "SELECT * FROM field_reviews WHERE id = :id"


def get_field_review(conn: Connection, field_review_id: uuid.UUID) -> FieldReview:
    row = conn.execute(text(_SELECT), {"id": field_review_id}).mappings().fetchone()
    if row is None:
        raise FieldReviewNotFoundError(f"field review {field_review_id} does not exist")
    return FieldReview(**dict(row))
