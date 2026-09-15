"""extractions.provider: which LLM provider produced the row

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every row that exists before this migration was produced through the
    # Anthropic API — it was the only implementation — so that is the
    # backfill. The default is then dropped: new rows must say explicitly.
    op.add_column(
        "extractions",
        sa.Column("provider", sa.String(), nullable=False, server_default="anthropic"),
    )
    op.alter_column("extractions", "provider", server_default=None)

    # Idempotency key gains the provider: the same model string may mean
    # different things on different providers.
    op.drop_index("ux_extractions_complete", table_name="extractions")
    op.create_index(
        "ux_extractions_complete",
        "extractions",
        ["document_id", "schema_version", "provider", "model"],
        unique=True,
        postgresql_where=sa.text("status = 'complete'"),
    )


def downgrade() -> None:
    op.drop_index("ux_extractions_complete", table_name="extractions")
    op.create_index(
        "ux_extractions_complete",
        "extractions",
        ["document_id", "schema_version", "model"],
        unique=True,
        postgresql_where=sa.text("status = 'complete'"),
    )
    op.drop_column("extractions", "provider")
