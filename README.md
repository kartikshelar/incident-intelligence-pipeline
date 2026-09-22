# Incident Intelligence Pipeline

This ingests public engineering postmortems, extracts a structured
incident record from each with an LLM, and routes low-confidence fields
to a human review queue whose corrections become gold-set ground truth.
It measures its own extraction quality against a hand-labeled gold set
rather than assuming the model's output is correct. The output is rows
in a database and a review queue, not answers to questions — there is no
chat interface, no RAG, and no fine-tuning (`PROJECT_BRIEF.md` §3).

See [`docs/PROJECT_BRIEF.md`](docs/PROJECT_BRIEF.md) for the full
contract and milestone plan (including its own session-protocol notes
for the coding-agent workflow this project was built with —
process detail, not part of the system itself, which is why it lives
under `docs/` rather than at the repo root), [`docs/adr/`](docs/adr/)
for every architectural decision, and
[`eval/M5_PROTOCOL.md`](eval/M5_PROTOCOL.md) for the pre-registered
evaluation design this README reports against.

## Status

**Both pre-registered success criteria failed, and the second failure
follows mechanically from the first.** Self-reported model confidence —
the only signal this system has for deciding which extracted fields a
human should check — does not separate correct extractions from
incorrect ones on the dev split (0.00–0.03 confidence-point gap between
right and wrong, against a 0.05 bar). Because the review queue is just a
threshold cut on that same score, it inherits the failure: at no
threshold does it hit the pre-registered precision *and* recall bar at
once. Both are measured, both are reported, and neither result was
revised after being seen — see [What failed](#what-failed) for the full
derivation.

Everything else in this project — the extraction pipeline, the schema
and taxonomy design (12 ADRs), the queue, the observability layer, and
the deployment below — exists to make that measurement possible and
honest. It is a working system with one honestly negative headline
result, not a system that is broken.

**Try it:** [`incident-intel-kartik-b115ad1b5098.herokuapp.com`](https://incident-intel-kartik-b115ad1b5098.herokuapp.com)
(Heroku; `render.yaml` also ships in this repo and works the same way, it
is just not the one currently live — see [Deployment](#deployment)).
Seeded with the 30-document corpus on deploy, extraction replayed from
the frozen `spike/extraction_run_12.json` report — no live model calls
made to seed it. `/health` and `/metrics` are open; `/ui/review` requires
a login (ask for the demo credentials — not published here since this
README is public).

---

## Architecture

Four processes, one Postgres database, no other services:

```
                         POST /sources {url}
                                │
                                ▼
                       ┌─────────────────┐
                       │   API (FastAPI) │
                       └────────┬────────┘
                                │ insert source + job
                                │ (same transaction)
                                ▼
                   ┌────────────────────────┐
                   │   Postgres              │
                   │  sources · documents ·  │
                   │  jobs · extractions ·   │
                   │  field_reviews          │
                   └───────────┬────────────┘
                        SELECT … FOR UPDATE
                         SKIP LOCKED (ADR-003)
                                │
                                ▼
                       ┌─────────────────┐
                       │     worker      │
                       │ ingest → parse  │──── fetch + normalize
                       │  → extract      │      (md / HTML / PDF)
                       └────────┬────────┘
                                │ Anthropic Messages API
                                │ schema-in-system-prompt,
                                │ validate + retry (ADR-005)
                                ▼
                     extractions row (record,
                     per-field confidence,
                     attempt_log)
                                │
                    routing pass (ADR-010):
                    confidence < floor (0.70)?
                                │
                     ┌──────────┴──────────┐
                     │ yes: routed          │ no: stays
                     ▼                      │ unreviewed
           ┌───────────────────┐            │
           │ /ui/review         │            │
           │ one field at a time│            │
           │ candidate passages │            │
           │ (keyword cues,     │            │
           │ ADR-011 — never    │            │
           │ the model's quote) │            │
           └─────────┬─────────┘            │
                      │ accept / correct     │
                      ▼                      ▼
              field_reviews.corrected_value
                      │
                      ▼
           eval/gold_set.json + M5 scoring
           (this README's Results section)
```

**Services**: API (FastAPI, request/response only), worker (claims jobs,
runs ingest and extraction), Postgres (state, queue, and storage — raw
bytes and text live in `documents`, not MinIO; deferred per the README's
Storage section), and the review UI (server-rendered pages served by the
API process, gated by HTTP Basic Auth on the deployed instance —
[Deployment](#deployment)). No Redis, no Celery, no vector store.

**Queue** (ADR-003): Postgres `SELECT … FOR UPDATE SKIP LOCKED`. The job
row and the record it produces commit in the same transaction, so a job
can never point at a record that was never written. Chosen over
Redis+RQ/Celery because the workload (single user, jobs take
seconds-to-minutes, no throughput requirement) doesn't justify a second
service boundary; see the ADR for what Postgres-as-queue costs (owning
retry/backoff/dead-letter and the visibility-timeout reclaim yourself).

**Review loop** (ADR-010, ADR-011): the unit of review is the field, not
the document — a typical document has one or two fields worth a human's
attention, and per-document review would spend reviewer time re-reading
fields nobody doubted. Fields are ranked by ascending self-reported
confidence and routed below a configurable floor (0.70, untuned). The
review page shows candidate passages chosen by fixed keyword cues, never
the model's own cited quote — showing the extractor's justification would
make the reviewer check the argument instead of the field, and corrections
feed directly into the gold set M5 scores against, so that bias would be
invisible and permanent (ADR-011 §3). A correction is stored beside the
model's original value, never over it.

---

## Observability

One OpenTelemetry trace per document, spanning the full path:

```
job                              (renamed job.ingest / job.extract once claimed)
├── job.claim                    the SKIP LOCKED claim itself
├── ingest_source                 (ingest jobs only)
│   ├── fetch
│   └── parse
└── extract_document              (extract jobs only)
    ├── extract.attempt (× attempts)
    │   ├── llm.call               the model request — a retry IS attempt 2+,
    │   │                          same span kind, distinguished by attempt number
    │   └── validation             schema validation; failures recorded here,
    │                              never just a log line
    └── route_pending              ADR-010's post-extraction routing pass
```

An `ingest` job's `extract` job is a separate trace (a separate queue
claim) linked by `document.id`, carried as an attribute on every span in
both. Exporter is configurable (`APP_OTEL_EXPORTER=console` prints spans
to stdout and needs no collector — the default, so this works with
`docker compose up` alone; `otlp` sends to `APP_OTEL_EXPORTER_ENDPOINT`).

Structured JSON logs carry a `correlation_id` — the active span's trace
id — so a log line and the trace it happened inside join on one field.

`GET /metrics` (Prometheus text format): documents by status, jobs by
queue state, queue depth, dead-letter count, extraction latency and
validation-attempt histograms (in-process, reset on restart), review
queue depth, and cost-per-document gauges. `GET /cost?run_id=...` gives
the same cost computation queryable per run. Both are `null`/`NaN` unless
all four `PRICE_*_USD_PER_MTOK` env vars are set — cost is computed from
`extractions.usage` (the token counts already stored per row), never a
separate running total that can drift from them, and the system never
guesses a price.

Implementation: `app/telemetry/` (`tracing.py`, `logctx.py`, `metrics.py`,
`cost.py`), instrumented into `app/worker/main.py`, `app/extract/`, and
`app/ingest/pipeline.py`.

---

## Load numbers

Three different measurements, reported separately because they measure
different things — conflating any of them would understate one or
overstate another:

| | real end-to-end (live API) | stubbed, threads | stubbed, processes |
|---|---|---|---|
| what's real | Postgres, queue, worker, ingest, parse, the actual Anthropic API call | Postgres, queue, worker, ingest, parse — LLM call and outbound HTTP are stubbed | same as threads |
| concurrency model | one worker process, serial | N threads in **one** process, sharing **one** SQLAlchemy connection pool | N **separate OS processes**, each with its **own** connection pool — nothing shared but Postgres |
| what it can tell you | what a user/reviewer actually waits on | queue/worker/Postgres overhead with the network and model subtracted out | whether ADR-003's SKIP LOCKED claim — workers claim concurrently without blocking or double-claiming — actually holds |
| source | [`spike/extraction_run_12.json`](spike/extraction_run_12.json), 30 docs, `claude-sonnet-5` / `adaptive:low` — reduced by [`scripts/latency_report.py`](scripts/latency_report.py) | [`scripts/load_test.py MODE=threads`](scripts/load_test.py), 200 docs, real fixture bytes | [`scripts/load_test.py MODE=processes`](scripts/load_test.py), 200 docs, real fixture bytes |
| extraction latency p50 | **25.6s** | **14.3ms** (N=1) | **13.9ms** (N=1) |
| extraction latency p95 | **46.1s** | **16.9ms** (N=1) | **16.4ms** (N=1) |
| documents/hour | **133.7** (serial) | **41,445** (N=1) → **15,203** (N=4) | **37,034** (N=1) → **59,907** (N=2) → **78,390** (N=4) |

The real number is what a user or a reviewer actually waits on; it is
dominated by the model's own response time (mean 25.9s), not this
system's overhead — the stubbed numbers are three orders of magnitude
below it, meaning this system is not the bottleneck.

**Threads get *slower* as `WORKERS` increases; processes get faster.**
This is the result the two concurrency models were built to separate.
`scripts/load_test.py` at 200 documents, `MODE=threads`
([`spike/load_test_report.json`](spike/load_test_report.json) at N=1,
[`spike/load_test_report_workers4.json`](spike/load_test_report_workers4.json)
at N=4): throughput **drops 2.73×** from N=1 to N=4 (41,445 → 15,203
docs/hour) — N threads sharing one process-wide connection pool
(`app.db.engine.get_engine()`) contend with each other, and that
contention is not what a real deployment looks like. `MODE=processes`
([`spike/load_test_report_processes1.json`](spike/load_test_report_processes1.json),
[`..._processes2.json`](spike/load_test_report_processes2.json),
[`..._processes4.json`](spike/load_test_report_processes4.json)) — N
separate OS processes, each with its own pool, the same shape as N real
`python -m app.worker.main` processes or N Heroku/Render worker dynos —
throughput instead **rises 1.62× at N=2 and 2.12× at N=4** relative to
N=1 (37,034 → 59,907 → 78,390 docs/hour), sublinear but genuinely
positive, unlike threads.

**Does ADR-003's SKIP LOCKED claim hold under real concurrency?** Yes,
measured directly, not assumed. Process mode instruments the claim
itself: in the same transaction as every claim attempt, it checks
whether `queued` rows existed at that moment but the claim still
returned nothing (contention, not just "the queue ran dry"), and it logs
every job id any process's claim returned, then checks the merged list
across all processes for any id appearing twice — what SKIP LOCKED must
never allow.

| N | empty-while-queued attempts | successful claims | duplicate claims | SKIP LOCKED holds |
|---|---|---|---|---|
| 1 | 0 | 400 | none | ✅ |
| 2 | 1 | 400 | none | ✅ |
| 4 | 3 | 400 | none | ✅ |

Zero duplicate claims at every N, across repeated runs. A small,
non-blocking amount of contention appears and grows with N (0 → 1 → 3
empty attempts out of 400 claims) — workers occasionally race for the
same row and one loses cleanly, exactly the behavior ADR-003 §4
describes (`FOR UPDATE SKIP LOCKED` lets the loser move on immediately
rather than block), not evidence against the claim.

**Cost per document** (run 12, `claude-sonnet-5` / `adaptive:low`, 30
documents, `$2.00` / `$10.00` / `$0.20` / `$2.50` per MTok input / output /
cache-read / cache-write): **$1.5154 total, $0.0505 mean per document**
(range $0.020–$0.123 across the corpus, driven mostly by output tokens —
see [What else didn't work](#what-else-didnt-work) for what moving that
number did and didn't achieve). Same computation the running system
exposes on `GET /metrics` (`cost_per_document_usd_mean`) and
`GET /cost?run_id=...`, computed from `extractions.usage` — see
[Observability](#observability) — once `PRICE_*_USD_PER_MTOK` is set.

Reproduce any of these:

```
python -m scripts.latency_report                                   # real, from the frozen run-12 report
DOCUMENTS=200 WORKERS=1 MODE=threads   python -m scripts.load_test  # stubbed, threads
DOCUMENTS=200 WORKERS=4 MODE=processes python -m scripts.load_test  # stubbed, processes; see claim_contention in the report
```

`scripts/load_test.py` refuses to run against any database whose name
doesn't end in `_loadtest` (same discipline as the test suite's `_test`
suffix), so it cannot be pointed at the dev or deployed database by
accident.

---

## Results (M5, dev split, n=12)

Scored per [`eval/M5_PROTOCOL.md`](eval/M5_PROTOCOL.md) §4 against
[`eval/gold_set.json`](eval/gold_set.json), run scored:
[`spike/extraction_run_12.json`](spike/extraction_run_12.json) (schema
v0.8, `adaptive:low`, post-ADR-012). Full numbers, confusion matrices, and
reliability diagrams are in
[`eval/m5_results_dev.md`](eval/m5_results_dev.md). **The locked 18-document
test split has not been run** (see Limitations).

The gold set covers three fields only — `trigger.label`, `mechanism.label`,
`detection_method` — not the other 20 extracted fields (see
Limitations).

### Lead number: the uncontaminated subset (n=5)

`seen_before` (`eval/gold_set.json`, defined in `eval/M5_PROTOCOL.md` §3):
true when the labeler had contact with a document's model output, or a
written discussion of it, before labeling that document. Two
non-overlapping causes produce it across the 30-document gold set,
17 documents total: the 10 M0 documents (`A`–`J`, anchored per ADR-006
§7), and 7 more of the K–AD expansion documents — `P, Q, Y, Z, AA, AC,
AD` — marked `seen_before: true` in `eval/gold_set_labels_K_to_AD.md`
because "their difficulty/model-output discussion occurred before
labeling." **None of the 30 documents, including these 7, were part of
the original taxonomy-derivation corpus** — that was `A`–`J` only
(ADR-008 §2) — being discussed is a distinct, narrower kind of
contamination from being derived from.

Restricted to the 12-document dev split, 7 are `seen_before=true`: the
4 M0-anchored ones that fall in dev (`B, E, F, H`) plus 3 of the other
7 that also fall in dev (`P, AA, AC`). The remaining 5 (`L, N, R, W,
AB`) are genuinely fresh: blind-labeled, and neither M0-anchored nor
previously discussed.

| field | n=5 uncontaminated (L, N, R, W, AB) | n=12 full dev split |
|---|---|---|
| `trigger.label` | 3/5 = 60.0% | 10/12 = 83.3% |
| `mechanism.label` | 4/5 = 80.0% | 11/12 = 91.7% |
| `detection_method` | 3/5 = 60.0% | 9/12 = 75.0% |

n=5 is too small for a confidence interval and every disagreement moves
the number by 20 points; it is reported because it is the closest thing
in this project to an independent accuracy estimate, not because it is
precise. A stricter n=3 subset (`L, R, W` — dropping `N` and `AB` too,
since both were labeled using the ADR-012 provider-boundary rule that was
also written into the schema before this run) gives trigger 2/3 = 66.7%,
mechanism 2/3 = 66.7%, detection_method 2/3 = 66.7% — all three fields
converging near two-thirds on the least-contaminated reading available.

**Read the full-dev-split column with that gap in mind, not as a better
estimate of the same thing.** It is inflated by the same anchoring and
corpus-overlap effects the contamination caveat below describes, and
`mechanism` on the full split (91.7%) exceeds the pre-registered
cross-run reproducibility floor (86.7%) — which M5_PROTOCOL.md §4a
pre-registered as a flag that the gold set may be anchored to the model
rather than independent, not as a good result.

---

## What failed

Both pre-registered success criteria failed. Neither failure is
independent of the other.

### 1. Calibration (ADR-009 §5) — FAIL on all three fields

Criterion: if mean self-reported confidence on incorrect fields is within
0.05 of the mean on correct fields, the signal is not separating
correct from incorrect and is not a valid basis for review routing.

| field | mean confidence, correct | mean confidence, incorrect | gap | result |
|---|---|---|---|---|
| `trigger.label` | 0.7000 (n=10) | 0.7000 (n=2) | 0.0000 | FAIL |
| `mechanism.label` | 0.7500 (n=11) | 0.7500 (n=1) | 0.0000 | FAIL |
| `detection_method` | 0.6444 (n=9) | 0.6167 (n=3) | 0.0278 | FAIL |

ECE was 0.13–0.24 across the three fields. This is not merely
overconfidence (expected and not a failure per ADR-009 §5) — the model
reports essentially the same confidence whether it is right or wrong.
Self-reported confidence, the only routing signal M4 has, does not
predict correctness on this split.

### 2. Review queue (ADR-010 §5) — FAIL

Criteria: review precision ≥ 2.5× the base error rate, and review recall
≥ 80%, both on the uncapped threshold sweep. Base error rate on the dev
split (36 scored fields, 6 wrong) is 16.67%, so the precision bar is
41.67%.

At the pre-registered M4 operating threshold (0.70): 15 fields routed, 3
of them actually wrong. Precision 20.0% (need ≥ 41.67% — FAIL). Recall
50.0% (need ≥ 80% — FAIL). Across the full sweep in
[`eval/m5_results_dev.md`](eval/m5_results_dev.md) §4c, precision never
reaches 24% at any threshold from 0.00 to 1.00, and the only thresholds
that clear 80% recall (0.80 and above) route 25 of 36 fields — most of
the record — for a precision of 24% or worse.

**The queue failure follows directly from the calibration failure.**
Review routing is a threshold cut on self-reported confidence; §4b
already showed that score doesn't separate correct from incorrect
fields. A ranking signal with no separation cannot produce a routing
rule with both good precision and good recall — there is no threshold
that carves out an "actually wrong" region because the score doesn't
locate one. The queue is not a broken implementation of a sound design;
it is a sound implementation of a signal that measured as uninformative.

---

## What else didn't work

**The real cost driver is reasoning tokens, not the schema.** Run 04
(`default` thinking — no `thinking` parameter sent, which is adaptive
thinking on Sonnet 5) produced ~20k tokens of visible JSON against
80,412 billed output tokens total (`spike/extraction_run_04.json`
summary) — roughly **75% of every dollar spent on output was invisible
reasoning tokens**, not the record being returned. No schema change can
touch that, because the schema only shapes the visible 20k. That is the
finding the two schema-trimming attempts below ran into without knowing
it, and it is why the fix that actually worked was changing the thinking
setting, not the schema.

**Two schema-trimming attempts, two net cost losses** — both targeted
the cached schema text or the model's written output, and both lost
money, because a validation retry resends the whole document and
retries dominate per-document cost far more than the schema-text token
count does:

| Attempt | What changed | Intended saving | Measured result |
|---|---|---|---|
| Schema v0.2 ([ADR-005](docs/adr/005-structured-output.md) §4) | Cut every description the model *reads* to one sentence | Shrink the cached system block | Cached block 5,532→5,198 tokens, but mean attempts 1.20→1.30 and cost $0.96→$0.99 (run 03 vs run 02) |
| Schema v0.3 (README run_04) | Cap descriptions the model *writes* at 200 chars | Shrink output tokens | Written text did shrink 24%, but 3 of 4 retries were the new cap itself firing; output tokens rose (76,526→80,412) and cost rose to $0.104/doc |

Both trims moved tokens that cost roughly $0.0001–$0.001 per request
and, by coincidence or by causing the new retries, lost far more than
that back.

**`APP_EXTRACTION_THINKING=adaptive:low` was a success, not a loss**: a
**37% cost cut** (cost ratio 0.627 vs the `default`-thinking baseline,
`spike/thinking_experiment.json`) with **10/10 trigger agreement and
7/10 mechanism agreement** against that same baseline — no accuracy
loss on the corpus's hardest document. This is the project default for
exactly that reason, and it is the one cost experiment here that
actually worked.

**`disabled` thinking is a quality failure, not a cost failure.** It cut
cost further still (cost ratio 0.562, i.e. a 44% cut) but collapsed
agreement with the baseline to 8/10 trigger and 5/10 mechanism —
specifically flipping AWS, the corpus's one deliberately ambiguous
document, into a `null`-trigger / `race_condition`-mechanism reading
that recreates the exact two-field confusion [ADR-001](docs/adr/001-trigger-taxonomy.md)
exists to prevent. `adaptive:low` was kept as the project default over
`disabled` for this reason: it gets most of `disabled`'s cost saving
without `disabled`'s quality collapse.

**Taxonomy overfitting: 23% vs a pre-registered 20% ceiling.** ADR-006's
mechanism enum, derived from the original 10 documents, put 7 of the
30-document corpus (23%) on `other` — all seven in the 20 newly added,
held-out documents (35% of that subset). [ADR-008](docs/adr/008-taxonomy-revision.md)
restored a general `software_defect` class and closed `trigger` into an
enum in response; the revised taxonomy is what schema v0.6 onward and
the results above are scored against. The lesson recorded in ADR-008 §5
is procedural: the next corpus expansion freezes the enums *before*
looking at new documents and scores the held-out result before revising
anything, because passing on the derivation corpus had measured internal
fit, not generalization.

---

## Limitations

- **The gold set covers 3 of 23 record fields.** `trigger.label`,
  `mechanism.label`, and `detection_method` are the only fields M5
  scores, because they are categorical (exact-match scoring needs no LLM
  judge), they are what every taxonomy ADR is about, and they are the
  only fields the M0 blind labels cover. Temporal fields were deliberately
  excluded — hand-labeling a precise timestamp the source doesn't support
  would invent precision that isn't there (M5_PROTOCOL.md §1). The other
  20 fields (`affected`, `blast_radius`, `mitigations`/`remediations`,
  time anchors, …) have no measured accuracy anywhere in this project.
- **7 of the 20 blind-labeled documents were labeled under a boundary
  rule written into the schema shortly before this run.** ADR-012 gives
  an explicit trigger-classification rule for provider-caused incidents
  and names the 7 documents it changed the label for (`T, U, Y, N, O, X,
  AB`). Both the gold labels and the extractor were given the same rule,
  which is the right thing to do for a fair comparison, but it means
  those 7 labels and that schema version were finalized together rather
  than independently — 2 of them (`N`, `AB`) fall in the dev split scored
  above. **This is a different 7-document set from the `seen_before=true`
  7 described above** (`P, Q, Y, Z, AA, AC, AD`) — the two lists overlap
  in exactly one document, `Y` (Mozilla/Firefox), which is both
  ADR-012-affected and `seen_before=true` for the unrelated reason given
  above. Conflating the two sets was an earlier drafting error in this
  README; they are tracked separately in the source data
  (`eval/gold_set.json`'s `seen_before` field vs. ADR-012's own text) and
  should be read separately here too.
- **10 of 30 documents are anchored, not blind.** The M0 documents
  (`A`–`J`) were blind-labeled before any model output existed for
  `detection_method`, but their `trigger`/`mechanism` labels are a
  post-hoc mapping onto enums that were themselves partly derived from
  those same 10 documents' model output (ADR-006 §7, `eval/m0_enum_mapping.md`).
  Agreement on anchored documents is not independent evidence of
  extraction quality the way the K–AD blind labels are.
- **The 18-document locked test split has not been run.**
  M5_PROTOCOL.md §2 commits to running it exactly once, after all dev-split
  work is settled — it has not been run at all yet, so there is no
  test-split number anywhere in this document, and the dev/test overfitting
  gap check (§4a) is unevaluated.
- **An invented-key retry failure mode recurs and is unexplained.**
  Across runs 08–10, some fraction of validation retries are the model
  inventing an extra JSON key on the `trigger`/`mechanism` objects rather
  than a genuine schema violation. The README's run notes track a
  correlation with the length of the enum-definition block near those
  fields (run 09) that did not hold when the block grew further in run 10
  — "not a simple function of enum-block length," per the run 10 write-up,
  and not investigated further.
- **n is small everywhere.** 12 dev documents, an uncontaminated subset
  of 5 (or 3, on the stricter reading above), 30 documents total in the
  full corpus. Every number in this README moves several percentage
  points on a single document changing. No result here should be read as
  a stable rate.
- **The thread-mode load test is measurably slower per document at higher
  `WORKERS`, not a "barely moves" wash — and process mode now exists to
  show the opposite is true for real concurrency.** `scripts/load_test.py
  MODE=threads` at 200 documents: N=1 measured 41,445 docs/hour, N=4
  measured 15,203 — about 2.73× slower, reproducible across repeated
  runs. That mode's N threads share one process-wide SQLAlchemy
  connection pool (`app.db.engine.get_engine()`), not separate processes,
  so it was never evidence about how N real worker processes/dynos would
  scale — only about thread contention on one shared pool. `MODE=processes`,
  added specifically to close that gap, measured the opposite: N=1
  37,034 docs/hour → N=2 59,907 → N=4 78,390, a real 2.12× improvement at
  N=4 (see [Load numbers](#load-numbers)). What remains untested: N above
  4, a real (non-stubbed) model call competing for the same Postgres
  connections, and multi-machine deployment — this repo has only run
  process mode on one machine, up to 4 processes, against a stub.
- **Two open [DERIVE] decisions from the brief have no ADR.** DERIVE-04
  (whether multi-tenancy is real or theater here) was never written up —
  the honest answer given the single-user, all-public-source corpus is
  probably "not needed," but that has not been decided in writing. The
  M6 Basic Auth gate on `/review/*` (`app/api/auth.py`) is a lock on the
  door, not an answer to DERIVE-04: one shared credential pair, no
  accounts, no per-user data isolation. DERIVE-03 (partial-extraction
  semantics) also remains unimplemented: `extractions.status` is
  `complete | failed` only, and `partial` is not a state the system
  produces.

---

## ADR index

- [001 — Trigger taxonomy structure](docs/adr/001-trigger-taxonomy.md): split one trigger field into nullable `trigger` (initiating change/event) and required `mechanism` (what failed).
- [002 — Detection method](docs/adr/002-detection-method.md): merge `monitoring`/`automated`, keep single-valued, add `operator` self-detection, add `ambiguous` vs `unknown`.
- [003 — Queue](docs/adr/003-queue.md): Postgres `SELECT … FOR UPDATE SKIP LOCKED`, no Redis/Celery, for transactional consistency at this workload's scale.
- [005 — Structured output](docs/adr/005-structured-output.md): the API's grammar-constrained JSON schema is too large for this record; validate client-side and retry instead.
- [006 — Taxonomy classes](docs/adr/006-taxonomy-classes.md): close `mechanism` into an enum (label vocabulary wasn't reproducible as free text); keep `trigger` open pending further evidence.
- [007 — Document identity](docs/adr/007-document-identity.md): a document is its extracted text (`text_hash`), not its bytes (`content_hash`) — re-served pages with changing nonces are not new documents.
- [008 — Taxonomy revision](docs/adr/008-taxonomy-revision.md): the mechanism enum was overfit to its 10-document derivation corpus (23% `other` on expansion); restore `software_defect` and close `trigger` too.
- [009 — Confidence source](docs/adr/009-confidence-source.md): keep model self-report as the M4 routing signal (cheap, and separates stable from unstable extractions pre-M5); calibration is unvalidated until measured — see Results above.
- [010 — Review queue routing](docs/adr/010-review-queue-routing.md): per-field review unit, rank by ascending confidence, provisional 0.70 floor, pre-registers the precision/recall criteria that failed above.
- [011 — Review evidence](docs/adr/011-review-evidence.md): show reviewers candidate passages from fixed keyword cues, never the model's own cited quote, to keep corrections an independent check rather than persuasion.
- [012 — External-provider trigger boundary](docs/adr/012-external-provider-boundary.md): a rule distinguishing documented provider-side failures from merely suspected ones, applied to both the gold labels and the schema before this run's scoring.

---

## How to run it

```
cp .env.example .env   # set ANTHROPIC_API_KEY; provider and model are preset
docker compose up
```

This runs cold on a clean machine: Postgres starts, a one-shot `migrate`
service applies the Alembic migrations, then the API and worker start.
Without a provider, model, or key, ingestion still works and each
extract job dead-letters with a recorded "not configured" error.

Register a source and follow its job:

```
curl -X POST http://localhost:8000/sources \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/some-postmortem"}'

curl http://localhost:8000/jobs/<job_id>
```

Review queue: `http://localhost:8000/ui/review`. Reviewed corrections:
`GET /review/corrections`.

Observability: `GET /metrics` (Prometheus text), `GET /cost` (optionally
`?run_id=...`). Traces print to the `api`/`worker` container logs by
default (`APP_OTEL_EXPORTER=console`); set `APP_OTEL_EXPORTER=otlp` and
`APP_OTEL_EXPORTER_ENDPOINT` to send them to a collector instead. Cost is
`null` until all four `PRICE_*_USD_PER_MTOK` env vars are set.

Reproduce the M5 numbers above:

```
python -m eval.scripts.score_m5_dev
```

against `spike/extraction_run_12.json` and `eval/gold_set.json` — see
[`eval/m5_results_dev.md`](eval/m5_results_dev.md) for the full output
this README's Results section summarizes.

Tests, without a local Python 3.12 (this project's own dev environment):

```
docker build -f Dockerfile.dev -t incident-intel-dev .
docker run --rm --network host -v "$PWD:/srv" \
  incident-intel-dev sh -c "ruff check . && mypy app && pytest -q -p no:randomly"
```

Tests run against real Postgres, never SQLite, in their own `_test`
database (see `tests/__init__.py`); the compose stack can stay up while
they run. `-p no:randomly` is required — `pytest-randomly`'s reordering
races the DB tests here.

---

## Deployment

Two platform manifests, both building the same [`Dockerfile`](Dockerfile)
— **only one is meant to be live at a time**; the Status section at the
top of this README says which. Both run the same seed step, the same
migrations, and gate the review UI with the same Basic Auth mechanism,
so the app-level behavior is identical regardless of which one is
actually deployed.

| | [`render.yaml`](render.yaml) | [`heroku.yml`](heroku.yml) |
|---|---|---|
| web process | `api` web service, public URL | `web` dyno, public URL |
| worker process | `worker` background service (paid — Render has no free tier for workers, `0.5c-512mb`) | `worker` dyno (paid — Heroku has no free dyno tier at all since Nov 2022) |
| Postgres | managed database resource, `fromDatabase` | Heroku Postgres add-on, `DATABASE_URL` config var |
| migrate + seed | `preDeployCommand` on the `api` service | `release` phase, runs before new dynos start |
| how connection string reaches the app | `APP_DATABASE_URL`, already the right scheme | bare `DATABASE_URL`, `postgres://` scheme — normalized to `postgresql+psycopg://` by `app/settings.py` (see below) |
| launch | Render dashboard → New → Blueprint | `heroku apps:create` + `git push heroku master` (this repo's default branch), or Container Registry |

**The `DATABASE_URL` scheme fix**: Heroku Postgres only ever sets a bare
`DATABASE_URL` (never `APP_-prefixed`), with a `postgres://` scheme that
SQLAlchemy's psycopg 3 dialect rejects outright. `app.settings.Settings.
database_url` reads `APP_DATABASE_URL` first, falls back to bare
`DATABASE_URL` if that's unset (`pydantic.AliasChoices` — Render's
`fromDatabase` targets `APP_DATABASE_URL` directly, so it's unaffected),
and rewrites `postgres://`/`postgresql://` to `postgresql+psycopg://`
either way. One field handles both platforms; neither manifest needs to
alias the other's variable name.

**Seeding** ([`scripts/seed_corpus.py`](scripts/seed_corpus.py)): runs
after migrations, on every deploy, on both platforms. It ingests the
30-document corpus for real (fetch → parse → persist — 10 documents from
local fixtures under `spike/raw/`, no network; the other 20 fetched once,
for real, since only 10 were ever saved locally) but **replays extraction
from the frozen [`spike/extraction_run_12.json`](spike/extraction_run_12.json)
report** rather than calling the model — no Anthropic API cost at seed
time, byte-for-byte the same record on every redeploy. It also runs the
same post-extraction routing pass the worker runs after a real extraction
(ADR-010), so the review queue has real content on first boot. Idempotent
and safe to run repeatedly: re-seeding re-fetches nothing new and inserts
no duplicate rows, and — this was a real bug caught while building it —
it enqueues **no queue jobs**, specifically because a queued `extract` job
would eventually be claimed by the live worker and would call the real
API, spending money on data this script already wrote for free (see the
script's own docstring for how that was found and fixed).

**The live Heroku instance runs the `web` dyno only** — the `worker` dyno
is defined in `heroku.yml` but deliberately left scaled to 0
(`heroku ps:scale worker=0`, Heroku's default for any process type until
you scale it up). The seeded 30 documents don't need it, since they never
go through the queue; a worker is only needed if you register a genuinely
new source (`POST /sources`) and want it actually processed. Scale it on
with `heroku ps:scale worker=1 -a <app>` — it's a paid dyno, so check
Heroku's billing page first if that matters to you.

**Auth** ([`app/api/auth.py`](app/api/auth.py)): every `/review/*` and
`/ui/review/*` endpoint — read and write alike — requires HTTP Basic Auth
on the deployed instance, `APP_REVIEW_BASIC_AUTH_USER` / `_PASS`. On
Render these are Blueprint secrets (`sync: false`, set once in the
dashboard); on Heroku they're ordinary Config Vars (`heroku config:set
APP_REVIEW_BASIC_AUTH_USER=... APP_REVIEW_BASIC_AUTH_PASS=...`) — same
env vars either way, `app/settings.py` doesn't know or care which
platform set them. The review surface is gated as a whole because it is
the one place this system writes gold-set data (ADR-010 §6) on a human's
say-so; nothing else needs a login (`/sources`, `/jobs/{id}`, `/metrics`,
`/cost`). Locally, both env vars are unset by default, so `docker compose
up` and the test suite need no credentials — see `app/api/auth.py`'s
docstring for the fail-closed rule if only one of the two is ever set by
mistake.

**To actually deploy**: this repo ships everything short of the
platform-side account action, which needs credentials this automation
has no access to either way.

Render:

1. Push this repo to GitHub (or GitLab).
2. In the Render dashboard: **New → Blueprint**, point it at the repo.
   Render reads `render.yaml` and proposes the three services in the
   table above.
3. Before the first deploy, set the `sync: false` secrets Render will
   prompt for: `APP_REVIEW_BASIC_AUTH_USER`, `APP_REVIEW_BASIC_AUTH_PASS`
   (pick any demo credentials), `ANTHROPIC_API_KEY` (only needed if you
   want the deployed worker to extract *new* registrations for real — the
   seeded 30 documents never call it), and, optionally, the four
   `PRICE_*_USD_PER_MTOK` vars if you want `/metrics` and `/cost` to
   report real dollar figures instead of null.
4. Deploy. The `api` service's `preDeployCommand` migrates and seeds
   automatically; watch its logs for `done: 30 document(s) ingested, ...`.
5. Put the resulting `https://<service>.onrender.com` URL in the Status
   section above and note it's on Render.

Heroku (steps actually run to bring up the live instance above):

1. `heroku apps:create <name>` (app names are globally unique across all
   Heroku users, so pick your own), then `heroku stack:set container -a
   <name>` so `heroku.yml` is honored instead of buildpack auto-detection.
2. `heroku addons:create heroku-postgresql:essential-0 -a <name>` (the
   cheapest current plan, ~$5/month — Heroku Postgres has had no
   permanent free tier since Nov 2022) sets `DATABASE_URL` automatically;
   nothing to configure by hand for the database.
3. `heroku config:set -a <name> APP_REVIEW_BASIC_AUTH_USER=...
   APP_REVIEW_BASIC_AUTH_PASS=...` (pick your own demo credentials).
   `ANTHROPIC_API_KEY` only if you want the worker to extract *new*
   registrations for real — the seeded 30 documents never call it, so
   it's fine to skip or leave a placeholder for now.
4. `git push heroku master` — push whatever your local default branch is
   named (this repo's is `master`, not `main`; Heroku only deploys the
   branch you actually push, so match it to your own checkout). The build
   uses `heroku.yml`; the `release` phase migrates and seeds
   automatically — watch `heroku logs -a <name> --source app --dyno
   release` for `done: 30 document(s) ingested, ...`.
5. The `web` dyno starts automatically; `worker` stays at 0 instances
   until you explicitly `heroku ps:scale worker=1 -a <name>` (a real,
   ongoing paid cost — the live instance above deliberately leaves it at
   0, since the seeded demo doesn't need it).

One gotcha hit deploying this for real: `heroku.yml`'s `release.command`
and `run.worker.command` must be YAML lists (`command:` then a `-` line),
not bare strings — Heroku's manifest parser rejects the plain-string form
outright, at push time, with a message naming exactly which two fields
are wrong. Fixed in this repo's `heroku.yml`; worth knowing before you
edit it further.
