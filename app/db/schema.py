"""Database schema, defined with SQLAlchemy Core.

`sources` (what the user registered) and `jobs` (the queue, per ADR-003)
are M1. `documents` is M2: the result of fetching a source's URL and
normalizing markdown/HTML/PDF to text, with provenance. There is no
`incident` table yet — extraction doesn't exist until M3, and the schema
for it is still being corrected per spike/FINDINGS.md and ADR-001/002.

Core, not ORM: ADR-003 requires the claim query to be raw, verbatim SQL,
never ORM-generated. Using Core (not the ORM layer) for table definitions
keeps the whole data-access surface honest about that from the start.
"""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()

# A registered document to ingest. Registering does not fetch or parse it —
# that happens when a worker claims the job (M2).
sources = Table(
    "sources",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("url", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# Fetched + normalized result of ingesting a source (M2). One row per
# distinct `content_hash` — idempotent on re-ingest (FINDINGS.md §2.9 notes
# content_hash dedups *documents*, not incidents; that distinction is left
# for the incident table in M3+).
#
# `raw_bytes` is stored in Postgres for M2, not MinIO (see ADR discussion in
# the M2 session): a `storage_backend` column records where the bytes live
# so a later swap to object storage doesn't need a data migration, only a
# backend change in app/storage/.
documents = Table(
    "documents",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("source_id", UUID(as_uuid=True), nullable=False),
    Column("source_url", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    # markdown | html | pdf — detected from Content-Type / URL, see
    # app/ingest/detect.py.
    Column("format", String, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("storage_backend", String, nullable=False, server_default="postgres"),
    Column("raw_bytes", LargeBinary, nullable=False),
    # Normalized plain text (FINDINGS.md §2 fixes applied). NOT NULL: a
    # document row is only ever created after successful normalization.
    # Empty-string text is rejected before the row is written — see
    # app/ingest/parse.py EmptyExtractionError — so NULL vs "" is not an
    # ambiguity this table has to carry.
    Column("text", Text, nullable=False),
    # From document metadata (<title>/og:title, PDF metadata, markdown
    # H1), never sourced from body text — FINDINGS.md §2.2. Nullable: some
    # formats/documents genuinely have none (rare, but real).
    Column("title", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("content_hash", name="documents_content_hash_key"),
)

# Job lifecycle per ADR-003 §4:
#   queued -> running -> succeeded
#   running -> queued        (transient failure, retryable)
#   running -> dead_letter   (permanent failure, or retries exhausted)
JOB_STATUSES = ("queued", "running", "succeeded", "dead_letter")

jobs = Table(
    "jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column(
        "source_id",
        UUID(as_uuid=True),
        nullable=False,
        # No ON DELETE behavior specified yet; sources are not deleted in M1.
    ),
    Column("status", String, nullable=False, server_default="queued"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("claimed_at", DateTime(timezone=True), nullable=True),
    Column("succeeded_at", DateTime(timezone=True), nullable=True),
    Column("last_error", Text, nullable=True),
    CheckConstraint(f"status IN {JOB_STATUSES}", name="jobs_status_valid"),
)
