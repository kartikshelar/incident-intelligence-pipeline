"""GET /metrics (PROJECT_BRIEF M6): documents by status, jobs by state,
queue depth, dead-letter count, extraction latency histogram, validation
attempts histogram, review queue depth, cost per document.

Two kinds of number here, deliberately kept apart:

  DB-derived gauges   documents/jobs/review-queue counts, and cost per
                      document. Computed fresh on every scrape from the
                      tables that are already the source of truth (jobs,
                      documents, extractions, field_reviews) — never a
                      second running total that can drift from them.
  In-process histograms  extraction latency and validation-attempt counts,
                      recorded as each extraction finishes (app.extract.
                      pipeline, via `record_extraction`). These describe
                      *this process's* work between scrapes; they reset on
                      restart, same as any Prometheus client-side histogram,
                      and are not a substitute for the attempt_log/usage
                      columns, which remain the durable record.

Exposed as Prometheus text exposition format (the conventional shape for a
path literally named `/metrics`), hand-rolled rather than pulling in
`prometheus_client`: the DB-derived numbers are recomputed per scrape
anyway, so a client library's own registry would just be a second place to
keep them in sync.
"""

from __future__ import annotations

import dataclasses
import threading

from sqlalchemy import Connection, text

from app.settings import Settings
from app.telemetry.cost import Pricing, cost_usd, pricing_from_settings

# Fixed bucket boundaries, Prometheus convention (cumulative, +Inf last).
_LATENCY_BUCKETS_SECONDS: tuple[float, ...] = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600)
_ATTEMPTS_BUCKETS: tuple[float, ...] = (1, 2, 3, 4, 5)


class _Histogram:
    """Minimal cumulative-bucket histogram, Prometheus client semantics
    (le-bucketed counts, a running sum, a running count). Thread-safe: the
    worker's `run_once` and the API's scrape can both touch it.
    """

    def __init__(self, buckets: tuple[float, ...]) -> None:
        self._buckets = buckets
        self._lock = threading.Lock()
        self._bucket_counts = [0] * len(buckets)
        self._count = 0
        self._sum = 0.0

    def observe(self, value: float) -> None:
        with self._lock:
            self._count += 1
            self._sum += value
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    self._bucket_counts[i] += 1

    def render(self, name: str, help_text: str) -> list[str]:
        with self._lock:
            bucket_counts = list(self._bucket_counts)
            count = self._count
            total = self._sum
        lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
        for bound, cumulative in zip(self._buckets, bucket_counts, strict=True):
            lines.append(f'{name}_bucket{{le="{bound}"}} {cumulative}')
        lines.append(f'{name}_bucket{{le="+Inf"}} {count}')
        lines.append(f"{name}_sum {total}")
        lines.append(f"{name}_count {count}")
        return lines


# Process-global: one set of histograms per process (worker and API each
# have their own, like any Prometheus client-side metric).
extraction_latency_seconds = _Histogram(_LATENCY_BUCKETS_SECONDS)
validation_attempts = _Histogram(_ATTEMPTS_BUCKETS)


def record_extraction(*, duration_seconds: float, attempts: int) -> None:
    """Called once per finished extraction (success or failure) by
    app.extract.pipeline.extract_document."""
    extraction_latency_seconds.observe(duration_seconds)
    validation_attempts.observe(attempts)


@dataclasses.dataclass(frozen=True)
class CostSummary:
    priced: bool
    total_usd: float | None
    documents_priced: int
    mean_usd_per_document: float | None


def cost_summary(
    conn: Connection, pricing: Pricing | None, *, run_id: str | None = None
) -> CostSummary:
    """Cost across `extractions` rows, optionally scoped to one run_id
    (PROJECT_BRIEF M6: "queryable per run_id")."""
    if pricing is None:
        return CostSummary(
            priced=False, total_usd=None, documents_priced=0, mean_usd_per_document=None
        )
    query = "SELECT usage FROM extractions WHERE usage IS NOT NULL"
    params: dict[str, str] = {}
    if run_id is not None:
        query += " AND run_id = :run_id"
        params["run_id"] = run_id
    rows = conn.execute(text(query), params).mappings()
    all_costs = (cost_usd(row["usage"], pricing) for row in rows)
    costs: list[float] = [c for c in all_costs if c is not None]
    if not costs:
        return CostSummary(
            priced=True, total_usd=0.0, documents_priced=0, mean_usd_per_document=None
        )
    total = sum(costs)
    return CostSummary(
        priced=True,
        total_usd=total,
        documents_priced=len(costs),
        mean_usd_per_document=total / len(costs),
    )


def _counts(conn: Connection, table: str, column: str) -> dict[str, int]:
    rows = conn.execute(
        text(f"SELECT {column} AS key, count(*) AS n FROM {table} GROUP BY {column}")
    )
    return {row.key: row.n for row in rows}


def render_metrics(conn: Connection, settings: Settings) -> str:
    """The full /metrics body, Prometheus text exposition format."""
    lines: list[str] = []

    documents_by_status = _document_status_counts(conn)
    lines += _gauge_family(
        "documents_total",
        "Documents by extraction status (complete/failed/pending: no extraction row yet).",
        "status",
        documents_by_status,
    )

    jobs_by_state = _counts(conn, "jobs", "status")
    lines += _gauge_family("jobs_total", "Jobs by queue state.", "status", jobs_by_state)

    queue_depth = jobs_by_state.get("queued", 0)
    lines += _gauge("queue_depth", "Jobs currently queued (not yet claimed).", queue_depth)

    dead_letter_count = jobs_by_state.get("dead_letter", 0)
    lines += _gauge(
        "dead_letter_total",
        "Jobs that exhausted retries or failed permanently.",
        dead_letter_count,
    )

    review_queue_depth = int(
        conn.execute(
            text("SELECT count(*) FROM field_reviews WHERE review_state = 'routed'")
        ).scalar_one()
    )
    lines += _gauge(
        "review_queue_depth",
        "Fields routed for human review, awaiting a decision.",
        review_queue_depth,
    )

    lines += extraction_latency_seconds.render(
        "extraction_latency_seconds",
        "Wall-clock duration of extract_document calls in this process.",
    )
    lines += validation_attempts.render(
        "extraction_validation_attempts",
        "Validate-and-retry attempts per finished extraction in this process.",
    )

    pricing = pricing_from_settings(settings)
    summary = cost_summary(conn, pricing)
    lines += _gauge(
        "cost_per_document_usd_mean",
        "Mean cost per document across all priced extractions (null/absent if PRICE_* unset).",
        summary.mean_usd_per_document,
    )
    lines += _gauge(
        "cost_total_usd", "Total cost across all priced extractions.", summary.total_usd
    )
    lines += _gauge(
        "cost_documents_priced_total",
        "Extractions with token usage that could be priced.",
        summary.documents_priced,
    )

    return "\n".join(lines) + "\n"


def _document_status_counts(conn: Connection) -> dict[str, int]:
    """Documents grouped by their most recent extraction status, plus
    `pending` for documents with no extraction row yet (ingested but not
    yet claimed by an extract job, or the job hasn't run)."""
    rows = conn.execute(
        text(
            """
            SELECT COALESCE(latest.status, 'pending') AS status, count(*) AS n
            FROM documents d
            LEFT JOIN LATERAL (
                SELECT status FROM extractions e
                WHERE e.document_id = d.id
                ORDER BY e.created_at DESC
                LIMIT 1
            ) latest ON true
            GROUP BY COALESCE(latest.status, 'pending')
            """
        )
    )
    return {row.status: row.n for row in rows}


def _gauge(name: str, help_text: str, value: float | int | None) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    lines.append(f"{name} {value if value is not None else 'NaN'}")
    return lines


def _gauge_family(name: str, help_text: str, label: str, values: dict[str, int]) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    for key, value in sorted(values.items()):
        lines.append(f'{name}{{{label}="{key}"}} {value}')
    return lines
