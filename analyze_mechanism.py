"""Ad hoc: count (doc_id, mechanism.label) occurrences across every
extraction_run_0*.json report — a quick check of which mechanism label a
document received run to run, used while debugging taxonomy drift during
M0-era runs. Not part of any pipeline; kept for reference."""

import collections
import glob
import json

c: collections.Counter[tuple[str, str]] = collections.Counter()
for f in sorted(glob.glob("spike/extraction_run_0*.json")):
    d = json.load(open(f))
    docs = d.get("documents", []) if d else []
    for doc in docs:
        if not doc:
            continue
        extraction = doc.get("extraction", {})
        if not extraction:
            continue
        rec = extraction.get("record", {})
        if not rec:
            continue
        mech = rec.get("mechanism")
        if not mech:
            continue
        doc_id = doc.get("id")
        label = mech.get("label")
        if doc_id and label:
            c[(doc_id, label)] += 1

for (doc_id, label), n in sorted(c.items()):
    print(doc_id, label, n)
