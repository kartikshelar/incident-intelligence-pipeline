"""Job-level failure classes shared by every pipeline stage.

ADR-003 §4 defines exactly two failure paths for a job: transient
(`running -> queued`, retried with backoff until `job_max_attempts`) and
permanent (`running -> dead_letter`). Every stage — ingest (M2), extract
(M3) — expresses its failures as a subclass of one of these two so the
worker can map *any* stage's exception onto the queue's state machine
without knowing which stage raised it.

Stage-specific hierarchies (app.ingest.errors, app.extract.errors) keep
their own names and docstrings; they inherit from these to declare which
queue transition they want.
"""


class JobError(Exception):
    """Base class. Never raised directly."""


class TransientJobError(JobError):
    """Retryable: the same job may succeed if attempted again later."""


class PermanentJobError(JobError):
    """Not retryable: retrying the same input would fail the same way."""
