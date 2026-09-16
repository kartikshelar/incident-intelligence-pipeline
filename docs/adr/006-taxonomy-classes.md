# ADR 006 — Trigger and mechanism class lists (DERIVE-01b)

ADR 001 decided the *structure*: `trigger` (nullable) and `mechanism` (required,
single-valued). It deferred the actual class values to this document.

---

## 1. Decision

`mechanism` becomes a closed enum. Extraction must select from a fixed class
list; free-text mechanism labels are no longer accepted.

`trigger` remains free text. Across the measured runs it was stable on 7 of 10 documents with zero confirmed semantic collisions, so the current evidence does not justify constraining it to a closed enum. Revisit this decision if future runs show materially lower stability or confirmed collisions between distinct initiating events.

This ADR also amends ADR 001 with a rule for anomalous conditions (§5) and
removes a corpus leak from the schema text (§6).

---

## 2. Why mechanism must be closed

Measured across runs 02, 04, 05, 06 and 07 on the same ten documents:

- `mechanism` produced a single label string on only **4 of 10** documents.
  `trigger` was stable on **7 of 10**.
- Of 18 co-occurring mechanism label pairs, **4 are the same concept in
  different wording**: `route_deletion` / `network_route_deletion`, and three
  spellings of the misrouting concept (`misrouting_bug`,
  `misrouted_scheduling`, `misrouted_workload_scheduling`).
- Three further pairs are borderline same-concept: `data_loss` /
  `accidental_data_deletion`, `out_of_bounds_read` / `crash_on_bad_input`,
  `resource_contention` / `resource_exhaustion`.
- Run 07 introduced **two new mechanism strings** (`lock_contention`,
  `misrouting_resource_exhaustion`) on a corpus it had already seen six times.

The problem is not that mechanisms are numerous. It is that the *same* concept
receives a different string on each pass. A field whose vocabulary changes
between runs cannot be scored: per-field agreement would measure string luck
rather than extraction correctness, and the M5 confusion matrix would have a
different axis on every run.

A closed enum is therefore required for measurement, not merely preferred.

---

## 3. The class list

### Mechanism

| Class | One-line definition |
|---|---|
| `accidental_data_deletion` | Production data is unintentionally deleted by an operator action or command. |
| `cascading_overload` | Interacting failures increase load or reduce effective capacity until the system enters a self-reinforcing overload state. |
| `limit_violation` | An input or state exceeds an enforced implementation limit, causing the consuming component to fail. |
| `lock_contention` | Concurrent operations contend for a shared lock or synchronization primitive, blocking forward progress. |
| `null_pointer_failure` | Missing or null data reaches a code path that does not safely handle the null value, causing execution failure. |
| `out_of_bounds_read` | Code attempts to read beyond the bounds of an available buffer, array, or input structure. |
| `race_condition` | Concurrent operations interact in a timing-dependent way that produces an invalid or inconsistent system state. |
| `resource_exhaustion` | A finite computational or infrastructure resource is depleted, preventing the affected component from continuing normal operation. |
| `route_deletion` | Required network routes are removed, breaking connectivity between affected components or nodes. |
| `unsafe_failover` | Failover moves service state or responsibility to a topology that the dependent system cannot safely support. |
| `workload_misrouting` | Work is incorrectly directed to an execution environment or destination other than the intended one. |
| `other` | No existing mechanism class fits; every use of `other` must be reviewed before the gold set is frozen. |


### Trigger

`trigger` remains open vocabulary. Unlike `mechanism`, its observed vocabulary has not shown the same degree of label fragmentation: it was stable on 7 of 10 documents, with no confirmed same-concept/different-string collisions in the measured runs. That conclusion depends on semantic adjudication rather than exact string equality; changes such as `config_change` / `content_update` and `config_change` / `feature_rollout` are judgment calls about whether the extracted initiating event is materially the same. Keeping the field open avoids prematurely collapsing distinct initiating events into broad classes such as `config_change`. Revisit the decision if corpus expansion or repeat runs produce confirmed semantic collisions or materially lower concept-level stability.

For scoring, trigger agreement is a manually adjudicated concept match, not an exact label-string match. Two trigger extractions agree when the `label`, `quote`, and `description` together identify the same initiating change or external event. They disagree when they identify materially different initiating events, or when one extraction is `null` and the other identifies a trigger.

---

## 4. What I rejected

**Leaving mechanism as free text.** Simpler, and it captures nuance a closed
list loses. Rejected because the stability data shows the labels are not
reproducible, which makes M5's per-field metrics meaningless. The nuance is not
lost in any case — the `quote` and `description` fields still carry it.

**A single flat taxonomy across both fields.** Rejected in ADR 001 and not
revisited here.

**Keeping `crash_on_bad_input` as a single class.** The model applied it to both
Cloudflare's hard feature-limit breach and GCP's null dereference. Rejected
because those are different failure modes that happen to share a surface
description; split into `limit_violation` and `null_pointer_failure`.

---

## 5. Amendment to ADR 001 — anomalous conditions

Investigation of the AWS trigger instability (see
`nullable_trigger_regression.md`) found no schema-text regression. Across four
default-thinking runs, AWS returned `null` three times and `operational_delay`
once, and the non-null run was not the one with the weakest schema wording.
Self-reported trigger confidence sat at 0.55–0.75 in both null and non-null
runs.

The cause is an ambiguity in ADR 001, not a defect. AWS contains an anomalous
condition immediately before failure — a DNS Enactor experiencing unusual
delays — and ADR 001 does not say whether an anomalous condition counts as an
initiating event.

**Rule:** An anomalous condition is never the trigger. If the anomaly is attributable to an identifiable change or external event, that change or event is the trigger. If no such change or external event is identifiable, `trigger` is `null`.

---

## 6. Corpus leak in the schema text

The wire schema currently names AWS as the example of a null trigger. AWS is in
the evaluation corpus, so the schema is supplying the answer to a document the
system is scored on.

Replace with a described case or a synthetic example that is not in the corpus.
No document in the gold set may be named in the schema text.

---

## 7. Anchoring caveat

These classes are derived from model output that has already been produced on
the evaluation corpus. The re-label pass in §8 is therefore not fully
independent: both the classes and the labels descend from the same runs. The
M0 blind labels, made before any model output existed, remain the only
unanchored ground truth in the project. This limitation belongs in the README's
limitations section, not only here.

---

## 8. How I'd know I was wrong

Pre-registered, decided before looking:

- Once the mechanism classes exist, re-label the ten M0 documents. Require agreement of at least **8 of 10** on each field between that pass and the M0 blind labels, allowing for fields the blind set did not cover. `mechanism` agreement is exact closed-enum class equality. `trigger` agreement uses the concept-match adjudication rule in §3: exact label-string equality is not required, but both labels must identify the same initiating change or external event.
- Re-run extraction on the ten documents twice at `adaptive:low`. Require
  **identical** mechanism labels across both runs on at least 9 of 10
  documents. Matches where both runs answered `other` do not count toward
  the 9: two documents can land on `other` for unrelated reasons and still
  agree. The enum exists to make labels reproducible; if it does not, it has
  failed its purpose.
- If more than **20% of documents** land on `other`, the class list is too
  narrow and needs revision before the gold set is frozen. Stated as a
  proportion so the criterion survives the corpus expansion to 30.
