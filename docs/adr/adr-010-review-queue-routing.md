# ADR 010 — Review queue routing (DERIVE-06)

## 1. Decision

**Unit of review: per-field.** The queue routes uncertain extracted fields,
not whole documents. A reviewer receives the field, its current value, and
enough surrounding source context to judge that field without re-reviewing
unrelated fields.

**What the queue optimizes: maximum errors corrected.** At a fixed human
review budget, route the fields most likely to be wrong so that each reviewer
action has the highest expected error-correction value.

**Routing rule:** rank eligible fields by ascending self-reported confidence
and route the lowest-confidence fields first, subject to the available review
budget. M4 uses a provisional configurable confidence floor of **0.70**; M5
sweeps the threshold against the gold set and selects the operating point while
preserving the review budget.

---

## 2. The choice

### Unit of review

A document has twelve-odd extracted fields. A typical document has one or two
that are uncertain and the rest that are fine.

**Per-document review** is simpler to build and simpler to reason about: one
queue, one reviewer action, one approved record. It also spends reviewer
attention re-reading fields nobody doubted. Rejected because the review unit
is too coarse for the confidence signal the system already produces.

**Per-field review** routes only the uncertain fields. It gets more correction
per reviewer-minute and makes the confidence signal operationally useful. It
requires per-field review state in the schema and a UI that shows enough
surrounding context to judge one field correctly. Chosen because the queue
exists to allocate limited reviewer attention to uncertainty rather than to
re-read entire records.

### What the queue optimizes

At a fixed human review budget — say a reviewer can look at N fields — the queue
is choosing which N. The chosen objective is:

- **Maximum errors corrected.** Route the N fields most likely to be wrong.
  This is directly measurable against the M5 gold set and does not require
  inventing field-importance weights or a record-completeness utility
  function.

This is preferred over the alternatives for M4 because it measures the direct
purpose of routing: finding and correcting extraction mistakes under a fixed
human-time constraint.

---

## 3. The threshold

M4 ships with a **provisional confidence floor of 0.70**, stored as
configuration rather than a code constant. The queue first excludes fields at
or above the floor, then ranks the remaining fields by ascending confidence
and routes the lowest-confidence fields until the available review budget is
reached.

The value `0.70` is an operating starting point, not a claim that 0.70 has a
special probabilistic meaning. In M5, sweep the threshold against the gold set
and record review precision, review recall, and review volume across the full
threshold curve. The production threshold is selected from that measured
tradeoff rather than tuned against the M5 answers after the fact.

The provisional M4 confidence floor is 0.70. It is not an optimized threshold: runs 10 and 11 place the mean confidence of stable extractions at approximately 0.750 and unstable extractions at approximately 0.638, so 0.70 is chosen as an operating point between the two observed groups. M5 will replace it with a threshold selected from the gold-set sweep.

---

## 4. What I rejected

**Maximum consequential errors corrected.** Rejected for M4 because it requires
field-importance weights that have not been independently justified. Adding
those weights would introduce another product assumption into the routing
experiment and make the result harder to interpret.

**Maximum record-level correctness.** Rejected because it optimizes a different
unit from the queue itself. It can prefer distributing reviews across records
rather than correcting the largest number of incorrect fields, depending on
how many fields in each record are wrong. That objective may be appropriate for
a later product decision, but it is not the cleanest test of confidence-based
field routing.

**Routing everything below a confidence floor with no budget cap.** Rejected
because it makes review volume a function of model behaviour rather than of
available human time; a bad batch would route everything and the queue would
stop being a queue.

---

## 5. How I'd know I was wrong

The queue is a classifier: it predicts which fields are wrong. It is scored against the M5 gold set.

Review precision — of the fields routed to a human, what fraction were actually wrong? Low precision means the queue is spending reviewer time on correct extractions.
Review recall — of the fields that were actually wrong, what fraction got routed? Low recall means errors are reaching the database unreviewed.

The pre-registered success criteria are:

Review precision: at least 2.5× the base error rate measured on the M5 gold set, where base error rate is the fraction of all evaluated extracted fields that disagree with gold. The target is stated as lift because an absolute precision requirement is not interpretable before the underlying error prevalence is known.
Review recall: at least 80% of incorrect fields are routed for review.

Precision and recall are evaluated on the uncapped threshold sweep, not under the operational review-budget cap. The budget cap constrains deployment volume; it is not part of the routing policy's quality evaluation.

Report both metrics at the chosen threshold and as a curve across thresholds. The curve is the interesting artifact: it shows the reviewer-effort-versus-error-caught tradeoff, which is the actual product question this milestone exists to answer.
---

## 6. After review

Review is not a terminal UI action. It changes system state.

Once a routed field is reviewed, its review state is recorded so the same extraction is not routed again. If the reviewer confirms the extraction, the field is marked reviewed without changing its extracted value. If the reviewer corrects it, the corrected value is written back alongside the original extraction and review provenance rather than silently overwriting the model output.

Reviewed corrections become eligible ground-truth input for the gold set and subsequent M5 evaluation. This closes the loop from extraction → confidence → routing → human correction → evaluation data.

Re-extraction of the source creates a new extraction result and may therefore create a new review decision; prior review state applies to the extraction that was actually reviewed.

---