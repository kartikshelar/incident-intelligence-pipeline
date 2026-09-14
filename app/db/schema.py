"""Database schema, defined with SQLAlchemy Core.

M1 scope only. This is the skeleton: a `sources` table (what the user
registered) and a `jobs` table (the queue, per ADR-003). There is no
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
    MetaData,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()

# A registered document to ingest. M1 does not fetch or parse it — that's M2.
sources = Table(
    "sources",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("url", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
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
