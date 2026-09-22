"""M5 §4 scoring, dev split only (M5_PROTOCOL.md §4).

Scores extraction_run_12 (schema v0.8, post ADR-012) against eval/gold_set.json,
restricted to the 12 documents assigned "dev" in eval/split_manifest.json.

Computes exactly what M5_PROTOCOL.md §4 asks for and nothing else:
  4a. Per-field accuracy + confusion matrix for trigger, mechanism,
      detection_method; reproducibility-floor check.
  4b. Calibration: reliability diagram bins + ECE per field; mean confidence
      correct vs incorrect; 0.05 separation criterion.
  4c. Review queue, uncapped threshold sweep: precision/recall vs base error
      rate, at pre-registered floor 0.70 and across the curve.
  4d. Trigger accuracy split by null vs non-null gold.
  Plus: M0-anchored (10) vs blind (20); seen_before (7) vs rest;
  software_defect rate gold vs predicted.

No thresholds are tuned here. The 0.70 floor, the 2.5x/80% review bar, the
0.05 calibration bar, and the 86.7%/90.0% reproducibility floor are all taken
verbatim from the ADRs / protocol, not fit to this data.

Usage: python -m eval.scripts.score_m5_dev
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "eval" / "gold_set.json"
SPLIT_PATH = ROOT / "eval" / "split_manifest.json"
RUN_PATH = ROOT / "spike" / "extraction_run_12.json"
OUT_PATH = ROOT / "eval" / "m5_results_dev.md"

FIELDS = ["trigger", "mechanism", "detection_method"]
GOLD_KEY = {
    "trigger": "trigger_label",
    "mechanism": "mechanism_label",
    "detection_method": "detection_method",
}

REPRO_FLOOR = {"mechanism": 0.867, "trigger": 0.900}
REVIEW_FLOOR_M4 = 0.70
CALIBRATION_SEPARATION_BAR = 0.05
REVIEW_PRECISION_LIFT = 2.5
REVIEW_RECALL_BAR = 0.80


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dev_ids(split: dict[str, Any]) -> list[str]:
    return sorted(
        [doc_id for doc_id, assign in split["assignment"].items() if assign == "dev"],
        key=lambda x: (len(x), x),
    )


def record_for(run: dict[str, Any], doc_id: str) -> dict[str, Any]:
    for doc in run["documents"]:
        if doc["id"] == doc_id:
            extraction = doc.get("extraction") or {}
            return extraction.get("record") or {}
    raise KeyError(doc_id)


def confidence_for(run: dict[str, Any], doc_id: str) -> dict[str, Any]:
    for doc in run["documents"]:
        if doc["id"] == doc_id:
            extraction = doc.get("extraction") or {}
            return extraction.get("per_field_confidence") or {}
    raise KeyError(doc_id)


def pred_value(record: dict[str, Any], field: str) -> Any:
    if field == "detection_method":
        return record.get("detection_method")
    node = record.get(field)
    if isinstance(node, dict):
        return node.get("label")
    return node


def norm(v: Any) -> Any:
    """Normalize for equality: JSON null and python None both become None."""
    return v


def fmt_val(v: Any) -> str:
    return "null" if v is None else str(v)


def main() -> int:
    gold_list = load_json(GOLD_PATH)
    split = load_json(SPLIT_PATH)
    run = load_json(RUN_PATH)

    gold_by_id = {g["document_id"]: g for g in gold_list}
    dev = dev_ids(split)

    missing = [d for d in dev if d not in gold_by_id]
    lines: list[str] = []

    def w(s: str = "") -> None:
        lines.append(s)

    w("# M5 Results — DEV SPLIT ONLY")
    w()
    w("Scored per M5_PROTOCOL.md §4. Numbers only; no interpretation, no")
    w("threshold revision. Run scored: spike/extraction_run_12.json (schema")
    w("v0.8, adaptive:low, post ADR-012). Gold: eval/gold_set.json. Split:")
    w("eval/split_manifest.json (seed 20260918).")
    w()
    w(f"Dev-split document count: {len(dev)}")
    w(f"Dev-split document IDs: {', '.join(dev)}")
    if missing:
        w(f"WARNING: dev IDs missing from gold set: {missing}")
    w()

    # Per-document field values, confidences, correctness
    per_doc: dict[str, dict[str, Any]] = {}
    for doc_id in dev:
        gold = gold_by_id[doc_id]
        record = record_for(run, doc_id)
        conf = confidence_for(run, doc_id)
        entry: dict[str, Any] = {"org": gold.get("org", ""), "seen_before": gold.get("seen_before"),
                                  "anchored": gold.get("anchored_trigger_mechanism", False)}
        for field in FIELDS:
            gv = norm(gold.get(GOLD_KEY[field]))
            pv = norm(pred_value(record, field))
            correct = gv == pv
            entry[field] = {
                "gold": gv,
                "pred": pv,
                "correct": correct,
                "confidence": conf.get(field),
            }
        per_doc[doc_id] = entry

    # ---------------- 4a: accuracy per field + confusion matrix ----------------
    w(f"## 4a. Extraction accuracy (dev split, n={len(dev)})")
    w()
    for field in FIELDS:
        n = len(dev)
        correct = sum(1 for d in dev if per_doc[d][field]["correct"])
        acc = correct / n if n else 0.0
        w(f"### {field}")
        w()
        w(f"Accuracy: {correct}/{n} = {acc:.4f} ({acc*100:.1f}%)")
        w()
        # confusion matrix: rows = gold, cols = pred
        gold_vals = [per_doc[d][field]["gold"] for d in dev]
        pred_vals = [per_doc[d][field]["pred"] for d in dev]
        labels = sorted({fmt_val(v) for v in gold_vals + pred_vals})
        w("Confusion matrix (rows=gold, cols=predicted):")
        w()
        header = "| gold\\pred | " + " | ".join(labels) + " |"
        sep = "|---|" + "---|" * len(labels)
        w(header)
        w(sep)
        for gl in labels:
            row_counts = []
            for pl in labels:
                c = sum(
                    1
                    for d in dev
                    if fmt_val(per_doc[d][field]["gold"]) == gl
                    and fmt_val(per_doc[d][field]["pred"]) == pl
                )
                row_counts.append(str(c))
            w(f"| {gl} | " + " | ".join(row_counts) + " |")
        w()
        w("Per-document detail:")
        w()
        w("| doc | gold | pred | correct |")
        w("|---|---|---|---|")
        for d in dev:
            e = per_doc[d][field]
            w(f"| {d} | {fmt_val(e['gold'])} | {fmt_val(e['pred'])} | {e['correct']} |")
        w()

    w("### Reproducibility-floor check")
    w()
    w("Pre-registered floors (cross-run reproducibility, already measured):")
    w(f"- mechanism: {REPRO_FLOOR['mechanism']*100:.1f}%")
    w(f"- trigger: {REPRO_FLOOR['trigger']*100:.1f}%")
    w()
    for field in ("mechanism", "trigger"):
        n = len(dev)
        correct = sum(1 for d in dev if per_doc[d][field]["correct"])
        acc = correct / n if n else 0.0
        floor = REPRO_FLOOR[field]
        flag = "ABOVE FLOOR (flagged per §4a)" if acc > floor else "at or below floor"
        w(f"- {field}: accuracy {acc:.4f} ({acc*100:.1f}%) vs floor {floor*100:.1f}% -> {flag}")
    w()

    # ---------------- 4b: calibration ----------------
    w("## 4b. Confidence calibration (ADR 009 §5, dev split)")
    w()
    bin_edges = [i / 10 for i in range(11)]  # 0.0..1.0 in 10 bins

    for field in FIELDS:
        w(f"### {field}")
        w()
        pts = [
            (per_doc[d][field]["confidence"], per_doc[d][field]["correct"])
            for d in dev
            if per_doc[d][field]["confidence"] is not None
        ]
        missing_conf = [d for d in dev if per_doc[d][field]["confidence"] is None]
        if missing_conf:
            w(f"Documents missing confidence for {field}: {missing_conf}")
            w()
        n = len(pts)
        w(f"n with confidence recorded: {n}")
        w()

        # Reliability diagram: 10 fixed-width bins [0.0,0.1) ... [0.9,1.0]
        w("Reliability diagram (10 fixed-width bins):")
        w()
        w("| bin | n | mean confidence | accuracy |")
        w("|---|---|---|---|")
        ece = 0.0
        for i in range(10):
            lo, hi = bin_edges[i], bin_edges[i + 1]
            if i == 9:
                bin_pts = [(c, ok) for c, ok in pts if lo <= c <= hi]
            else:
                bin_pts = [(c, ok) for c, ok in pts if lo <= c < hi]
            bn = len(bin_pts)
            if bn == 0:
                w(f"| [{lo:.1f},{hi:.1f}{']' if i == 9 else ')'} | 0 | — | — |")
                continue
            mean_conf = sum(c for c, _ in bin_pts) / bn
            bin_acc = sum(1 for _, ok in bin_pts if ok) / bn
            ece += (bn / n) * abs(bin_acc - mean_conf)
            close = "]" if i == 9 else ")"
            w(f"| [{lo:.1f},{hi:.1f}{close} | {bn} | {mean_conf:.4f} | {bin_acc:.4f} |")
        w()
        w(f"ECE ({field}): {ece:.4f}")
        w()

        correct_confs = [c for c, ok in pts if ok]
        incorrect_confs = [c for c, ok in pts if not ok]
        mean_correct = sum(correct_confs) / len(correct_confs) if correct_confs else None
        mean_incorrect = sum(incorrect_confs) / len(incorrect_confs) if incorrect_confs else None
        w(f"Mean confidence on correct ({field}): "
          f"{mean_correct:.4f} (n={len(correct_confs)})" if mean_correct is not None
          else f"Mean confidence on correct ({field}): n/a (n=0)")
        w(f"Mean confidence on incorrect ({field}): "
          f"{mean_incorrect:.4f} (n={len(incorrect_confs)})" if mean_incorrect is not None
          else f"Mean confidence on incorrect ({field}): n/a (n=0)")
        if mean_correct is not None and mean_incorrect is not None:
            gap = mean_correct - mean_incorrect
            passes = abs(gap) > CALIBRATION_SEPARATION_BAR
            verdict = (
                "PASS (separating)"
                if passes
                else "FAIL (within 0.05 — not separating, per ADR-009 §5)"
            )
            w(f"Gap (correct - incorrect): {gap:.4f}")
            w(f"0.05 separation criterion: {verdict}")
        else:
            w("0.05 separation criterion: cannot be evaluated (one class empty)")
        w()

    # ---------------- 4c: review queue, uncapped ----------------
    w("## 4c. Review queue, uncapped (ADR 010 §5, dev split)")
    w()
    w("Scored per-field, across trigger/mechanism/detection_method, all 12 dev")
    w("documents (36 scored fields total). Routing rule: field is 'routed' if")
    w("its self-reported confidence < threshold.")
    w()

    all_scored = []
    for d in dev:
        for field in FIELDS:
            e = per_doc[d][field]
            if e["confidence"] is None:
                continue
            all_scored.append((d, field, e["confidence"], e["correct"]))

    total_scored = len(all_scored)
    total_wrong = sum(1 for *_r, correct in all_scored if not correct)
    base_error_rate = total_wrong / total_scored if total_scored else 0.0
    w(f"Total scored fields (with confidence): {total_scored}")
    w(f"Total wrong: {total_wrong}")
    w(f"Base error rate: {base_error_rate:.4f} ({base_error_rate*100:.2f}%)")
    w()

    thresholds = [round(0.05 * i, 2) for i in range(0, 21)]  # 0.00 .. 1.00

    def sweep_row(thresh: float) -> tuple[int, int, int, float | None, float | None]:
        routed = [r for r in all_scored if r[2] < thresh]
        n_routed = len(routed)
        routed_wrong = sum(1 for *_r, correct in routed if not correct)
        precision = routed_wrong / n_routed if n_routed else None
        recall = routed_wrong / total_wrong if total_wrong else None
        return n_routed, routed_wrong, total_wrong, precision, recall

    w("Threshold sweep (uncapped — every field below threshold is routed):")
    w()
    w(
        "| threshold | n routed | routed & wrong | precision | recall "
        "| precision >= 2.5x base | recall >= 80% |"
    )
    w("|---|---|---|---|---|---|---|")
    required_precision = REVIEW_PRECISION_LIFT * base_error_rate
    op_row = None
    for t in thresholds:
        n_routed, routed_wrong, tw, precision, recall = sweep_row(t)
        prec_s = f"{precision:.4f}" if precision is not None else "n/a (0 routed)"
        rec_s = f"{recall:.4f}" if recall is not None else "n/a (0 errors)"
        prec_ok = precision is not None and precision >= required_precision
        prec_pass = "PASS" if prec_ok else "FAIL"
        rec_ok = recall is not None and recall >= REVIEW_RECALL_BAR
        rec_pass = "PASS" if rec_ok else "FAIL"
        w(
            f"| {t:.2f} | {n_routed} | {routed_wrong} | {prec_s} | {rec_s} "
            f"| {prec_pass} | {rec_pass} |"
        )
        if abs(t - REVIEW_FLOOR_M4) < 1e-9:
            op_row = (t, n_routed, routed_wrong, precision, recall, prec_pass, rec_pass)
    w()

    if op_row:
        t, n_routed, routed_wrong, precision, recall, prec_pass, rec_pass = op_row
        w(f"### At the pre-registered M4 operating threshold ({REVIEW_FLOOR_M4:.2f})")
        w()
        w(f"n routed: {n_routed}")
        w(f"routed & wrong: {routed_wrong}")
        w(f"Precision: {precision:.4f}" if precision is not None else "Precision: n/a (0 routed)")
        w(f"Recall: {recall:.4f}" if recall is not None else "Recall: n/a (0 errors)")
        w(f"Base error rate: {base_error_rate:.4f}")
        w(f"Required precision (2.5x base error rate): {required_precision:.4f}")
        w(f"Precision criterion: {prec_pass}")
        w(f"Recall criterion (>=80%): {rec_pass}")
        overall = "PASS" if prec_pass == "PASS" and rec_pass == "PASS" else "FAIL"
        w(f"Overall (both must hold): {overall}")
    else:
        w("(0.70 not in swept threshold list — should not happen)")
    w()

    # ---------------- 4d: null vs non-null trigger ----------------
    w("## 4d. Trigger accuracy: null vs non-null gold (ADR 011 §4, dev split)")
    w()
    null_docs = [d for d in dev if per_doc[d]["trigger"]["gold"] is None]
    nonnull_docs = [d for d in dev if per_doc[d]["trigger"]["gold"] is not None]

    def acc_over(docs: list[str]) -> tuple[int, int, float | None]:
        n = len(docs)
        c = sum(1 for d in docs if per_doc[d]["trigger"]["correct"])
        return c, n, (c / n if n else None)

    c_null, n_null, acc_null = acc_over(null_docs)
    c_nn, n_nn, acc_nn = acc_over(nonnull_docs)
    w(f"Null-gold trigger docs (n={n_null}): {null_docs}")
    if acc_null is not None:
        w(f"Null-gold accuracy: {c_null}/{n_null} = {acc_null:.4f}")
    else:
        w("Null-gold accuracy: n/a (n=0)")
    w()
    w(f"Non-null-gold trigger docs (n={n_nn}): {nonnull_docs}")
    if acc_nn is not None:
        w(f"Non-null-gold accuracy: {c_nn}/{n_nn} = {acc_nn:.4f}")
    else:
        w("Non-null-gold accuracy: n/a (n=0)")
    w()
    if acc_null is not None and acc_nn is not None:
        w(f"Difference (non-null minus null): {acc_nn - acc_null:.4f}")
    w()

    # ---------------- Also report ----------------
    w("## Also reported")
    w()

    w("### Accuracy: 10 anchored M0 documents vs 20 blind documents (restricted to dev split)")
    w()
    anchored_dev = [d for d in dev if per_doc[d]["anchored"]]
    blind_dev = [d for d in dev if not per_doc[d]["anchored"]]
    w(f"Anchored M0 docs in dev split (n={len(anchored_dev)}): {anchored_dev}")
    w(f"Blind docs in dev split (n={len(blind_dev)}): {blind_dev}")
    w()
    w("| field | anchored acc | blind acc |")
    w("|---|---|---|")
    for field in FIELDS:
        ca, na = (
            sum(1 for d in anchored_dev if per_doc[d][field]["correct"]),
            len(anchored_dev),
        )
        aa = ca / na if na else None
        cb, nb = (
            sum(1 for d in blind_dev if per_doc[d][field]["correct"]),
            len(blind_dev),
        )
        ab = cb / nb if nb else None
        aa_s = f"{ca}/{na}={aa:.4f}" if aa is not None else "n/a"
        ab_s = f"{cb}/{nb}={ab:.4f}" if ab is not None else "n/a"
        w(f"| {field} | {aa_s} | {ab_s} |")
    w()

    w("### Accuracy: seen_before documents vs the rest (dev split)")
    w()
    seen_dev = [d for d in dev if gold_by_id[d].get("seen_before") is True]
    rest_dev = [d for d in dev if gold_by_id[d].get("seen_before") is not True]
    w(f"seen_before=true docs in dev split (n={len(seen_dev)}): {seen_dev}")
    w(f"remaining docs in dev split (n={len(rest_dev)}): {rest_dev}")
    w()
    w("| field | seen_before acc | rest acc |")
    w("|---|---|---|")
    for field in FIELDS:
        cs, ns = (
            sum(1 for d in seen_dev if per_doc[d][field]["correct"]),
            len(seen_dev),
        )
        as_ = cs / ns if ns else None
        cr, nr = (
            sum(1 for d in rest_dev if per_doc[d][field]["correct"]),
            len(rest_dev),
        )
        ar = cr / nr if nr else None
        as_s = f"{cs}/{ns}={as_:.4f}" if as_ is not None else "n/a"
        ar_s = f"{cr}/{nr}={ar:.4f}" if ar is not None else "n/a"
        w(f"| {field} | {as_s} | {ar_s} |")
    w()

    w("### software_defect rate: gold vs predicted (mechanism field, dev split)")
    w()
    gold_sd = sum(1 for d in dev if per_doc[d]["mechanism"]["gold"] == "software_defect")
    pred_sd = sum(1 for d in dev if per_doc[d]["mechanism"]["pred"] == "software_defect")
    n = len(dev)
    w(f"Gold software_defect count: {gold_sd}/{n} = {gold_sd/n:.4f}")
    w(f"Predicted software_defect count: {pred_sd}/{n} = {pred_sd/n:.4f}")
    gold_sd_docs = [d for d in dev if per_doc[d]["mechanism"]["gold"] == "software_defect"]
    pred_sd_docs = [d for d in dev if per_doc[d]["mechanism"]["pred"] == "software_defect"]
    w(f"Gold software_defect docs: {gold_sd_docs}")
    w(f"Predicted software_defect docs: {pred_sd_docs}")
    w()

    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
