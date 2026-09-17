"""One `field_reviews` row per (complete extraction, top-level field).

Creation happens inside the extraction transaction (app/extract/pipeline.py
calls `create_field_reviews` right after inserting the complete row), so a
complete extraction can never exist without its 23 review states, and a
rolled-back extraction leaves no orphan states behind.

Decisions (`record_decision`) are the write-back half of ADR-010 §6:
  accept   the model's value stands; the field is `reviewed`/`accepted`.
  correct  the human's value is validated against the field's own type
           in app.extract.schema.IncidentRecord and stored in
           `corrected_value`; `model_value` is untouched. A "correction"
           equal to the model's value is refused — that is an accept, and
           calling it a correction would poison the gold set.
  skip     the field stays `routed` (it goes back to the queue) and only
           its skip bookkeeping changes, which app/review/queries.py uses
           to show it after fields the reviewer has not passed over yet.

A decision on an already-reviewed field is refused (`AlreadyReviewedError`):
one human answer per field per extraction. Re-extracting the document is a
new extraction with its own rows (ADR-010 §6).
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Connection, text

from app.extract.schema import RECORD_FIELDS, FieldConfidence, IncidentRecord

Action = Literal["accept", "correct", "skip"]


class ReviewError(Exception):
    """Base for review-state errors the API maps to 4xx responses."""


class FieldReviewNotFoundError(ReviewError, LookupError):
    pass


class AlreadyReviewedError(ReviewError):
    """The field has a recorded decision; it is not reviewed twice."""


class NotRoutedError(ReviewError):
    """Skip only makes sense for a field that is in the queue."""


class InvalidCorrectionError(ReviewError, ValueError):
    """The corrected value does not fit the field's schema, or equals the
    model's value (which is an accept, not a correction)."""


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


def validate_correction(field: str, value: Any) -> Any:
    """Check `value` against the field's type in IncidentRecord and return
    it normalised to JSON-compatible Python (the form it is stored in)."""
    if field not in RECORD_FIELDS:
        raise InvalidCorrectionError(f"{field!r} is not an IncidentRecord field")
    adapter: TypeAdapter[Any] = TypeAdapter(IncidentRecord.model_fields[field].annotation)
    try:
        validated = adapter.validate_python(value)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or field}: {err['msg']}" for err in exc.errors()
        )
        raise InvalidCorrectionError(f"corrected value is not a valid {field}: {problems}") from exc
    return adapter.dump_python(validated, mode="json")


def record_decision(
    conn: Connection,
    field_review_id: uuid.UUID,
    *,
    action: Action,
    reviewer: str | None = None,
    corrected_value: Any = None,
    note: str | None = None,
) -> FieldReview:
    """Apply one reviewer action. Locks the row for the duration of the
    caller's transaction so two reviewers cannot both decide it."""
    row = conn.execute(text(_SELECT + " FOR UPDATE"), {"id": field_review_id}).mappings().fetchone()
    if row is None:
        raise FieldReviewNotFoundError(f"field review {field_review_id} does not exist")
    current = FieldReview(**dict(row))
    if current.review_state == "reviewed":
        raise AlreadyReviewedError(
            f"field {current.field} of extraction {current.extraction_id} was already "
            f"reviewed by {current.reviewer} at {current.reviewed_at:%Y-%m-%dT%H:%M:%S}"
        )

    if action == "skip":
        if current.review_state != "routed":
            raise NotRoutedError(
                f"field {current.field} is {current.review_state}, not routed; "
                "only a queued field can be skipped"
            )
        conn.execute(
            text(
                "UPDATE field_reviews SET skip_count = skip_count + 1, last_skipped_at = now() "
                "WHERE id = :id"
            ),
            {"id": field_review_id},
        )
        return get_field_review(conn, field_review_id)

    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise InvalidCorrectionError("a reviewer identity is required to accept or correct")

    if action == "accept":
        conn.execute(
            text(
                "UPDATE field_reviews SET review_state = 'reviewed', decision = 'accepted', "
                "reviewer = :reviewer, reviewer_note = :note, reviewed_at = now() "
                "WHERE id = :id"
            ),
            {"id": field_review_id, "reviewer": reviewer, "note": note},
        )
        return get_field_review(conn, field_review_id)

    if action == "correct":
        value = validate_correction(current.field, corrected_value)
        if value == current.model_value:
            raise InvalidCorrectionError(
                f"the corrected value equals the extracted value for {current.field}; "
                "use accept, so the gold set records the model as right"
            )
        conn.execute(
            text(
                "UPDATE field_reviews SET review_state = 'reviewed', decision = 'corrected', "
                "corrected_value = CAST(:value AS jsonb), reviewer = :reviewer, "
                "reviewer_note = :note, reviewed_at = now() WHERE id = :id"
            ),
            {
                "id": field_review_id,
                "value": json.dumps(value),
                "reviewer": reviewer,
                "note": note,
            },
        )
        return get_field_review(conn, field_review_id)

    raise ValueError(f"unknown action {action!r}")  # unreachable via the Literal type
