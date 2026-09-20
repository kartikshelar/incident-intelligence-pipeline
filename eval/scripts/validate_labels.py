"""Validate gold-set labels against the closed enums (M5_PROTOCOL.md §3).

Checks every document's `trigger_label`, `mechanism_label`, and
`detection_method` in a gold-set JSON file (the format produced by
eval/gold_set_template.json and, once labeling is complete, expected of
eval/gold_set.json) against the enums enforced by extraction itself
(app/extract/taxonomy.py) — the same source ENUMS.md was built from, so
a label that validates here is guaranteed consistent with what the model
schema allows and with the reference sheet a labeler read.

Rules (M5_PROTOCOL.md §3):
  - trigger_label:      one of TRIGGER_CLASSES, or the JSON value null
                         (never the string "null", and never empty).
  - mechanism_label:     one of MECHANISM_CLASSES. Not nullable.
  - detection_method:    one of DETECTION_METHODS. Not nullable ("unknown"
                          is the enum's own escape value, not an empty
                          field).
  - labeled_on:          must be set (non-empty) for every document that
                          has any non-empty label field, since a label
                          without a date does not meet the protocol's
                          "labels are recorded with a date" rule (§3).

An empty string ("") in any of the three label fields is reported
separately from an invalid-value error, since an unlabeled template
document is expected (this is what eval/gold_set_template.json ships
with) and is not itself a taxonomy violation — but is a violation to
still be empty in a completed gold set.

Usage:
    python -m eval.scripts.validate_labels eval/gold_set_template.json
    python -m eval.scripts.validate_labels eval/gold_set.json --strict

--strict exits non-zero (and is meant for CI / pre-freeze checks) if
*any* document has an empty label field, not only an invalid one. Without
--strict, empty fields are reported but do not fail the run — this is
the mode to use against the template right after generation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.extract.taxonomy import (
    DETECTION_METHODS,
    MECHANISM_CLASSES,
    TRIGGER_CLASSES,
)

REQUIRED_FIELDS = ("document_id", "org", "text_hash", "trigger_label", "mechanism_label",
                   "detection_method", "labeled_on")


def validate_document(doc: dict) -> list[str]:
    """Return a list of problem descriptions for one document; empty means clean."""
    problems: list[str] = []
    doc_id = doc.get("document_id", "<missing document_id>")

    for field in REQUIRED_FIELDS:
        if field not in doc:
            problems.append(f"{doc_id}: missing required field '{field}'")

    trigger = doc.get("trigger_label", "")
    if trigger is None:
        pass  # null is a valid trigger label (no identifiable initiating event)
    elif trigger == "":
        problems.append(f"{doc_id}: trigger_label is empty")
    elif trigger not in TRIGGER_CLASSES:
        problems.append(
            f"{doc_id}: trigger_label '{trigger}' is not in the closed enum "
            f"({', '.join(TRIGGER_CLASSES)}, or null)"
        )

    mechanism = doc.get("mechanism_label", "")
    if mechanism == "":
        problems.append(f"{doc_id}: mechanism_label is empty")
    elif mechanism is None:
        problems.append(f"{doc_id}: mechanism_label is null (mechanism is required, not nullable)")
    elif mechanism not in MECHANISM_CLASSES:
        problems.append(
            f"{doc_id}: mechanism_label '{mechanism}' is not in the closed enum "
            f"({', '.join(MECHANISM_CLASSES)})"
        )

    detection = doc.get("detection_method", "")
    if detection == "":
        problems.append(f"{doc_id}: detection_method is empty")
    elif detection is None:
        problems.append(
            f"{doc_id}: detection_method is null (use the string 'unknown' instead, "
            "per ADR-002 — null is not a valid value for this field)"
        )
    elif detection not in DETECTION_METHODS:
        problems.append(
            f"{doc_id}: detection_method '{detection}' is not in the closed enum "
            f"({', '.join(DETECTION_METHODS)})"
        )

    any_label_set = any(doc.get(f) not in ("", None) for f in
                         ("trigger_label", "mechanism_label", "detection_method"))
    if any_label_set and not doc.get("labeled_on"):
        problems.append(
            f"{doc_id}: has at least one label set but labeled_on is empty "
            "(M5_PROTOCOL.md §3: labels are recorded with a date)"
        )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="gold-set JSON file to validate")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also fail (non-zero exit) on empty label fields, not only invalid ones",
    )
    args = parser.parse_args()

    data = json.loads(args.path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print(f"error: {args.path} does not contain a JSON list of documents", file=sys.stderr)
        return 2

    all_problems: list[str] = []
    empty_count = 0
    invalid_count = 0
    for doc in data:
        problems = validate_document(doc)
        all_problems.extend(problems)
        empty_count += sum(1 for p in problems if "is empty" in p)
        invalid_count += sum(1 for p in problems if "is empty" not in p)

    print(f"Checked {len(data)} documents against app/extract/taxonomy.py enums.")
    print(f"  {empty_count} empty label field(s)")
    print(f"  {invalid_count} invalid/other problem(s)")
    print()

    if all_problems:
        for p in all_problems:
            print(f"  - {p}")
    else:
        print("  No problems found.")

    fail = invalid_count > 0 or (args.strict and empty_count > 0)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
