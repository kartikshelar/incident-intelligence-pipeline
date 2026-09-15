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

Usage: python -m scripts.extraction_run
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime
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
        text("SELECT id, source_url, format, title, length(text) AS text_chars, content_hash "
             "FROM documents WHERE id = :id"),
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
        "content_hash": r["content_hash"],
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
    pricing = _pricing()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs = manifest["documents"]
    started = datetime.now(UTC)

    registered: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
    for doc in docs:
        source_id, job_id = register(api_url, doc["url"])
        registered[doc["id"]] = (source_id, job_id)
        print(f"registered {doc['id']} {doc['org']}: source={source_id} job={job_id}", flush=True)

    finished = wait_for_jobs([s for s, _ in registered.values()], timeout_seconds)
    if not finished:
        print(f"TIMEOUT after {timeout_seconds}s; reporting whatever is terminal", flush=True)

    engine = get_engine()
    per_doc: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for doc in docs:
            source_id, _ = registered[doc["id"]]
            jobs = _jobs(conn, source_id)
            ingest = next((j for j in jobs if j["kind"] == "ingest"), None)
            extract = next((j for j in jobs if j["kind"] == "extract"), None)
            document = _document(conn, extract["document_id"]) if extract else None
            rows = _extractions(conn, extract["document_id"]) if extract else []
            final = rows[-1] if rows else None
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
                    "validation_attempts": final["validation_attempts"] if final else None,
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
                    "earlier_extraction_rows": rows[:-1],
                }
            )

    succeeded = [d for d in per_doc if d["outcome"] == "complete"]
    dead = [d for d in per_doc if d["outcome"] == "dead_letter"]
    attempts = [d["validation_attempts"] for d in per_doc if d["validation_attempts"] is not None]
    costs = [d["cost_usd"] for d in per_doc if d["cost_usd"] is not None]
    total_cost = sum(costs) if pricing is not None else None
    providers_models = sorted({(d["extraction"]["provider"], d["extraction"]["model"])
                               for d in per_doc if d["extraction"]})

    summary = {
        "documents": len(per_doc),
        "succeeded": len(succeeded),
        "dead_lettered": len(dead),
        "not_terminal": len(per_doc) - len(succeeded) - len(dead),
        "mean_validation_attempts": statistics.mean(attempts) if attempts else None,
        "validation_attempts_histogram": {
            str(n): attempts.count(n) for n in sorted(set(attempts))
        },
        "total_tokens": _sum_usage([d["extraction"] for d in per_doc if d["extraction"]]
                                   + [r for d in per_doc for r in d["earlier_extraction_rows"]]),
        "total_cost_usd": total_cost,
        "provider_model": [f"{p}/{m}" for p, m in providers_models],
    }
    report = {
        "run": out_path.stem,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "manifest": str(manifest_path),
        "pricing_usd_per_mtok": pricing,
        "summary": summary,
        "documents": per_doc,
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print()
    print(f"wrote {out_path}")
    print(f"documents succeeded:     {summary['succeeded']}/{summary['documents']}")
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
        print(f"  {d['id']} {d['org']:<40} {d['outcome']:<12} attempts={d['validation_attempts']} "
              f"cost={d['cost_usd'] if d['cost_usd'] is None else round(d['cost_usd'], 4)}"
              + (f"  error={d['error'][:100]}" if d["error"] else ""))
    return 0 if finished else 1


if __name__ == "__main__":
    sys.exit(main())
