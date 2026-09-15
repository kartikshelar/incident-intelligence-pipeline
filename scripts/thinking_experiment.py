"""Compare extraction runs made at different thinking settings against a
baseline run, document by document, and write one JSON report.

Reads the per-document reports `scripts/extraction_run.py` writes; calls
no model and touches no database. It reports; it does not decide.

    BASELINE   report to compare against (default spike/extraction_run_04.json)
    ARMS       comma-separated `name=path` list, e.g.
               "adaptive:low=spike/extraction_run_05.json,disabled=spike/extraction_run_06.json"
    OUT        default spike/thinking_experiment.json

Per arm, against the baseline:
  - output tokens and cost, total and per document (with deltas)
  - first-attempt output tokens: the same comparison with validation
    retries removed, since a retry resends the document and its tokens
    depend on what failed validation, not on the thinking setting
  - mean validation attempts
  - exact-match agreement on trigger.label, mechanism.label and
    detection_method, as a count out of N with every mismatch listed
  - every field that flipped between a value and null (walked
    recursively; list items compared by position)
  - whether the AWS document (manifest id C) returned trigger = null

Usage: python -m scripts.thinking_experiment
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AGREEMENT_FIELDS = ("trigger.label", "mechanism.label", "detection_method")
AWS_ID = "C"


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _get(record: dict[str, Any] | None, dotted: str) -> Any:
    node: Any = record
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _docs(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Manifest id -> what this comparison needs from one document."""
    out: dict[str, dict[str, Any]] = {}
    for d in report["documents"]:
        extraction = d.get("extraction") or {}
        attempt_log = extraction.get("attempt_log") or []
        first = attempt_log[0]["usage"] if attempt_log else {}
        out[d["id"]] = {
            "org": d["org"],
            "outcome": d["outcome"],
            "validation_attempts": d["validation_attempts"],
            "output_tokens": d["tokens"]["output_tokens"],
            "first_attempt_output_tokens": int(first.get("output_tokens", 0)),
            "cost_usd": d["cost_usd"],
            "record": extraction.get("record"),
            "thinking": extraction.get("thinking"),
            "schema_version": extraction.get("schema_version"),
            "model": extraction.get("model"),
            "validation_errors": [
                a["validation_error"] for a in attempt_log if not a.get("ok")
            ],
        }
    return out


def _null_flips(base: Any, other: Any, path: str = "") -> list[dict[str, Any]]:
    """Leaf paths where exactly one side is null. Lists are compared by
    position over their common length; a longer list is a length change,
    not a flip, and is not reported here."""
    flips: list[dict[str, Any]] = []
    if (base is None) != (other is None):
        flips.append(
            {"path": path or "<root>", "baseline": _brief(base), "arm": _brief(other)}
        )
        return flips
    if isinstance(base, dict) and isinstance(other, dict):
        for key in sorted(set(base) | set(other)):
            if key in base and key in other:
                flips.extend(_null_flips(base[key], other[key], f"{path}.{key}" if path else key))
    elif isinstance(base, list) and isinstance(other, list):
        for i, (b, o) in enumerate(zip(base, other, strict=False)):
            flips.extend(_null_flips(b, o, f"{path}[{i}]"))
    return flips


def _brief(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if len(value) <= 80 else value[:77] + "..."
    if isinstance(value, dict):
        # Enough to see what kind of thing was there: the label for
        # trigger/mechanism, the `at` for anchors, else the keys.
        for key in ("label", "at", "text"):
            if key in value:
                return {key: _brief(value[key])}
        return {"keys": sorted(value)}
    if isinstance(value, list):
        return {"items": len(value)}
    return str(value)


def _mean(values: list[Any]) -> float | None:
    numbers = [v for v in values if v is not None]
    return statistics.mean(numbers) if numbers else None


def _total(values: list[Any]) -> float | int | None:
    numbers = [v for v in values if v is not None]
    return sum(numbers) if numbers else None


def _totals(docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = sorted(docs)
    return {
        "documents": len(ids),
        "complete": sum(1 for i in ids if docs[i]["outcome"] == "complete"),
        "output_tokens": _total([docs[i]["output_tokens"] for i in ids]),
        "first_attempt_output_tokens": _total(
            [docs[i]["first_attempt_output_tokens"] for i in ids]
        ),
        "cost_usd": _total([docs[i]["cost_usd"] for i in ids]),
        "cost_usd_per_document": _mean([docs[i]["cost_usd"] for i in ids]),
        "output_tokens_per_document": _mean([docs[i]["output_tokens"] for i in ids]),
        "mean_validation_attempts": _mean([docs[i]["validation_attempts"] for i in ids]),
        "validation_attempts_histogram": {
            str(n): [docs[i]["validation_attempts"] for i in ids].count(n)
            for n in sorted({docs[i]["validation_attempts"] for i in ids} - {None})
        },
        "thinking": sorted({docs[i]["thinking"] for i in ids if docs[i]["thinking"]}),
        "schema_version": sorted(
            {docs[i]["schema_version"] for i in ids if docs[i]["schema_version"]}
        ),
        "model": sorted({docs[i]["model"] for i in ids if docs[i]["model"]}),
    }


def _ratio(a: float | int | None, b: float | int | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round(a / b, 3)


def compare(name: str, base: dict[str, dict[str, Any]], arm: dict[str, dict[str, Any]]) -> dict:
    ids = sorted(set(base) & set(arm))
    base_totals, arm_totals = _totals({i: base[i] for i in ids}), _totals({i: arm[i] for i in ids})

    per_document = []
    for i in ids:
        b, a = base[i], arm[i]
        per_document.append(
            {
                "id": i,
                "org": b["org"],
                "outcome": a["outcome"],
                "validation_attempts": {
                    "baseline": b["validation_attempts"],
                    "arm": a["validation_attempts"],
                },
                "output_tokens": {
                    "baseline": b["output_tokens"],
                    "arm": a["output_tokens"],
                    "delta": a["output_tokens"] - b["output_tokens"],
                },
                "first_attempt_output_tokens": {
                    "baseline": b["first_attempt_output_tokens"],
                    "arm": a["first_attempt_output_tokens"],
                    "delta": a["first_attempt_output_tokens"] - b["first_attempt_output_tokens"],
                },
                "cost_usd": {
                    "baseline": b["cost_usd"],
                    "arm": a["cost_usd"],
                    "delta": (
                        None
                        if b["cost_usd"] is None or a["cost_usd"] is None
                        else round(a["cost_usd"] - b["cost_usd"], 6)
                    ),
                },
                "arm_validation_errors": a["validation_errors"],
            }
        )

    agreement: dict[str, Any] = {}
    for field in AGREEMENT_FIELDS:
        mismatches = []
        for i in ids:
            bv, av = _get(base[i]["record"], field), _get(arm[i]["record"], field)
            if bv != av:
                mismatches.append({"id": i, "org": base[i]["org"], "baseline": bv, "arm": av})
        agreement[field] = {
            "agree": len(ids) - len(mismatches),
            "of": len(ids),
            "mismatches": mismatches,
        }

    null_flips = []
    for i in ids:
        for flip in _null_flips(base[i]["record"], arm[i]["record"]):
            null_flips.append({"id": i, "org": base[i]["org"], **flip})

    aws_base = base.get(AWS_ID, {}).get("record") or {}
    aws_arm = arm.get(AWS_ID, {}).get("record") or {}
    return {
        "arm": name,
        "documents_compared": len(ids),
        "totals": {"baseline": base_totals, "arm": arm_totals},
        "ratios_arm_over_baseline": {
            "output_tokens": _ratio(arm_totals["output_tokens"], base_totals["output_tokens"]),
            "first_attempt_output_tokens": _ratio(
                arm_totals["first_attempt_output_tokens"],
                base_totals["first_attempt_output_tokens"],
            ),
            "cost_usd": _ratio(arm_totals["cost_usd"], base_totals["cost_usd"]),
        },
        "mean_validation_attempts": {
            "baseline": base_totals["mean_validation_attempts"],
            "arm": arm_totals["mean_validation_attempts"],
        },
        "agreement_with_baseline": agreement,
        "null_flips": null_flips,
        "aws_trigger": {
            "baseline": _get(aws_base, "trigger"),
            "arm": _get(aws_arm, "trigger"),
            "arm_is_null": _get(aws_arm, "trigger") is None,
        },
        "per_document": per_document,
    }


def main() -> int:
    baseline_path = os.environ.get("BASELINE", "spike/extraction_run_04.json")
    arms_spec = os.environ.get("ARMS", "")
    out_path = Path(os.environ.get("OUT", "spike/thinking_experiment.json"))
    if not arms_spec:
        print("ARMS is required, e.g. ARMS='adaptive:low=spike/extraction_run_05.json'")
        return 2

    baseline = _load(baseline_path)
    base_docs = _docs(baseline)
    arms = []
    for item in arms_spec.split(","):
        name, _, path = item.partition("=")
        arm_report = _load(path)
        arms.append(
            {
                "report": path,
                "run": arm_report["run"],
                "started_at": arm_report["started_at"],
                **compare(name.strip(), base_docs, _docs(arm_report)),
            }
        )

    caveats = [
        "Agreement is with the baseline run, not with ground truth: none of these "
        "fields has been scored against a hand label here. A null flip means the arm "
        "and the baseline disagree on whether the document states the value; which one "
        "is right is not established by this report.",
        "One run per arm. Validation retries and label choices vary between runs at "
        "the same setting, so single-document differences are within run-to-run noise "
        "unless they repeat.",
        "Total output tokens and validation attempts include retries; a retry resends "
        "the document and its tokens depend on what failed validation. "
        "first_attempt_output_tokens is the comparison with retries removed.",
    ]
    base_versions = set(_totals(base_docs)["schema_version"])
    for arm in arms:
        arm_versions = set(arm["totals"]["arm"]["schema_version"])
        if arm_versions != base_versions:
            caveats.append(
                f"Schema version differs: baseline {sorted(base_versions)} vs arm "
                f"{arm['arm']!r} {sorted(arm_versions)}. Any change to the validation "
                "contract between them (app/extract/schema.py changelog) is confounded "
                "with the thinking setting in the attempt and total-token comparisons."
            )

    report = {
        "rendered_at": datetime.now(UTC).isoformat(),
        "caveats": caveats,
        "baseline": {
            "report": baseline_path,
            "run": baseline["run"],
            "started_at": baseline["started_at"],
            "thinking": sorted({d["thinking"] for d in base_docs.values() if d["thinking"]})
            or ["default (column added by migration 0006 after this run; nothing was sent)"],
            "schema_version": sorted(
                {d["schema_version"] for d in base_docs.values() if d["schema_version"]}
            ),
            "aws_trigger": _get(base_docs.get(AWS_ID, {}).get("record"), "trigger"),
        },
        "pricing_usd_per_mtok": baseline.get("pricing_usd_per_mtok"),
        "arms": arms,
    }
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"wrote {out_path}")
    b = _totals(base_docs)
    print(f"baseline {baseline['run']}: output={b['output_tokens']} cost=${b['cost_usd']:.4f} "
          f"attempts={b['mean_validation_attempts']:.2f}")
    for arm in arms:
        t, r = arm["totals"]["arm"], arm["ratios_arm_over_baseline"]
        agree = {f: f"{v['agree']}/{v['of']}" for f, v in arm["agreement_with_baseline"].items()}
        print(
            f"{arm['arm']:<14} {arm['run']}: output={t['output_tokens']} "
            f"({r['output_tokens']}x) first-attempt {r['first_attempt_output_tokens']}x "
            f"cost=${t['cost_usd']:.4f} ({r['cost_usd']}x) "
            f"attempts={t['mean_validation_attempts']:.2f} agree={agree} "
            f"null_flips={len(arm['null_flips'])} "
            f"aws_trigger_null={arm['aws_trigger']['arm_is_null']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
