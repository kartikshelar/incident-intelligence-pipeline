"""M2: documents table (fetched + normalized text, with provenance)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "storage_backend", sa.String(), nullable=False, server_default="postgres"
        ),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("content_hash", name="documents_content_hash_key"),
    )

    # Idempotent-ingest lookups (pipeline.ingest_source) and source ->
    # documents traversal both key on these.
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"], unique=True)
    op.create_index("ix_documents_source_id", "documents", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_source_id", table_name="documents")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_table("documents")
