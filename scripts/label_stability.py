"""How stable are the open trigger / mechanism labels across runs of the
same documents? Evidence for DERIVE-01b; it proposes nothing.

Reads the per-document reports `scripts/extraction_run.py` writes; calls
no model and touches no database.

    RUNS   comma-separated report paths (default: runs 02, 04, 05, 06 —
           the runs at the API's default thinking plus the two thinking
           arms; run 03 is left out because schema v0.2 was reverted)
    OUT    default spike/label_stability.json

For `trigger.label` and `mechanism.label`, per document:
  - every label string it received, per run, and how many DISTINCT
    strings that is (null is not a string; nulls are counted separately)
  - every unordered pair of distinct strings the same document received
    ("co-occurring pairs"), each marked as one of:
      same_concept   the two strings name one concept in different words —
                     one is the other plus or minus a qualifier, or a
                     synonymous rewording (route_deletion /
                     network_route_deletion)
      borderline     the same referent at a different granularity or
                     framing (an effect vs the action that caused it, a
                     specific vs a general failure); a class list could go
                     either way on these, so they are listed but NOT
                     counted as same_concept
      different      two concepts

The same_concept / borderline judgements are hand-written below
(`SAME_CONCEPT`, `BORDERLINE`), by Claude Code on 2026-09-16 from the
label strings alone. They are equivalences between strings that already
occurred, not a taxonomy; ADR-001 defers the class list to 01b and this
script does not answer it. A pair not listed in either table is reported
as `different`.

Usage: python -m scripts.label_stability
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

FIELDS = ("trigger.label", "mechanism.label")
DEFAULT_RUNS = (
    "spike/extraction_run_02.json",
    "spike/extraction_run_04.json",
    "spike/extraction_run_05.json",
    "spike/extraction_run_06.json",
)

# Unordered pairs of label strings judged to be one concept in different
# wording. Only pairs that co-occur on a document are ever consulted, but
# the judgement is about the strings, not the document.
SAME_CONCEPT: dict[frozenset[str], str] = {
    frozenset({"route_deletion", "network_route_deletion"}): (
        "identical head noun; `network_` is a qualifier"
    ),
    frozenset({"misrouting_bug", "misrouted_scheduling"}): (
        "both name misrouting; `_bug` vs `_scheduling` says what kind of thing "
        "misrouted, not a different failure"
    ),
    frozenset({"misrouting_bug", "misrouted_workload_scheduling"}): (
        "as above, with `workload_` as a further qualifier"
    ),
    frozenset({"misrouted_scheduling", "misrouted_workload_scheduling"}): (
        "identical apart from the `workload_` qualifier"
    ),
}

# Same referent, but a class list could reasonably split them. Listed so
# the count above is not read as the whole story; not counted.
BORDERLINE: dict[frozenset[str], str] = {
    frozenset({"data_loss", "accidental_data_deletion"}): (
        "effect (loss) vs the action that caused it (deletion)"
    ),
    frozenset({"out_of_bounds_read", "crash_on_bad_input"}): (
        "specific fault vs the general failure it produced"
    ),
    frozenset({"resource_contention", "resource_exhaustion"}): (
        "contention (waiting on a resource) vs exhaustion (running out of it); "
        "the document describes both"
    ),
    frozenset({"config_change", "feature_rollout"}): (
        "enabling a feature is a kind of config change; whether rollouts are "
        "their own class is a 01b question"
    ),
    frozenset({"config_change", "content_update"}): (
        "a content/definition-file update is a kind of config change; same question"
    ),
}


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _get(record: dict[str, Any] | None, dotted: str) -> Any:
    node: Any = record
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _labels(reports: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    """Manifest id -> {org, per_run: {run: label | None}}."""
    out: dict[str, dict[str, Any]] = {}
    for report in reports:
        for d in report["documents"]:
            record = (d.get("extraction") or {}).get("record")
            entry = out.setdefault(d["id"], {"org": d["org"], "per_run": {}})
            entry["per_run"][report["run"]] = _get(record, field) if record else None
    return out


def _classify(pair: frozenset[str]) -> tuple[str, str | None]:
    if pair in SAME_CONCEPT:
        return "same_concept", SAME_CONCEPT[pair]
    if pair in BORDERLINE:
        return "borderline", BORDERLINE[pair]
    return "different", None


def analyse(reports: list[dict[str, Any]]) -> dict[str, Any]:
    runs = [r["run"] for r in reports]
    result: dict[str, Any] = {}
    for field in FIELDS:
        labels = _labels(reports, field)
        per_document = []
        pairs_seen: dict[frozenset[str], dict[str, Any]] = {}
        for doc_id in sorted(labels):
            per_run = labels[doc_id]["per_run"]
            strings = [v for v in per_run.values() if isinstance(v, str)]
            distinct = sorted(set(strings))
            nulls = sum(1 for v in per_run.values() if v is None)
            doc_pairs = []
            for a, b in combinations(distinct, 2):
                pair = frozenset({a, b})
                kind, why = _classify(pair)
                doc_pairs.append({"pair": sorted(pair), "judgement": kind, "why": why})
                seen = pairs_seen.setdefault(
                    pair, {"pair": sorted(pair), "judgement": kind, "why": why, "documents": []}
                )
                seen["documents"].append(doc_id)
            per_document.append(
                {
                    "id": doc_id,
                    "org": labels[doc_id]["org"],
                    "labels_by_run": {run: per_run.get(run) for run in runs},
                    "distinct_strings": len(distinct),
                    "null_count": nulls,
                    "strings": distinct,
                    "co_occurring_pairs": doc_pairs,
                }
            )
        pair_list = sorted(pairs_seen.values(), key=lambda p: p["pair"])
        by_kind = {
            kind: [p for p in pair_list if p["judgement"] == kind]
            for kind in ("same_concept", "borderline", "different")
        }
        histogram = {
            str(n): sum(1 for d in per_document if d["distinct_strings"] == n)
            for n in sorted({d["distinct_strings"] for d in per_document})
        }
        result[field] = {
            "documents": len(per_document),
            "documents_with_one_string": sum(
                1 for d in per_document if d["distinct_strings"] == 1 and d["null_count"] == 0
            ),
            "distinct_strings_histogram": histogram,
            "distinct_strings_total": len({s for d in per_document for s in d["strings"]}),
            "co_occurring_pairs_total": len(pair_list),
            "same_concept_pairs": len(by_kind["same_concept"]),
            "borderline_pairs": len(by_kind["borderline"]),
            "different_pairs": len(by_kind["different"]),
            "pairs": by_kind,
            "per_document": per_document,
        }
    return result


def main() -> int:
    run_paths = [p.strip() for p in os.environ.get("RUNS", ",".join(DEFAULT_RUNS)).split(",")]
    out_path = Path(os.environ.get("OUT", "spike/label_stability.json"))
    reports = [_load(p) for p in run_paths]

    fields = analyse(reports)
    report = {
        "rendered_at": datetime.now(UTC).isoformat(),
        "runs": [
            {
                "run": r["run"],
                "report": p,
                "schema_version": r["summary"].get("schema_version"),
                "thinking": r["summary"].get("thinking"),
            }
            for r, p in zip(reports, run_paths, strict=True)
        ],
        "caveats": [
            "Stability is agreement of a run with other runs on the same document, "
            "not with a hand label; a label that is stable can still be wrong.",
            "The class values are open (ADR-001 defers them to 01b), so the model "
            "chooses wording freely each run; that is what this measures.",
            "same_concept / borderline are hand judgements on the label strings "
            "(scripts/label_stability.py); the strict count is same_concept only.",
            "Runs differ in schema version and thinking setting as listed in `runs`; "
            "no run is repeated at one setting, so run-to-run noise at a fixed "
            "setting is not separated from the effect of the setting.",
        ],
        "fields": fields,
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {out_path}")
    for field, f in fields.items():
        print(
            f"{field}: {f['documents_with_one_string']}/{f['documents']} documents got one "
            f"string in every run; distinct-string histogram {f['distinct_strings_histogram']}; "
            f"{f['distinct_strings_total']} distinct strings overall; "
            f"co-occurring pairs {f['co_occurring_pairs_total']} = "
            f"{f['same_concept_pairs']} same_concept + {f['borderline_pairs']} borderline + "
            f"{f['different_pairs']} different"
        )
        for kind in ("same_concept", "borderline"):
            for p in f["pairs"][kind]:
                a, b = p["pair"]
                print(f"  {kind:<13} {a} / {b}  (docs {','.join(p['documents'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
