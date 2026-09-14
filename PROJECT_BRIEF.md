# Project Brief — Incident Intelligence Pipeline

> **This file is the contract.** Claude Code reads it at the start of every
> session. It is not a spec to implement in one pass. Work one milestone at a
> time and stop at the end of each one.
>
> Lines marked **[DERIVE]** are open decisions. Do NOT answer them in code.
> Kartik writes an ADR for each before implementation begins. If a session
> needs a [DERIVE] answer to proceed, stop and ask.

**Owner:** Kartik Pradip Shelar
**Started:** 2026-09-13
**Repo name:** [DERIVE — pick before M1]

---

## 1. What this is

A pipeline that ingests public engineering postmortems from heterogeneous
sources, extracts a structured incident record from each, routes low-confidence
fields to human review, and measures its own extraction quality against a
frozen hand-labeled gold set.

The output is **rows in a database and a review queue**, not answers to
questions.

One sentence for a recruiter: *ingests messy real-world engineering postmortems
and turns them into a structured, queryable incident database, with measured
extraction accuracy and a human-in-the-loop review path for anything the model
isn't sure about.*

## 2. Why this project

Portfolio gap being closed: **multi-service systems engineering**. Existing
projects (SourceBound, SpectralKD, the multimodal radiology work) demonstrate
ML depth and honest evaluation. None demonstrate a system that other people's
data flows through, with queues, partial failure, migrations, and
observability.

Secondary: the subject matter is native to every tech company, so the value is
legible to an interviewer in one sentence with no domain explanation.

## 3. Non-goals — hard boundaries

Violating any of these turns this into a second SourceBound and destroys its
reason to exist.

- ❌ **No chat or Q&A interface.** Not now, not as a stretch goal.
- ❌ **No RAG, no vector database, no semantic retrieval layer.** Search over
  extracted records is SQL, not embeddings.
- ❌ **No fine-tuning.** Extraction quality comes from schema design,
  prompting, and validation.
- ❌ **No auth/SSO theater.** See DERIVE-04 on whether tenancy is real here.
- ❌ **No microservice count for its own sake.** Every service justifies its
  own process boundary in an ADR.

If a feature idea arrives mid-build, check it against this list first.

---

## 4. M0 — Reality-check spike

**Split: Claude Code does the recon. Kartik does a small blind label set.**
Kartik's time: 30–60 min. Everything else delegated.

### 4a. Delegated to Claude Code

1. Collect 10 postmortems spanning 4+ orgs and 3+ formats (markdown in a repo,
   a vendor status-page writeup, a PDF, a blog post with heavy HTML chrome).
   Start from `danluu/post-mortems` plus public Cloudflare, GitLab, AWS, and
   Datadog writeups.
2. For each draft-schema field (§6), report across the 10: stated explicitly /
   requires inference / genuinely absent.
3. Flag every place the draft schema breaks on real text.
4. Propose 2–3 candidate trigger taxonomies with tradeoffs. **Propose only** —
   DERIVE-01 stays open.
5. Write `spike/FINDINGS.md`.

### 4b. Kartik, blind, BEFORE reading FINDINGS.md or any model output

Target 10 documents, floor is 5. Three fields only: `trigger_class`,
`detection_method`, `contributing_factors`. Skip the other nine fields.

Save to `eval/blind_labels_m0.json`, with a date on each label.

Stop when applications or coursework start pressing. **Five complete beats
seven abandoned** — a partial set is a real gold set, not a failed one.

**Why this part is not delegated:** model-generated ground truth makes the M5
eval circular. It would measure agreement with the labeler, not correctness,
and "who made the gold labels" is a question that voids an eval section. This
is the same discipline as SourceBound's 20 blind-scored judge items.

### 4c. Self-agreement check (~1 week later)

Re-label 3 of the blind set without looking at the originals. This produces
Kartik's own consistency number, the denominator for every model score reported
later. Low self-agreement on `contributing_factors` is a finding worth
publishing, not a problem to hide.

### 4d. Kill criteria

If fewer than 6 of 10 yield a usable record, or the taxonomy cannot be applied
consistently across the blind set, say so in FINDINGS.md and reconsider the
vertical before spending the week.

**Ordering:** 4a can run in one terminal while 4b happens in parallel, as long
as Kartik does not read 4a's output first.

---

## 5. Milestones

Each ends in something that **runs, is tested, and is committed**. Do not start
the next until the previous is green.

| # | Milestone | Done when |
|---|---|---|
| M0 | Reality-check spike | `spike/FINDINGS.md` + `eval/blind_labels_m0.json` exist, schema corrected |
| M1 | Skeleton | `docker compose up` cold → register a source URL → row in Postgres → worker picks it up → status transitions. No intelligence yet. |
| M2 | Ingest & parse | Markdown, HTML, and PDF normalize to text + provenance (source URL, fetch time, content hash). Idempotent on re-ingest. |
| M3 | Extraction v0 | Structured output validated against the schema. Invalid output retries with the validation error fed back. Failures recorded, not swallowed. |
| M4 | Confidence & review queue | Per-field confidence, threshold routing, minimal review UI, corrections written back as ground truth. |
| M5 | Eval harness | Frozen gold set, per-field metrics, calibration curve, pre-registered thresholds, results table in README. |
| M6 | Hardening | OTel traces end to end, `/metrics`, load numbers, cost per document, deployed and publicly reachable. |
| M7 | Writeup | README with architecture diagram, results table, honest limitations, ADR index. |

**Pacing:** M0–M4 during Sep 13–19. M5–M7 the following week. Shipping M1–M4
well beats shipping all seven badly.

---

## 6. Draft extraction schema — v0, to be corrected by the spike

```
incident
  id                   uuid
  source_url           text       -- provenance, always required
  content_hash         text       -- idempotency key
  org                  text
  title                text
  occurred_at          date       -- often absent or imprecise
  affected_services    text[]
  trigger_class        enum       -- [DERIVE-01] taxonomy
  contributing_factors jsonb      -- list of {text, normalized_class}
  detection_method     enum       -- monitoring | customer_report |
                                  -- internal_manual | automated | unknown
  time_to_detect       interval   -- frequently absent or relative
  time_to_mitigate     interval
  blast_radius         jsonb      -- {qualitative, quantitative_if_stated}
  change_induced       boolean    -- was a deploy/config change the trigger
  remediation_actions  text[]
  extraction_status    enum       -- complete | partial | failed
  per_field_confidence jsonb
```

### Known-hard fields — do not pretend these are easy

- **contributing_factors** — causal structure is buried in prose, listed
  inconsistently, and sometimes contradicts itself between the summary and the
  timeline. This is where the interesting failures live. Expect low agreement
  and report it.
- **trigger_class** — the taxonomy is contested and there is no standard one.
  Whatever is chosen must be defended, not inherited.
- **time_to_detect / time_to_mitigate** — often relative ("within minutes") or
  absent. Pick a representation for imprecision instead of coercing to a
  number.
- **blast_radius** — almost never quantified consistently across orgs.

---

## 7. Open decisions — [DERIVE]

ADR in `docs/adr/` for each, written by Kartik in his own words, before
implementation.

| ID | Decision | Why it matters |
|---|---|---|
| **DERIVE-01** | Trigger taxonomy — how many classes, derived from what | Single most defensible-or-not choice in the project |
| **DERIVE-02** | Queue: Postgres `SKIP LOCKED` vs Redis+RQ vs Celery | Adding Redis to look distributed is worse than not having it |
| **DERIVE-03** | Partial extraction failure: persist partial, retry whole, or quarantine | Defines the state machine |
| **DERIVE-04** | Is multi-tenancy real here, or theater | Honest answer may be "not needed". Fake tenancy is worse than none. |
| **DERIVE-05** | Confidence source: self-report, logprob-derived, ensemble disagreement, or verifier pass | Self-reported confidence is poorly calibrated. Must be measured in M5, not assumed. |
| **DERIVE-06** | Review-queue routing metric at a fixed human-review budget | Without it, M4's threshold is arbitrary |
| **DERIVE-07** | Re-ingest semantics when a source is edited upstream | Provenance integrity |

---

## 8. Evaluation design

The eval is not a final step. It is what makes this project Kartik's rather
than generic.

- **Gold set:** 40 incidents, 15 dev / 25 locked test, with a manifest
  recording every selection filter. **Do not run the locked test split until
  M5 results are otherwise final.**
- **Labeling protocol:** the M0 blind set is unanchored ground truth. For the
  remaining items, the model pre-labels and Kartik adjudicates — roughly 4×
  faster than labeling cold. The blind subset exists specifically to detect
  whether adjudication drifted toward accepting plausible model output. Name
  this bias in the README.
- **Self-agreement before model scores** — see §4c.
- **Per-field metrics:**
  - enums → exact match + confusion matrix
  - multi-label (`affected_services`, `remediation_actions`) → precision /
    recall / F1
  - durations → tolerance-based match, absent-vs-wrong scored separately
  - free text (`contributing_factors`) → judge-scored, judge validated against
    blind hand-scoring the way SourceBound's was
- **Calibration:** does per-field confidence predict correctness? Reliability
  diagram and ECE.
- **Pre-registered criteria:** before running M5, commit what counts as success
  and what would falsify the approach.
- **Report what didn't work.** A measured upgrade that lost is worth more than
  a win with no counterfactual.

---

## 9. Systems requirements

Most of this is SWE, not ML. That is the point.

- `docker compose up` works cold on a clean machine, no manual steps
- Postgres with Alembic migrations; no SQLite
- Async worker with retries, exponential backoff, dead-letter handling
- **Idempotent ingest** — same content hash never produces a duplicate
- Object storage for raw source documents (MinIO locally)
- OpenTelemetry traces spanning ingest → parse → extract → review
- Structured JSON logs; `/metrics` endpoint
- **Cost per document as a first-class tracked metric**
- CI on every push: tests, lint, type check, container build
- Deployed and publicly reachable
- README load numbers: p50/p95 extraction latency, documents/hour, cost per
  document

---

## 10. Session protocol for Claude Code

- **One milestone per session.** Do not run ahead.
- **Model split:** Fable for architecture, schema design, and hard debugging.
  Sonnet for boilerplate, tests, and plumbing. Fable draws from usage credits;
  Sonnet draws from the plan limit.
- **Never implement a [DERIVE] item without its ADR.** Stop and ask.
- **End every session green** — tests pass, committed, `docker compose up`
  still works.
- If a session proposes retrieval, embeddings, or a chat interface, refuse and
  point at §3.

---

## 11. Questions to answer cold

Write these as they get decided, not the night before an interview.

1. Why a queue instead of extracting synchronously in the request?
2. Why this trigger taxonomy and not another? What did you reject?
3. Where do confidence scores come from, and are they calibrated? Show the
   reliability diagram.
4. Three of twelve fields fail to extract. What does the system do, and why is
   that right?
5. What is the human review budget optimizing for?
6. What broke that you didn't expect during the spike?
7. What did you measure that didn't work?
8. What would you do differently with another month?

---

## 12. Success criteria

- Runs cold from a clean clone with one command
- README leads with a results table and an honest limitations section
- Every architectural decision has an ADR written by Kartik
- At least one measured negative result reported prominently
- Kartik can answer all eight questions in §11 without notes
