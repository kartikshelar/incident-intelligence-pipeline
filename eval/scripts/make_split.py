"""Build the frozen M5 dev/test split (M5_PROTOCOL.md §2).

30 documents, 12 dev / 18 locked test, stratified random and seeded.
Strata: (original-10 vs new-20) x (publisher profile from the corpus
manifest). Run once; the output is committed and never regenerated.

Publisher profile for documents A-J: the corpus manifest's `profile` key
was introduced with the 2026-09-16 expansion (K onward) and was never
backfilled onto A-J. This script infers it from each document's own org
using the manifest's own profile_key definitions (large cloud/CDN/infra
vendor or hyperscaler = large_vendor; open-source project or foundation =
oss_project) rather than leaving A-J unstratified: A/C/D/E/F/G/H/I are
large infrastructure vendors by the same standard K/L/M were classified
under, B (GitLab) is the same tier, and J is the Kubernetes SIG Testing
org that AA/AB (the same org, added later) are explicitly `oss_project`
for. The inferred mapping is recorded in the output manifest under
`profile_source` so it is auditable, not silently assumed.

Allocation: each stratum's dev count is round(len(stratum) * 12/30),
adjusted by largest-remainder so the 8 per-stratum dev counts sum to
exactly 12 (and test counts to exactly 18). Within each stratum, which
specific documents land in dev is decided by the seeded RNG, so the
result is reproducible from the seed alone.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 20260918  # date this split was drawn, per M5_PROTOCOL.md commit date
DEV_SIZE = 12
TEST_SIZE = 18

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "spike" / "corpus_manifest.json"
OUT_PATH = ROOT / "eval" / "split_manifest.json"

ORIGINAL_10 = frozenset("ABCDEFGHIJ")

# A-J predate the `profile` key (added with the 2026-09-16 expansion).
# Inferred here from each org against the manifest's own profile_key
# definitions; see module docstring.
INFERRED_PROFILE = {
    "A": "large_vendor",  # Cloudflare
    "B": "large_vendor",  # GitLab
    "C": "large_vendor",  # AWS
    "D": "large_vendor",  # Datadog
    "E": "large_vendor",  # GitHub
    "F": "large_vendor",  # CrowdStrike
    "G": "large_vendor",  # Google Cloud
    "H": "large_vendor",  # Slack
    "I": "large_vendor",  # Roblox
    "J": "oss_project",  # Kubernetes SIG Testing (test-infra); matches AA/AB, same org
}


def load_documents() -> list[dict]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    docs = []
    for d in manifest["documents"]:
        doc_id = d["id"]
        is_original = doc_id in ORIGINAL_10
        if "profile" in d and d["profile"] is not None:
            profile = d["profile"]
            profile_source = "corpus_manifest"
        else:
            profile = INFERRED_PROFILE[doc_id]
            profile_source = "inferred_from_org_by_make_split.py"
        docs.append(
            {
                "id": doc_id,
                "org": d["org"],
                "is_original_10": is_original,
                "profile": profile,
                "profile_source": profile_source,
            }
        )
    if len(docs) != 30:
        raise ValueError(f"expected 30 documents, found {len(docs)}")
    return docs


def largest_remainder_allocation(sizes: dict[tuple, int], total_dev: int) -> dict[tuple, int]:
    """Per-stratum dev counts proportional to stratum size, rounded so the
    counts sum to exactly `total_dev` (largest-remainder / Hamilton
    method): each stratum gets floor(share), then the strata with the
    largest fractional remainder each get one more, until the total dev
    seats are exhausted."""
    total_docs = sum(sizes.values())
    exact = {k: v * total_dev / total_docs for k, v in sizes.items()}
    base = {k: int(v) for k, v in exact.items()}
    remainder = total_dev - sum(base.values())
    order = sorted(sizes, key=lambda k: (exact[k] - base[k], k), reverse=True)
    for k in order[:remainder]:
        base[k] += 1
    return base


def build_split(docs: list[dict], seed: int) -> dict:
    rng = random.Random(seed)
    strata: dict[tuple, list[str]] = {}
    for d in docs:
        key = (d["is_original_10"], d["profile"])
        strata.setdefault(key, []).append(d["id"])

    sizes = {k: len(v) for k, v in strata.items()}
    dev_counts = largest_remainder_allocation(sizes, DEV_SIZE)

    assignment: dict[str, str] = {}
    strata_report = []
    for key in sorted(strata):
        ids = sorted(strata[key])  # deterministic order before shuffling
        shuffled = ids[:]
        rng.shuffle(shuffled)
        n_dev = dev_counts[key]
        dev_ids = sorted(shuffled[:n_dev])
        test_ids = sorted(shuffled[n_dev:])
        for doc_id in dev_ids:
            assignment[doc_id] = "dev"
        for doc_id in test_ids:
            assignment[doc_id] = "test"
        strata_report.append(
            {
                "is_original_10": key[0],
                "profile": key[1],
                "size": len(ids),
                "dev": dev_ids,
                "test": test_ids,
            }
        )

    dev_total = sum(1 for v in assignment.values() if v == "dev")
    test_total = sum(1 for v in assignment.values() if v == "test")
    if dev_total != DEV_SIZE or test_total != TEST_SIZE:
        raise ValueError(f"split sizes wrong: dev={dev_total} test={test_total}")

    return {
        "created": "2026-09-18",
        "protocol": "eval/M5_PROTOCOL.md §2",
        "seed": seed,
        "method": "stratified random assignment; per-stratum dev/test counts by "
        "largest-remainder allocation proportional to 12/30, documents within each "
        "stratum assigned by random.Random(seed).shuffle()",
        "strata_definition": "(is_original_10, profile), profile from spike/corpus_manifest.json "
        "(inferred for documents A-J per this script's module docstring)",
        "dev_size": DEV_SIZE,
        "test_size": TEST_SIZE,
        "total_documents": len(docs),
        "documents": {d["id"]: {k: v for k, v in d.items() if k != "id"} for d in docs},
        "assignment": assignment,
        "strata": strata_report,
        "selection_filters": {
            "original_10": "spike/corpus_manifest.json top-level selection_filter: hand-picked "
            "for org/format spread across danluu/post-mortems plus Cloudflare/GitLab/AWS/Datadog; "
            "not random, not a gold-set sample.",
            "expansion_20": "spike/corpus_manifest.json expansion_2026_09_16.selection_filter: "
            "weighted away from the large-vendor profile of the original 10, hand-picked, not "
            "random, not a gold-set sample.",
        },
    }


def main() -> None:
    docs = load_documents()
    result = build_split(docs, SEED)
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    dev = sorted(k for k, v in result["assignment"].items() if v == "dev")
    test = sorted(k for k, v in result["assignment"].items() if v == "test")
    print(f"wrote {OUT_PATH}")
    print(f"dev  ({len(dev)}): {dev}")
    print(f"test ({len(test)}): {test}")


if __name__ == "__main__":
    main()
