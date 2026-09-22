"""ADR-006 §8 two-run reproducibility check at schema v0.6.

Compares two extraction_run reports (same schema, thinking, corpus) and
reports agreement on mechanism and trigger, per the pre-registered bar:
identical mechanism labels on >= 9 of 10 (here, stated as a proportion of
30), excluding pairs where both runs answered `other`. Also reports
trigger identical-class agreement, null/non-null flips, and whether
retries show invented (schema-violating) keys.

Usage: python -m scripts.repro_check RUN_A RUN_B
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _record(doc: dict[str, Any]) -> dict[str, Any] | None:
    extraction = doc.get("extraction")
    if not extraction:
        return None
    return extraction.get("record")


def _field(record: dict[str, Any] | None, dotted: str) -> Any:
    node: Any = record
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _attempt_errors(doc: dict[str, Any]) -> list[str]:
    extraction = doc.get("extraction") or {}
    errors = []
    for a in extraction.get("attempt_log", []) or []:
        err = a.get("validation_error")
        if err:
            errors.append(str(err))
    return errors


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python -m scripts.repro_check RUN_A RUN_B")
        return 1
    a_path, b_path = sys.argv[1], sys.argv[2]
    a = _load(a_path)
    b = _load(b_path)

    a_docs = {d["id"]: d for d in a["documents"]}
    b_docs = {d["id"]: d for d in b["documents"]}
    ids = sorted(set(a_docs) & set(b_docs), key=lambda x: (len(x), x))

    print(f"run A: {a_path}  schema={a['summary']['schema_version']} "
          f"thinking={a['summary']['thinking']}")
    print(f"run B: {b_path}  schema={b['summary']['schema_version']} "
          f"thinking={b['summary']['thinking']}")
    print(f"documents compared: {len(ids)}")
    print()

    mech_total_eligible = 0
    mech_agree = 0
    mech_both_other = 0
    mech_disagree_docs = []

    trig_agree = 0
    trig_disagree_docs = []
    trig_null_flips = []

    retry_docs_a = []
    retry_docs_b = []
    invented_key_errors = []

    for doc_id in ids:
        da, db = a_docs[doc_id], b_docs[doc_id]
        ra, rb = _record(da), _record(db)
        org = da.get("org", "")

        mech_a = _field(ra, "mechanism.label")
        mech_b = _field(rb, "mechanism.label")
        if mech_a == "other" and mech_b == "other":
            mech_both_other += 1
        else:
            mech_total_eligible += 1
            if mech_a == mech_b:
                mech_agree += 1
            else:
                mech_disagree_docs.append((doc_id, org, mech_a, mech_b))

        trig_a = _field(ra, "trigger.label")
        trig_b = _field(rb, "trigger.label")
        if trig_a == trig_b:
            trig_agree += 1
        else:
            trig_disagree_docs.append((doc_id, org, trig_a, trig_b))
        if (trig_a is None) != (trig_b is None):
            trig_null_flips.append((doc_id, org, trig_a, trig_b))

        attempts_a = da.get("validation_attempts")
        attempts_b = db.get("validation_attempts")
        if attempts_a and attempts_a > 1:
            retry_docs_a.append((doc_id, org, attempts_a))
        if attempts_b and attempts_b > 1:
            retry_docs_b.append((doc_id, org, attempts_b))

        for label, doc in (("A", da), ("B", db)):
            for err in _attempt_errors(doc):
                lower = err.lower()
                if (
                    "extra input" in lower
                    or "additional" in lower
                    or "unexpected" in lower
                    or "not permitted" in lower
                ):
                    invented_key_errors.append((doc_id, org, label, err[:300]))

    print("=== Mechanism ===")
    print(f"eligible (excludes both-`other`): {mech_total_eligible} / {len(ids)} "
          f"(both `other`: {mech_both_other})")
    print(f"identical mechanism class: {mech_agree} / {mech_total_eligible} eligible "
          f"= {mech_agree} / {len(ids)} of all 30")
    bar = "PASS" if mech_agree >= round(0.9 * len(ids)) else "FAIL (bar: >=9/10, i.e. >=90%)"
    print(f"ADR-006 §8 bar (>=9/10 equivalent, i.e. >=90%): "
          f"{mech_agree}/{len(ids)} = {mech_agree/len(ids):.1%}  [{bar}]")
    if mech_disagree_docs:
        print("disagreements:")
        for doc_id, org, ma, mb in mech_disagree_docs:
            print(f"  {doc_id:<4}{org[:40]:<42} A={ma!r:<30} B={mb!r}")
    print()

    print("=== Trigger ===")
    print(f"identical trigger class (incl. null==null): {trig_agree} / {len(ids)} "
          f"= {trig_agree/len(ids):.1%}")
    if trig_disagree_docs:
        print("disagreements:")
        for doc_id, org, ta, tb in trig_disagree_docs:
            print(f"  {doc_id:<4}{org[:40]:<42} A={ta!r:<30} B={tb!r}")
    print(f"null/non-null flips: {len(trig_null_flips)}")
    for doc_id, org, ta, tb in trig_null_flips:
        print(f"  {doc_id:<4}{org[:40]:<42} A={ta!r:<30} B={tb!r}")
    print()

    print("=== Retry failure modes ===")
    print(f"run A docs with >1 validation attempt: {len(retry_docs_a)} -> {retry_docs_a}")
    print(f"run B docs with >1 validation attempt: {len(retry_docs_b)} -> {retry_docs_b}")
    print(f"attempt-log errors mentioning invented/extra/unexpected keys: "
          f"{len(invented_key_errors)}")
    for doc_id, org, label, err in invented_key_errors:
        print(f"  [{label}] {doc_id} {org[:40]}: {err}")
    if not invented_key_errors and (retry_docs_a or retry_docs_b):
        print("  (retries occurred but no attempt_log error matched invented/extra-key patterns; "
              "see raw attempt_log errors below)")
        for label, docs, report in (("A", retry_docs_a, a_docs), ("B", retry_docs_b, b_docs)):
            for doc_id, org, _n in docs:
                errs = _attempt_errors(report[doc_id])
                for e in errs:
                    print(f"  [{label}] {doc_id} {org[:40]}: {e[:300]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
