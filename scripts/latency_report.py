"""M6 part 2: real end-to-end extraction latency, from the existing
30-document run against the live Anthropic API (spike/extraction_run_12.json).

PROJECT_BRIEF M6: "Report both stubbed throughput and real end-to-end
latency from the existing 30-document runs. Say which is which." This is
the "real" half; scripts/load_test.py is the stubbed half.

Per-document latency is NOT a field the run report stores directly (it
records token usage and outcome per document, not a duration), and
`document.fetched_at` is the wrong signal to compute it from: ADR-007
means a document already known by content/text hash is not re-fetched, so
`fetched_at` is often from a much earlier session, not this run (confirmed
by inspection: several of run 12's documents show `fetched_at` days before
`run.started_at`). What IS reliable is `extraction.created_at` for every
document in this run (all 30 have `reused_from_earlier_run: false`, i.e.
freshly extracted in this run, per the report's own bookkeeping) — one
worker process handled these serially, so the gap between one document's
completion and the previous one's is that document's real ingest+extract
wall time, bounded below by nothing but the model's own response time and
this system's overhead.

    RUN     path to the extraction_run_*.json report
            (default spike/extraction_run_12.json)
    OUT     where to write the JSON summary
            (default spike/latency_report.json)

Usage: python -m scripts.latency_report
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def main() -> None:
    run_path = Path(os.environ.get("RUN", "spike/extraction_run_12.json"))
    out_path = Path(os.environ.get("OUT", "spike/latency_report.json"))

    data = json.loads(run_path.read_text())
    reused = [d["id"] for d in data["documents"] if d["reused_from_earlier_run"]]
    if reused:
        raise SystemExit(
            f"{run_path} has {len(reused)} reused document(s) ({reused}); this report "
            "assumes every document was freshly extracted in the run. Pick a run.started_at"
            "–run.finished_at where none are reused, or extend this script to skip them."
        )

    completions = sorted(
        datetime.fromisoformat(d["extraction"]["created_at"]) for d in data["documents"]
    )
    started_at = datetime.fromisoformat(data["started_at"])

    latencies = []
    prev = started_at
    for completed_at in completions:
        latencies.append((completed_at - prev).total_seconds())
        prev = completed_at

    finished_at = datetime.fromisoformat(data["finished_at"])
    wall_seconds = (finished_at - started_at).total_seconds()
    docs_per_hour = len(completions) / wall_seconds * 3600 if wall_seconds > 0 else float("nan")

    report: dict[str, Any] = {
        "rendered_at": datetime.now(UTC).isoformat(),
        "kind": "real_end_to_end",
        "note": (
            "Against the live Anthropic API (see run.summary.provider_model / thinking "
            "in the source report), one worker process, documents processed serially. "
            "Per-document latency is the gap between consecutive extraction completions "
            "(see module docstring for why fetched_at cannot be used instead). Stubbed "
            "throughput under concurrency is reported separately by scripts/load_test.py "
            "and the README's load numbers section."
        ),
        "source_run": data["run"],
        "documents": len(completions),
        "wall_seconds": wall_seconds,
        "documents_per_hour": docs_per_hour,
        "extraction_latency_seconds": {
            "n": len(latencies),
            "min": min(latencies),
            "max": max(latencies),
            "mean": statistics.mean(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "provider_model": data["summary"]["provider_model"],
        "thinking": data["summary"]["thinking"],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    hist = report["extraction_latency_seconds"]
    print(f"real end-to-end latency, {report['documents']} documents from {data['run']}:")
    print(f"  p50={hist['p50']:.2f}s  p95={hist['p95']:.2f}s  mean={hist['mean']:.2f}s")
    print(f"  documents/hour (wall clock, single worker): {docs_per_hour:.1f}")
    print(f"report written to {out_path}")


if __name__ == "__main__":
    main()
