"""extractions.run_id: distinguish repeat runs at identical settings

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-16

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every row that exists before this migration was produced by a single
    # extraction_run.py invocation each, before "run" was a tracked concept.
    # Backfilling one id per existing row (rather than one shared id) is
    # deliberately conservative: it asserts nothing about which of those
    # rows belonged to the same run, and does not collapse any of them into
    # an artificial agreement pair for ADR-006 §8's two-run check. The
    # server default is then dropped: new rows must say explicitly what
    # run they belong to (app/extract/llm.py: one UUID per LLMClient unless
    # APP_EXTRACTION_RUN_ID names one).
    op.add_column("extractions", sa.Column("run_id", sa.String(), nullable=True))
    conn = op.get_bind()
    for row in conn.execute(sa.text("SELECT id FROM extractions")):
        conn.execute(
            sa.text("UPDATE extractions SET run_id = :run_id WHERE id = :id"),
            {"run_id": str(uuid.uuid4()), "id": row.id},
        )
    op.alter_column("extractions", "run_id", nullable=False)

    # Idempotency key gains run_id: the same document, schema, provider,
    # model and thinking setting extracted again under a different run is a
    # new complete row, not a duplicate (ADR-006 §8 needs two independent
    # runs at identical settings to each leave a measurable row).
    op.drop_index("ux_extractions_complete", table_name="extractions")
    op.create_index(
        "ux_extractions_complete",
        "extractions",
        ["document_id", "schema_version", "provider", "model", "thinking", "run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'complete'"),
    )


def downgrade() -> None:
    # Fails if two complete rows for one (document, schema, provider, model,
    # thinking) exist under different run ids — the index cannot be
    # recreated without choosing which run's record to keep, and that is
    # not a migration's call.
    op.drop_index("ux_extractions_complete", table_name="extractions")
    op.create_index(
        "ux_extractions_complete",
        "extractions",
        ["document_id", "schema_version", "provider", "model", "thinking"],
        unique=True,
        postgresql_where=sa.text("status = 'complete'"),
    )
    op.drop_column("extractions", "run_id")
