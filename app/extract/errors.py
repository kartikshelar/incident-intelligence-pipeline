"""Extraction failure taxonomy.

Same two-way split as ingest (ADR-003 §4), declared via app.errors so the
worker routes both stages identically:

  TransientExtractionError  -> job requeued   (rate limit, 5xx, overloaded,
                                               network, timeout)
  PermanentExtractionError  -> job dead_letter (auth/config, refusal,
                                               schema validation exhausted,
                                               truncated output)

Every extraction error carries the `attempts` made before it was raised so
the pipeline can persist the full attempt log — PROJECT_BRIEF M3:
"Failures recorded, not swallowed." An error with no attempts (e.g. the
client could not be constructed) records an empty list, which is itself
the record of *where* it failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.errors import JobError, PermanentJobError, TransientJobError

if TYPE_CHECKING:
    from app.extract.extractor import Attempt


class ExtractionError(JobError):
    """Base class. Never raised directly."""

    def __init__(self, message: str, *, attempts: list[Attempt] | None = None) -> None:
        super().__init__(message)
        self.attempts: list[Attempt] = list(attempts or [])


class TransientExtractionError(ExtractionError, TransientJobError):
    """Retryable: 429, 5xx/529, connection error, request timeout."""


class PermanentExtractionError(ExtractionError, PermanentJobError):
    """Not retryable: bad credentials/config, 400 (our request is wrong),
    model refusal, output truncated at max_tokens, or the model could not
    produce schema-valid output within the attempt budget."""


class SchemaValidationExhaustedError(PermanentExtractionError):
    """Every attempt produced output that failed schema validation, even
    with the validation error fed back. Permanent rather than transient:
    the attempt budget *is* the retry policy for this failure mode, and
    letting the queue retry the whole job would just spend more money on
    the same document with no new information."""
