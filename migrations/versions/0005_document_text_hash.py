"""documents.text_hash: document identity over extracted text (ADR-007)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15

Adds `text_hash` (sha256 of `documents.text`), backfills it in SQL, merges
any documents that already share a text (the AWS and Google Cloud pages
were re-ingested on 2026-09-15 with new nonces in the HTML and identical
text — one document each, stored twice), then makes the column NOT NULL
and unique.

Merge rule, matching app.ingest.pipeline's runtime behaviour: the OLDEST
row (first `created_at`) is the document; every later duplicate's jobs
and extractions are re-pointed at it; the NEWEST duplicate's provenance
(source_url, fetched_at, content_hash, raw_bytes, storage_backend) is
copied onto it, since that is the fetch the runtime would have kept; the
duplicate rows are deleted. Nothing about the extractions themselves is
changed. If re-pointing would put two `complete` extractions for the same
(schema_version, provider, model) on one document — which the partial
unique index forbids — the migration stops before writing anything and
names the documents, rather than deleting one of the two records.

The downgrade drops the column; merged rows are not un-merged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("text_hash", sa.Text(), nullable=True))
    # Must agree byte-for-byte with app.ingest.pipeline.text_hash_of
    # (sha256 over the UTF-8 encoding of the text).
    op.execute(
        "UPDATE documents SET text_hash = encode(sha256(convert_to(text, 'UTF8')), 'hex')"
    )
    _merge_documents_sharing_a_text(op.get_bind())
    op.alter_column("documents", "text_hash", nullable=False)
    op.create_unique_constraint("documents_text_hash_key", "documents", ["text_hash"])
    op.create_index("ix_documents_text_hash", "documents", ["text_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_documents_text_hash", table_name="documents")
    op.drop_constraint("documents_text_hash_key", "documents", type_="unique")
    op.drop_column("documents", "text_hash")


def _merge_documents_sharing_a_text(conn: sa.Connection) -> None:
    groups = conn.execute(
        sa.text(
            "SELECT text_hash, array_agg(id ORDER BY created_at) AS ids "
            "FROM documents GROUP BY text_hash HAVING count(*) > 1"
        )
    ).all()

    # Check every group before changing anything: Postgres DDL is
    # transactional, so a raise here rolls the whole migration back.
    for _text_hash, ids in groups:
        canonical, *duplicates = ids
        for duplicate in duplicates:
            collisions = conn.execute(
                sa.text(
                    """
                    SELECT d.schema_version, d.provider, d.model
                    FROM extractions d
                    WHERE d.document_id = :duplicate AND d.status = 'complete'
                      AND EXISTS (
                          SELECT 1 FROM extractions c
                          WHERE c.document_id = :canonical AND c.status = 'complete'
                            AND c.schema_version = d.schema_version
                            AND c.provider = d.provider AND c.model = d.model
                      )
                    """
                ),
                {"duplicate": duplicate, "canonical": canonical},
            ).all()
            if collisions:
                raise RuntimeError(
                    f"cannot merge document {duplicate} into {canonical}: both have a "
                    f"complete extraction for {[tuple(c) for c in collisions]}; resolve "
                    "by hand (ux_extractions_complete allows one per document)"
                )

    for _text_hash, ids in groups:
        canonical, *duplicates = ids
        newest = conn.execute(
            sa.text(
                "SELECT source_url, fetched_at, content_hash, raw_bytes, storage_backend "
                "FROM documents WHERE id = :id"
            ),
            {"id": duplicates[-1]},
        ).mappings().one()
        for duplicate in duplicates:
            conn.execute(
                sa.text("UPDATE jobs SET document_id = :canonical WHERE document_id = :duplicate"),
                {"canonical": canonical, "duplicate": duplicate},
            )
            conn.execute(
                sa.text(
                    "UPDATE extractions SET document_id = :canonical WHERE document_id = :duplicate"
                ),
                {"canonical": canonical, "duplicate": duplicate},
            )
            # Delete before updating the canonical row: content_hash is
            # unique, and the canonical row is about to take this one's.
            conn.execute(sa.text("DELETE FROM documents WHERE id = :id"), {"id": duplicate})
        conn.execute(
            sa.text(
                """
                UPDATE documents
                SET source_url = :source_url, fetched_at = :fetched_at,
                    content_hash = :content_hash, raw_bytes = :raw_bytes,
                    storage_backend = :storage_backend
                WHERE id = :id
                """
            ),
            {"id": canonical, **dict(newest)},
        )
