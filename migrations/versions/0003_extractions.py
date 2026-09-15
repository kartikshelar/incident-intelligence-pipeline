"""M3: extractions table; job kinds (ingest | extract)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing jobs are all ingest jobs (the only kind before M3); the
    # server default backfills them.
    op.add_column("jobs", sa.Column("kind", sa.String(), nullable=False, server_default="ingest"))
    op.add_column("jobs", sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_check_constraint("jobs_kind_valid", "jobs", "kind IN ('ingest', 'extract')")
    op.create_check_constraint(
        "jobs_extract_has_document", "jobs", "(kind = 'extract') = (document_id IS NOT NULL)"
    )

    op.create_table(
        "extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("record", postgresql.JSONB(), nullable=True),
        sa.Column("per_field_confidence", postgresql.JSONB(), nullable=True),
        sa.Column("confidence_source", sa.String(), nullable=False, server_default="self_report"),
        sa.Column("derived", postgresql.JSONB(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_log", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_kind", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('complete', 'failed')", name="extractions_status_valid"),
        sa.CheckConstraint(
            "(status = 'complete') = (record IS NOT NULL)",
            name="extractions_complete_has_record",
        ),
    )
    op.create_index(
        "ux_extractions_complete",
        "extractions",
        ["document_id", "schema_version", "model"],
        unique=True,
        postgresql_where=sa.text("status = 'complete'"),
    )
    op.create_index("ix_extractions_document_id", "extractions", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_extractions_document_id", table_name="extractions")
    op.drop_index("ux_extractions_complete", table_name="extractions")
    op.drop_table("extractions")
    op.drop_constraint("jobs_extract_has_document", "jobs", type_="check")
    op.drop_constraint("jobs_kind_valid", "jobs", type_="check")
    op.drop_column("jobs", "document_id")
    op.drop_column("jobs", "kind")
