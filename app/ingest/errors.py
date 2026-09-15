"""Ingest failure taxonomy.

Maps onto ADR-003's two failure paths: `PermanentIngestError` sends a job
straight to `dead_letter` (e.g. a 404), `TransientIngestError` requeues it
(e.g. a fetch timeout). `EmptyExtractionError` is always permanent — per
PROJECT_BRIEF's M2 instruction, a silent empty extraction is a HARD FAILURE,
not a `partial` record, so it must never be retried into looking fine.

The transient/permanent split is declared by inheriting from
app.errors.TransientJobError / PermanentJobError so the worker can route
ingest and extraction failures with the same two `except` clauses.
"""

from app.errors import JobError, PermanentJobError, TransientJobError


class IngestError(JobError):
    """Base class. Never raised directly."""


class TransientIngestError(IngestError, TransientJobError):
    """Retryable: network timeout, connection reset, 5xx response."""


class PermanentIngestError(IngestError, PermanentJobError):
    """Not retryable: 404/410, unsupported content type, DNS failure."""


class EmptyExtractionError(PermanentIngestError):
    """Parsing "succeeded" (no exception) but produced no usable text.

    PROJECT_BRIEF.md M2: "A silent empty extraction is a HARD FAILURE, not a
    partial record." This is deliberately a subclass of PermanentIngestError
    (not TransientIngestError): retrying an empty extraction against the
    same bytes will produce the same empty result, so retrying wastes the
    job's attempt budget instead of surfacing the problem.
    """
