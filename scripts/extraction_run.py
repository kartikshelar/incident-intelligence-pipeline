"""Register every document in a corpus manifest, wait for the ingest and
extract jobs to finish, and write a per-document report.

Drives the running stack the way a user would — POST /sources — and then
reads outcomes from the database. It never calls a model itself and never
touches the prompt; it only reports what the worker did.

    APP_DATABASE_URL   Postgres (as for the app)
    API_URL            default http://localhost:8000
    MANIFEST           default spike/corpus_manifest.json
    OUT                default spike/extraction_run_01.json
    TIMEOUT_SECONDS    give up waiting after this (default 3600)
    PRICE_*_USD_PER_MTOK  input / output / cache_read / cache_write prices
                       for the configured model; cost is null if unset.
    REPORT_ONLY        path of an earlier report: re-render it from the
                       database (same sources, same start time) without
                       registering anything or waiting.

Tokens and cost are attributed to THIS run only: extraction rows created
before the run started (an earlier run's rows for the same document) are
listed under `earlier_extraction_rows` but not summed.

A document whose extract job succeeded without writing a new row was a
duplicate (app/extract/pipeline.py: a complete row for the same schema
version, provider, model and thinking already existed). Its record is
reported from that earlier row, marked `reused_from_earlier_run`, with no
tokens or cost attributed to this run and no validation attempts counted
in the mean, so a run over a corpus that grew since the last run still
reports every document.

Usage: python -m scripts.extraction_run
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from app.db.engine import get_engine

TERMINAL = {"succeeded", "dead_letter"}
_PRICE_KEYS = {
    "input_tokens": "PRICE_INPUT_USD_PER_MTOK",
    "output_tokens": "PRICE_OUTPUT_USD_PER_MTOK",
    "cache_read_input_tokens": "PRICE_CACHE_READ_USD_PER_MTOK",
    "cache_creation_input_tokens": "PRICE_CACHE_WRITE_USD_PER_MTOK",
}


def _pricing() -> dict[str, float] | None:
    values = {usage_key: os.environ.get(env) for usage_key, env in _PRICE_KEYS.items()}
    if any(v is None for v in values.values()):
        return None
    return {k: float(v) for k, v in values.items() if v is not None}


def _cost(usage: dict[str, int], pricing: dict[str, float] | None) -> float | None:
    if pricing is None:
        return None
    return sum(usage.get(k, 0) * price / 1_000_000 for k, price in pricing.items())


def register(api_url: str, url: str) -> tuple[uuid.UUID, uuid.UUID]:
    response = httpx.post(f"{api_url}/sources", json={"url": url}, timeout=30)
    response.raise_for_status()
    body = response.json()
    return uuid.UUID(body["source_id"]), uuid.UUID(body["job_id"])


def wait_for_jobs(source_ids: list[uuid.UUID], timeout_seconds: float) -> bool:
    """True when every job for these sources is terminal; False on timeout."""
    engine = get_engine()
    deadline = time.monotonic() + timeout_seconds
    last_line = ""
    while True:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT kind, status, count(*) AS n FROM jobs WHERE source_id = ANY(:ids) "
                     "GROUP BY kind, status ORDER BY kind, status"),
                {"ids": source_ids},
            ).all()
        counts = {f"{kind}/{status}": n for kind, status, n in rows}
        line = "  ".join(f"{k}={v}" for k, v in counts.items())
        if line != last_line:
            print(f"[{datetime.now(UTC):%H:%M:%S}] {line}", flush=True)
            last_line = line
        pending = sum(n for (kind, status, n) in rows if status not in TERMINAL)
        # Every ingest that succeeded enqueues an extract job in the same
        # transaction, so "no non-terminal jobs" means the run is over.
        if pending == 0 and rows:
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(5)


def _jobs(conn: Any, source_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = conn.execute(
        text("SELECT id, kind, document_id, status, attempts, last_error, created_at, "
             "claimed_at, succeeded_at FROM jobs WHERE source_id = :s ORDER BY created_at"),
        {"s": source_id},
    ).mappings()
    return [
        {
            "job_id": str(r["id"]),
            "kind": r["kind"],
            "status": r["status"],
            "attempts": r["attempts"],
            "last_error": r["last_error"],
            "document_id": str(r["document_id"]) if r["document_id"] else None,
        }
        for r in rows
    ]


def _document(conn: Any, document_id: str) -> dict[str, Any] | None:
    r = conn.execute(
        text("SELECT id, source_url, format, title, length(text) AS text_chars, content_hash, "
             "text_hash, fetched_at, created_at FROM documents WHERE id = :id"),
        {"id": uuid.UUID(document_id)},
    ).mappings().fetchone()
    if r is None:
        return None
    return {
        "document_id": str(r["id"]),
        "final_url": r["source_url"],
        "format": r["format"],
        "title": r["title"],
        "text_chars": r["text_chars"],
        # ADR-007: content_hash names the stored bytes (may change across
        # fetches); text_hash is the document's identity.
        "content_hash": r["content_hash"],
        "text_hash": r["text_hash"],
        "fetched_at": r["fetched_at"].isoformat(),
        "first_seen_at": r["created_at"].isoformat(),
    }


def _extractions(conn: Any, document_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text("SELECT * FROM extractions WHERE document_id = :d ORDER BY created_at"),
        {"d": uuid.UUID(document_id)},
    ).mappings()
    out = []
    for r in rows:
        out.append(
            {
                "extraction_id": str(r["id"]),
                "status": r["status"],
                "provider": r["provider"],
                "model": r["model"],
                "thinking": r["thinking"],
                "run_id": r["run_id"],
                "schema_version": r["schema_version"],
                "validation_attempts": r["attempts"],
                "usage": r["usage"],
                "error": r["error"],
                "error_kind": r["error_kind"],
                "record": r["record"],
                "per_field_confidence": r["per_field_confidence"],
                "derived": r["derived"],
                "attempt_log": [
                    {k: v for k, v in a.items() if k != "raw_output"} for a in r["attempt_log"]
                ],
                "created_at": r["created_at"].isoformat(),
            }
        )
    return out


def _sum_usage(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        for k, v in (row["usage"] or {}).items():
            totals[k] = totals.get(k, 0) + int(v)
    return totals


def main() -> int:
    api_url = os.environ.get("API_URL", "http://localhost:8000")
    manifest_path = Path(os.environ.get("MANIFEST", "spike/corpus_manifest.json"))
    out_path = Path(os.environ.get("OUT", "spike/extraction_run_01.json"))
    timeout_seconds = float(os.environ.get("TIMEOUT_SECONDS", "3600"))
    report_only = os.environ.get("REPORT_ONLY")
    pricing = _pricing()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs = manifest["documents"]

    registered: dict[str, uuid.UUID] = {}
    if report_only:
        earlier = json.loads(Path(report_only).read_text(encoding="utf-8"))
        started = datetime.fromisoformat(earlier["started_at"])
        registered = {d["id"]: uuid.UUID(d["source_id"]) for d in earlier["documents"]}
        finished = True
        print(f"re-rendering {report_only} (started {started.isoformat()})", flush=True)
    else:
        started = datetime.now(UTC)
        for doc in docs:
            source_id, job_id = register(api_url, doc["url"])
            registered[doc["id"]] = source_id
            print(
                f"registered {doc['id']} {doc['org']}: source={source_id} job={job_id}", flush=True
            )
        finished = wait_for_jobs(list(registered.values()), timeout_seconds)
        if not finished:
            print(f"TIMEOUT after {timeout_seconds}s; reporting whatever is terminal", flush=True)

    # Rows older than this belong to an earlier run of the same document.
    cutoff = (started - timedelta(minutes=1)).isoformat()

    engine = get_engine()
    per_doc: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for doc in docs:
            source_id = registered[doc["id"]]
            jobs = _jobs(conn, source_id)
            ingest = next((j for j in jobs if j["kind"] == "ingest"), None)
            extract = next((j for j in jobs if j["kind"] == "extract"), None)
            document = _document(conn, extract["document_id"]) if extract else None
            all_rows = _extractions(conn, extract["document_id"]) if extract else []
            earlier_rows = [r for r in all_rows if r["created_at"] < cutoff]
            rows = [r for r in all_rows if r["created_at"] >= cutoff]
            final = rows[-1] if rows else None
            reused = False
            if final is None and extract is not None and extract["status"] == "succeeded":
                earlier_complete = [r for r in earlier_rows if r["status"] == "complete"]
                if earlier_complete:
                    final = earlier_complete[-1]
                    reused = True
            usage = _sum_usage(rows)
            error = None
            if final is not None and final["status"] != "complete":
                error = final["error"]
            elif extract is not None and extract["status"] == "dead_letter":
                error = extract["last_error"]
            elif ingest is not None and ingest["status"] != "succeeded":
                error = f"ingest {ingest['status']}: {ingest['last_error']}"
            per_doc.append(
                {
                    "id": doc["id"],
                    "org": doc["org"],
                    "url": doc["url"],
                    "manifest_format": doc["format"],
                    "source_id": str(source_id),
                    "jobs": jobs,
                    "document": document,
                    "outcome": (
                        "complete"
                        if final is not None and final["status"] == "complete"
                        else "dead_letter"
                        if (extract or ingest or {}).get("status") == "dead_letter"
                        else "not_terminal"
                    ),
                    "validation_attempts": (
                        final["validation_attempts"] if final and not reused else None
                    ),
                    "reused_from_earlier_run": reused,
                    "extraction_rows": len(rows),
                    "tokens": {
                        "input_tokens": usage.get("input_tokens", 0),
                        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                        "total_input_tokens": usage.get("input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    },
                    "cost_usd": _cost(usage, pricing),
                    "error": error,
                    "extraction": final,
                    # This run's non-final rows (e.g. a transient failure
                    # then success) count toward tokens; earlier runs' rows
                    # for the same document are listed but not counted.
                    "this_run_extraction_rows": rows[:-1],
                    "earlier_extraction_rows": earlier_rows,
                }
            )

    succeeded = [d for d in per_doc if d["outcome"] == "complete"]
    reused_docs = [d for d in succeeded if d["reused_from_earlier_run"]]
    dead = [d for d in per_doc if d["outcome"] == "dead_letter"]
    attempts = [d["validation_attempts"] for d in per_doc if d["validation_attempts"] is not None]
    costs = [d["cost_usd"] for d in per_doc if d["cost_usd"] is not None]
    total_cost = sum(costs) if pricing is not None else None
    providers_models = sorted({(d["extraction"]["provider"], d["extraction"]["model"])
                               for d in per_doc if d["extraction"]})
    thinking_settings = sorted({d["extraction"]["thinking"] for d in per_doc if d["extraction"]})
    schema_versions = sorted({d["extraction"]["schema_version"]
                              for d in per_doc if d["extraction"]})

    summary = {
        "documents": len(per_doc),
        "succeeded": len(succeeded),
        # Of the succeeded: reported from an earlier run's complete row at
        # the same (schema, provider, model, thinking); nothing billed here.
        "reused_from_earlier_runs": len(reused_docs),
        "extracted_this_run": len(succeeded) - len(reused_docs),
        "dead_lettered": len(dead),
        "not_terminal": len(per_doc) - len(succeeded) - len(dead),
        "mean_validation_attempts": statistics.mean(attempts) if attempts else None,
        "validation_attempts_histogram": {
            str(n): attempts.count(n) for n in sorted(set(attempts))
        },
        "total_tokens": _sum_usage([d["extraction"] for d in per_doc if d["extraction"]]
                                   + [r for d in per_doc for r in d["this_run_extraction_rows"]]),
        "total_cost_usd": total_cost,
        "provider_model": [f"{p}/{m}" for p, m in providers_models],
        "thinking": thinking_settings,
        "schema_version": schema_versions,
    }
    report = {
        "run": out_path.stem,
        "started_at": started.isoformat(),
        "finished_at": (
            earlier["finished_at"] if report_only else datetime.now(UTC).isoformat()
        ),
        "rendered_at": datetime.now(UTC).isoformat(),
        "manifest": str(manifest_path),
        "pricing_usd_per_mtok": pricing,
        "summary": summary,
        "documents": per_doc,
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print()
    print(f"wrote {out_path}")
    print(f"documents succeeded:     {summary['succeeded']}/{summary['documents']}"
          + (f" ({summary['reused_from_earlier_runs']} reused from earlier runs)"
             if summary["reused_from_earlier_runs"] else ""))
    print(f"documents dead-lettered: {summary['dead_lettered']}/{summary['documents']}")
    if summary["not_terminal"]:
        print(f"documents not terminal:  {summary['not_terminal']}")
    mean = summary["mean_validation_attempts"]
    print(f"mean validation attempts: {mean:.2f}" if mean is not None else
          "mean validation attempts: n/a")
    print(f"total tokens: {summary['total_tokens']}")
    print(f"total cost: ${total_cost:.4f}" if total_cost is not None else
          "total cost: n/a (no PRICE_* env)")
    for d in per_doc:
        print(f"  {d['id']:<3}{d['org'][:40]:<40} "
              f"{(d['outcome'] + ('*' if d['reused_from_earlier_run'] else '')):<12} "
              f"attempts={d['validation_attempts']} "
              f"cost={d['cost_usd'] if d['cost_usd'] is None else round(d['cost_usd'], 4)}"
              + (f"  error={d['error'][:100]}" if d["error"] else ""))
    return 0 if finished else 1


if __name__ == "__main__":
    sys.exit(main())
