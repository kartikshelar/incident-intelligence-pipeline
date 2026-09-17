"""Database schema, defined with SQLAlchemy Core.

`sources` (what the user registered) and `jobs` (the queue, per ADR-003)
are M1. `documents` is M2: the result of fetching a source's URL and
normalizing markdown/HTML/PDF to text, with provenance. `extractions` is
M3: one row per extraction attempt-set against a document — complete or
failed — holding the validated incident record (app/extract/schema.py) as
JSONB plus the full attempt log. `field_reviews` is M4: one row per
(complete extraction, top-level record field) carrying that field's
review state, its self-reported confidence, and — once a human has looked
at it — the decision and any corrected value, next to the model's value.

It is called `extractions`, not `incidents`, on purpose: FINDINGS.md §4.11
and §4.12 (document != incident; several impact periods per document) are
OPEN. A row here is "what extraction v0 got from this document", keyed on
document_id. An `incidents` table with its own identity is the schema
decision those two findings are waiting on.

Core, not ORM: ADR-003 requires the claim query to be raw, verbatim SQL,
never ORM-generated. Using Core (not the ORM layer) for table definitions
keeps the whole data-access surface honest about that from the start.
"""

import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

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

# Fetched + normalized result of ingesting a source (M2). Two hashes, two
# jobs (ADR-007):
#   content_hash  sha256 of `raw_bytes` — the storage key, unique.
#   text_hash     sha256 of `text` — the document's identity and the ingest
#                 idempotency key, unique. A re-fetch whose bytes changed
#                 but whose text did not (nonces, CSP headers) updates this
#                 row's provenance columns (source_url, fetched_at,
#                 content_hash, raw_bytes, storage_backend) in place.
# So (content_hash, raw_bytes, source_url, fetched_at, storage_backend)
# describe the bytes currently stored; (text, text_hash, title, created_at)
# describe the document, and never change after insert. FINDINGS.md §2.9:
# this dedups *documents*, not incidents (still open, §4.11/§4.12).
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
    Column("text_hash", Text, nullable=False),
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
    UniqueConstraint("text_hash", name="documents_text_hash_key"),
)

# Job lifecycle per ADR-003 §4:
#   queued -> running -> succeeded
#   running -> queued        (transient failure, retryable)
#   running -> dead_letter   (permanent failure, or retries exhausted)
JOB_STATUSES = ("queued", "running", "succeeded", "dead_letter")

# Pipeline stages, one job each (M3). An `ingest` job's success enqueues an
# `extract` job for the resulting document in the same transaction
# (ADR-003 §2), so a document can never exist without an extraction job
# having been queued for it, and an extraction failure is retried/dead-
# lettered on its own without re-fetching the source.
JOB_KINDS = ("ingest", "extract")

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
    Column("kind", String, nullable=False, server_default="ingest"),
    # Set for `extract` jobs: the document to extract from. NULL for ingest.
    Column("document_id", UUID(as_uuid=True), nullable=True),
    Column("status", String, nullable=False, server_default="queued"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("claimed_at", DateTime(timezone=True), nullable=True),
    Column("succeeded_at", DateTime(timezone=True), nullable=True),
    Column("last_error", Text, nullable=True),
    CheckConstraint(f"status IN {JOB_STATUSES}", name="jobs_status_valid"),
    CheckConstraint(f"kind IN {JOB_KINDS}", name="jobs_kind_valid"),
    CheckConstraint(
        "(kind = 'extract') = (document_id IS NOT NULL)", name="jobs_extract_has_document"
    ),
)

# Outcome of running extraction v0 against a document (M3).
#   complete: `record` validated against app/extract/schema.py, persisted.
#   failed:   nothing usable; `error` / `error_kind` say why and
#             `attempt_log` holds every raw model output and validation
#             error, so a failure is a record, not a log line.
# `partial` from the draft's extraction_status enum is deliberately absent:
# whether to persist partial records is DERIVE-03 and has no ADR yet.
EXTRACTION_STATUSES = ("complete", "failed")

extractions = Table(
    "extractions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("schema_version", String, nullable=False),
    # Which app.extract.llm provider produced the row (APP_LLM_PROVIDER),
    # the configured model string (APP_EXTRACTION_MODEL), and the configured
    # thinking/effort setting (APP_EXTRACTION_THINKING), all verbatim. The
    # thinking string is provider-interpreted (app/extract/llm.py); it is
    # stored because on current models it decides most of the output bill.
    Column("provider", String, nullable=False),
    Column("model", String, nullable=False),
    Column("thinking", String, nullable=False),
    # Identifies which run produced this row (app/extract/llm.py: one UUID
    # per LLMClient unless APP_EXTRACTION_RUN_ID names one explicitly). Part
    # of the idempotency key so a second complete run at identical settings
    # (ADR-006 §8's two-run agreement check) gets its own row instead of
    # being treated as a duplicate of the first.
    Column("run_id", String, nullable=False),
    Column("status", String, nullable=False),
    # IncidentRecord as JSON. NULL iff status = 'failed'.
    Column("record", JSONB, nullable=True),
    # FieldConfidence as JSON: {field: 0..1}. Captured, NOT thresholded (M4).
    Column("per_field_confidence", JSONB, nullable=True),
    # Where the confidence numbers came from — DERIVE-05 will compare
    # sources in M5; 'self_report' is the only one that exists today.
    Column("confidence_source", String, nullable=False, server_default="self_report"),
    # app/extract/derive.py output (durations from anchors). NULL iff failed.
    Column("derived", JSONB, nullable=True),
    Column("attempts", Integer, nullable=False),
    Column("attempt_log", JSONB, nullable=False),
    # Summed token usage across attempts — the raw material for M6's
    # cost-per-document metric.
    Column("usage", JSONB, nullable=False),
    Column("error", Text, nullable=True),
    # transient | permanent | unexpected (a non-extraction exception that
    # escaped the loop — recorded here so it is never only a log line).
    Column("error_kind", String, nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint(f"status IN {EXTRACTION_STATUSES}", name="extractions_status_valid"),
    CheckConstraint(
        "(status = 'complete') = (record IS NOT NULL)", name="extractions_complete_has_record"
    ),
    # At-least-once delivery (ADR-003 §4) means two workers can run the same
    # extract job; the pipeline checks first, and this makes the check
    # race-proof: one complete row per (document, schema, provider, model,
    # thinking, run). Adding run_id (over 0006's four-column key) is what
    # lets a second run at identical settings produce its own complete row
    # instead of colliding with the first — the duplicate check in
    # app/extract/pipeline.py additionally scopes on run_id so at-least-once
    # redelivery *within* one run still dedups as before.
    Index(
        "ux_extractions_complete",
        "document_id",
        "schema_version",
        "provider",
        "model",
        "thinking",
        "run_id",
        unique=True,
        postgresql_where=text("status = 'complete'"),
    ),
    Index("ix_extractions_document_id", "document_id"),
)

# Per-field review state (M4, ADR-010 §1: the unit of review is the field).
#   unreviewed  nobody has been asked to look at it. Every field of every
#               complete extraction starts here (app/extract/pipeline.py
#               writes one row per top-level record field in the same
#               transaction as the extraction; migration 0008 backfilled
#               the rows for extractions that predate the table).
#   routed      selected for human review by app/review/routing.py
#               (confidence below the configured floor, ranked ascending,
#               budget applied after ranking). Stays routed until reviewed;
#               a skip does not change state, it only moves the field to
#               the back of the presentation order.
#   reviewed    a human decided: `accepted` (the model's value stands) or
#               `corrected` (`corrected_value` is the human's answer). A
#               reviewed field is never re-routed (ADR-010 §6), and its
#               `model_value` is never overwritten — both answers are kept.
REVIEW_STATES = ("unreviewed", "routed", "reviewed")
REVIEW_DECISIONS = ("accepted", "corrected")

field_reviews = Table(
    "field_reviews",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("extraction_id", UUID(as_uuid=True), nullable=False),
    # A top-level IncidentRecord field name (app.extract.schema.RECORD_FIELDS).
    Column("field", String, nullable=False),
    # Copied from extractions.per_field_confidence[field] when the row is
    # created, so the routing query can ORDER BY it without unpacking JSONB.
    # The extraction row remains the source of truth for where the number
    # came from (extractions.confidence_source, ADR-009).
    Column("confidence", Float, nullable=False),
    # The model's value for this field, as stored in extractions.record —
    # the JSON value itself, so a null field is JSON `null`, never SQL NULL.
    # Duplicated here deliberately: a corrections query is one table, and
    # `record` is immutable so the copy cannot drift.
    Column("model_value", JSONB, nullable=False),
    Column("review_state", String, nullable=False, server_default="unreviewed"),
    Column("routed_at", DateTime(timezone=True), nullable=True),
    # Skip bookkeeping: a skipped field goes to the back of the
    # presentation order (app/review/queries.py) but keeps its rank.
    Column("skip_count", Integer, nullable=False, server_default="0"),
    Column("last_skipped_at", DateTime(timezone=True), nullable=True),
    # Set together when review_state becomes 'reviewed'.
    Column("decision", String, nullable=True),
    # The human's value when decision = 'corrected'. JSON `null` is a valid
    # correction (the field should have been null); SQL NULL means "no
    # correction", which the check below ties to the decision.
    Column("corrected_value", JSONB, nullable=True),
    Column("reviewer", Text, nullable=True),
    Column("reviewer_note", Text, nullable=True),
    Column("reviewed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("extraction_id", "field", name="field_reviews_extraction_field_key"),
    CheckConstraint(f"review_state IN {REVIEW_STATES}", name="field_reviews_state_valid"),
    CheckConstraint(
        f"decision IS NULL OR decision IN {REVIEW_DECISIONS}", name="field_reviews_decision_valid"
    ),
    # Reviewed <=> a decision, a reviewer and a time are all recorded.
    CheckConstraint(
        "(review_state = 'reviewed') = "
        "(decision IS NOT NULL AND reviewer IS NOT NULL AND reviewed_at IS NOT NULL)",
        name="field_reviews_reviewed_is_complete",
    ),
    CheckConstraint(
        "(review_state = 'reviewed') OR "
        "(decision IS NULL AND reviewer IS NULL AND reviewed_at IS NULL)",
        name="field_reviews_unreviewed_has_no_decision",
    ),
    # A corrected value exists exactly when the decision was 'corrected'.
    CheckConstraint(
        "(decision IS NOT DISTINCT FROM 'corrected') = (corrected_value IS NOT NULL)",
        name="field_reviews_corrected_has_value",
    ),
    # Routed fields know when they were routed (reviewed ones may too).
    CheckConstraint(
        "review_state <> 'routed' OR routed_at IS NOT NULL",
        name="field_reviews_routed_has_time",
    ),
    Index("ix_field_reviews_extraction_id", "extraction_id"),
    # The routing scan (state + confidence) and the queue listing.
    Index("ix_field_reviews_state_confidence", "review_state", "confidence"),
)
