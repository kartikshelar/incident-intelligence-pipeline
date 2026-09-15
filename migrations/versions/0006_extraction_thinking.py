"""extractions.thinking: the thinking/effort setting that produced the row

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every row that exists before this migration was produced by an
    # adapter that sent no `thinking` parameter and no effort — which is
    # exactly what the setting value `default` means (app/extract/llm.py),
    # so that is the backfill. The server default is then dropped: new
    # rows must say explicitly what was configured.
    op.add_column(
        "extractions",
        sa.Column("thinking", sa.String(), nullable=False, server_default="default"),
    )
    op.alter_column("extractions", "thinking", server_default=None)

    # Idempotency key gains the thinking setting: the same document, schema,
    # provider and model at a different thinking setting is a different
    # extraction, not a duplicate.
    op.drop_index("ux_extractions_complete", table_name="extractions")
    op.create_index(
        "ux_extractions_complete",
        "extractions",
        ["document_id", "schema_version", "provider", "model", "thinking"],
        unique=True,
        postgresql_where=sa.text("status = 'complete'"),
    )


def downgrade() -> None:
    # Fails if two complete rows for one (document, schema, provider, model)
    # exist at different thinking settings — the index cannot be recreated
    # without choosing which record to keep, and that is not a migration's
    # call.
    op.drop_index("ux_extractions_complete", table_name="extractions")
    op.create_index(
        "ux_extractions_complete",
        "extractions",
        ["document_id", "schema_version", "provider", "model"],
        unique=True,
        postgresql_where=sa.text("status = 'complete'"),
    )
    op.drop_column("extractions", "thinking")
