# M5 Evaluation Protocol — pre-registered

**Committed before any gold-set labeling began.** Nothing in this document may
be revised after labeling starts or after any result is seen. If a criterion
turns out to be badly chosen, it is recorded as failed and the reason is stated
in the results; it is not edited.

Date committed: 09/18/2026
Commit hash of the frozen split manifest: 27bd9224c03512cf04e97b34ef815365e22346a5

---

## 1. Scope

The gold set covers **three fields** across the 30-document corpus:
`trigger.label`, `mechanism.label`, `detection_method`.

It does **not** cover the other nine extracted fields.

Reasons, stated in advance:

- These three are the subject of every taxonomy decision in ADRs 001, 002, 006
  and 008. They are what the project's design work was about.
- All three are categorical. Scoring is exact class equality, so no LLM judge
  is needed and judge validation is not a prerequisite.
- The M0 blind labels, made before any model output existed, cover exactly
  these three fields. They are the only unanchored ground truth in the project
  and they anchor 10 of the 30 documents.
- Temporal fields are deliberately excluded. Review of five routed fields found
  that sources frequently establish that an event occurred without supporting a
  precise timestamp. Hand-labeling those would introduce invented precision
  into the gold set, which is worse than not labeling them.

This scope is a limitation and is reported as one. Accuracy figures in this
project describe three categorical fields, not the whole record.

---

## 2. The split

- **30 documents**, split **12 dev / 18 locked test**.
- Split by stratified random assignment, seeded, with the seed recorded in the
  manifest. Stratified on: whether the document is one of the original 10, and
  the publisher profile recorded in the corpus manifest (large vendor / mid /
  small / individual blog / OSS / non-software).
- The manifest records the seed, the assignment, and every selection filter.
- **The 18-item locked test split is run exactly once**, after all dev-split
  work is complete and no further changes are pending. If it is run a second
  time for any reason, that fact is reported alongside the result.

---

## 3. Labeling protocol

- The 10 M0 documents keep their existing blind labels for `detection_method`.
  Their `trigger` and `mechanism` labels must be re-expressed against the
  closed enums from ADR 008; that mapping is recorded and is acknowledged as
  anchored, since the enums were derived after those labels were made.
- The 20 remaining documents are labeled **blind**: no model output for a
  document may be viewed before that document is labeled.
- Labels are recorded with a date, in `eval/gold_set.json`.
- Where the source genuinely does not support any class, the label is
  `unknown` for `detection_method` and `null` for `trigger`. A forced guess is
  never recorded as ground truth.
- Labeling stops when all 30 are done or when time runs out. A partial gold set
  is reported at its actual size; the split proportions are preserved.

---

## 4. Pre-registered criteria

### 4a. Extraction accuracy

Reported on dev, then once on locked test. No threshold is set for accuracy
itself — there is no prior work to set one against, and inventing a bar to
clear would be dishonest. Accuracy is reported as measured.

What **is** pre-registered:

- **Dev/test gap.** If accuracy on the locked test split is more than **10
  percentage points** below the dev split on any of the three fields, the
  result is reported as evidence of overfitting to the dev split, consistent
  with the taxonomy overfitting already recorded in ADR 008.
- **Reproducibility floor.** Extraction accuracy cannot meaningfully exceed
  cross-run reproducibility, already measured at 86.7% (mechanism) and 90.0%
  (trigger). Accuracy reported above those figures is flagged as a signal that
  the gold set may be anchored to the model rather than independent.

### 4b. Confidence calibration (ADR 009 §5)

- Reliability diagram and ECE for self-reported confidence against correctness,
  per field, on the dev split.
- **Criterion, from ADR 009:** if the mean self-reported confidence on
  incorrect fields is within **0.05** of the mean on correct fields, the signal
  is not separating and the review queue is routing at random. That triggers
  the verifier pass rejected in ADR 009 §3.
- Systematic overconfidence is expected and is not a failure. Being
  uninformative about correctness is.

### 4c. Review queue (ADR 010 §5)

Evaluated **uncapped** on the threshold sweep; the budget cap is an operational
constraint, not part of the routing policy being measured.

- **Review precision ≥ 2.5× the base error rate**, where base error rate is the
  fraction of all scored fields that disagree with gold.
- **Review recall ≥ 80%.**
- Both must hold at the selected operating threshold. Meeting one and not the
  other is a fail, and the routing policy is revised or rejected rather than
  the criterion adjusted.
- Report both as a curve across thresholds, not only at the operating point.
  The curve is the deliverable: it shows the reviewer-effort against
  errors-caught tradeoff.

### 4d. Null-field asymmetry (ADR 011)

- Accuracy on `trigger` is reported **separately for null and non-null gold
  labels**.
- ADR 011 predicts null-field review is less trustworthy than value-field
  review, because confirming absence requires reading the whole document while
  confirming a value requires one passage. If null-label accuracy is materially
  below non-null accuracy, that prediction is confirmed and reported.

### 4e. Model-tier comparison

If budget allows, the dev split only is re-run at a cheaper model tier, and
accuracy per dollar is reported for both. This is optional; if it is not run,
that is stated rather than omitted.

---

## 5. What would make this evaluation invalid

Stated in advance so it cannot be rationalized later:

- Any change to the enums, prompt, schema, or confidence floor after labeling
  begins, unless the whole evaluation is re-run and both results are reported.
- Viewing model output for a document before labeling it.
- Running the locked test split more than once without reporting that it
  happened.
- Revising any threshold in §4 after seeing a result.