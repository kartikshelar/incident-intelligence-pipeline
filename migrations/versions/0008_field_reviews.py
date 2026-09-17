"""field_reviews: per-field review state, decisions and corrections (M4)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model_value", postgresql.JSONB(), nullable=False),
        sa.Column("review_state", sa.String(), nullable=False, server_default="unreviewed"),
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skip_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column("corrected_value", postgresql.JSONB(), nullable=True),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("extraction_id", "field", name="field_reviews_extraction_field_key"),
        sa.CheckConstraint(
            "review_state IN ('unreviewed', 'routed', 'reviewed')",
            name="field_reviews_state_valid",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('accepted', 'corrected')",
            name="field_reviews_decision_valid",
        ),
        sa.CheckConstraint(
            "(review_state = 'reviewed') = "
            "(decision IS NOT NULL AND reviewer IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="field_reviews_reviewed_is_complete",
        ),
        sa.CheckConstraint(
            "(review_state = 'reviewed') OR "
            "(decision IS NULL AND reviewer IS NULL AND reviewed_at IS NULL)",
            name="field_reviews_unreviewed_has_no_decision",
        ),
        sa.CheckConstraint(
            "(decision IS NOT DISTINCT FROM 'corrected') = (corrected_value IS NOT NULL)",
            name="field_reviews_corrected_has_value",
        ),
        sa.CheckConstraint(
            "review_state <> 'routed' OR routed_at IS NOT NULL",
            name="field_reviews_routed_has_time",
        ),
    )
    op.create_index("ix_field_reviews_extraction_id", "field_reviews", ["extraction_id"])
    op.create_index(
        "ix_field_reviews_state_confidence", "field_reviews", ["review_state", "confidence"]
    )

    # Every field of every complete extraction gets a review state
    # (ADR-010 §1), including the extractions that predate this table. All
    # start `unreviewed`: nothing here has been routed yet, so the queue is
    # empty until a routing pass runs against the configured floor
    # (app/review/routing.py — the worker runs one after each completed
    # extraction, and POST /review/route runs one on demand). The
    # confidence keys are the field names (tests/test_extract_schema.py
    # pins FieldConfidence's keys to IncidentRecord's fields, and that has
    # held since v0.1); the record value is copied as JSON so a null field
    # is JSON `null`, matching what the pipeline writes for new rows.
    op.execute(
        sa.text(
            """
            INSERT INTO field_reviews (id, extraction_id, field, confidence, model_value)
            SELECT gen_random_uuid(), e.id, c.key, c.value::float,
                   COALESCE(e.record -> c.key, 'null'::jsonb)
            FROM extractions e, jsonb_each_text(e.per_field_confidence) c
            WHERE e.status = 'complete'
            """
        )
    )


def downgrade() -> None:
    # Drops every review decision and correction with the table. Human
    # labels are not recoverable from anything else, so export
    # (GET /review/corrections) before downgrading.
    op.drop_index("ix_field_reviews_state_confidence", table_name="field_reviews")
    op.drop_index("ix_field_reviews_extraction_id", table_name="field_reviews")
    op.drop_table("field_reviews")
