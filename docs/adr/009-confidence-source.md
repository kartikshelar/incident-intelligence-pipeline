# ADR 009 — Confidence source (DERIVE-05)

## 1. Decision

Per-field confidence stays **model self-report**, stored with
`confidence_source='self_report'`, as it is today.

Its calibration is treated as unvalidated until measured in M5. Nothing
downstream may assume the numbers are well calibrated before then.

M4 records cross-run disagreement as a **secondary validation signal**, but
not as a production routing signal. Disagreement is useful for evaluating
whether self-report tracks instability, while generating multiple extractions
for every document would add cost and is therefore deferred as an operational
signal unless M5 shows that self-report is insufficient.

---

## 2. Why

Self-report is the only confidence signal available at zero marginal cost, and
the corpus gives direct evidence that it carries signal rather than noise:

- All seven `other` mechanism cases in run 09 came back at confidence
  **0.50–0.60**, against a corpus where confident extractions sit higher.
- The AWS trigger, the single most genuinely ambiguous call in the corpus,
  reported **0.55–0.75** across four runs — in both the `null` and the non-null
  runs alike. The model reported uncertainty on a decision it was in fact
  making inconsistently.

That is the property a review queue needs: the score has to be lower where the
extraction is more likely to be wrong. It does not need to be a calibrated
probability to be useful for *ranking*, which is all M4 requires.

---

## 3. What I rejected

**Logprob-derived confidence.** Unavailable on this path. ADR 005 records that
`output_config.format` was dropped because the compiled grammar exceeded the
API limit, and the remaining path does not expose token logprobs for the
structured fields.

**A separate verifier pass.** A second model call scoring the first extraction.
Rejected for M4 on cost: it roughly doubles per-document spend for a signal
that self-report appears to already provide. Worth revisiting if M5 shows
self-report is uninformative.

**Ensemble disagreement as the primary signal.** Running each document N times
and using cross-run agreement as confidence. This is the most reliable of the
options and there is direct evidence it works — see §4 — but it multiplies cost
by N on every document, including the ones that were never in doubt. Rejected
as the primary signal for that reason.

---

## 4. The validation that is already paid for

Runs 10 and 11 are the same 30 documents at identical settings with different
`run_id`s. They give a free test of whether self-report predicts instability:

- Mechanism agreed on 26/30; four documents disagreed (LaunchDarkly, Turso,
  Twilio, Firefox).
- Trigger agreed on 27/30; three disagreed, two of them `null`/non-null flips.
- On the four documents whose **mechanism** changed between runs, the mean
  self-reported mechanism confidence was **0.638**, versus **0.750** on the
  26 documents whose mechanism remained the same: a **0.113 confidence-point
  gap**.
- On the three documents whose **trigger** changed between runs, the mean
  self-reported trigger confidence was **0.608**, versus **0.728** on the
  27 documents whose trigger remained the same: a **0.119 confidence-point
  gap**.

Self-reported confidence is therefore lower on the fields that showed
cross-run instability in this already-paid validation. M4's primary routing
signal has measured evidence behind it rather than relying only on the
assumption that the model's scores are informative.

This does not establish calibration or correctness. It establishes only that
self-report separates stable from unstable extractions in these runs. M5 still
has to test whether confidence separates correct from incorrect fields on the
gold set.

The sample is small: the mechanism result is based on four unstable documents and the trigger result on three. The direction and magnitude support using self-report in M4, but the estimates are sensitive to individual documents and are not treated as calibration evidence; M5 remains the definitive test.

---

## 5. How I'd know I was wrong

- In M5, plot a reliability diagram of self-reported confidence against
  correctness on the gold set and report ECE. Systematic overconfidence is
  expected and is not by itself a failure; the field being *uninformative*
  about correctness is.
- Concretely: if the mean self-reported confidence on incorrect fields is
  within **0.05** of the mean on correct fields, the signal is not separating
  and the review queue is routing at random. That triggers the verifier pass.
- If the runs available before M4 show no confidence gap between unstable and
  stable fields, treat that as early evidence of the same failure and do not
  wait for M5.

---
