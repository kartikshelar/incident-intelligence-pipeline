# Incident Intelligence Pipeline

Ingests public engineering postmortems, extracts a structured incident
record from each, routes low-confidence fields to human review, and
measures its own extraction quality against a frozen hand-labeled gold set.

See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the full contract, milestone
plan, and open [DERIVE] decisions. Architectural decisions are recorded in
[`docs/adr/`](docs/adr/).

## Status: M4 — Confidence & review queue

A registered source URL is fetched and normalized (M2), then a second job
sends the text to Claude and stores one validated incident record per
document in `extractions` (M3). Output is validated against the schema;
invalid output is retried with the validation error fed back; every
failure is a row, not a log line. Every field of every complete
extraction then gets a review state; fields whose self-reported
confidence is below a configured floor are ranked lowest-first and routed
to a one-field-at-a-time review UI, and a reviewer's accept or correction
is written back next to the model's value as gold-set input (M4, see
[Review](#review)).

```
cp .env.example .env   # set ANTHROPIC_API_KEY; provider and model are preset
docker compose up
```

runs cold on a clean machine: Postgres starts, a one-shot `migrate`
service applies Alembic migrations, then the API and worker start. Without
a provider, model, or key, ingestion still works and each extract job
dead-letters with a recorded "not configured" error.

## Configuration

Provider, model, thinking setting, and API key are configuration, never
constants in app code (`app/settings.py`; `.env` is read if present and
is git-ignored):

| Variable | Meaning |
|---|---|
| `APP_LLM_PROVIDER` | which `app/extract/llm.py` implementation to use (`anthropic`) |
| `APP_EXTRACTION_MODEL` | model string passed to the provider verbatim; the project default, `claude-sonnet-5`, lives in `.env.example` and `docker-compose.yml`, not in code |
| `APP_EXTRACTION_THINKING` | thinking/effort, interpreted by the provider and stored verbatim. For `anthropic`: `default` (send nothing; the API's default, which on Sonnet 5 is adaptive thinking), `adaptive`, `disabled`, each optionally `:<low\|medium\|high\|xhigh\|max>` for `output_config.effort`, e.g. `adaptive:low`. The project default, `adaptive:low` since 2026-09-16, lives in `.env.example` and `docker-compose.yml`; why it is not `default` or `disabled` is in the `app/extract/schema.py` changelog and the thinking experiment below |
| `ANTHROPIC_API_KEY` | the provider's key, under the SDK's own name |
| `APP_EXTRACTION_MAX_TOKENS`, `APP_EXTRACTION_MAX_ATTEMPTS` | 16000 / 3 |
| `APP_REVIEW_CONFIDENCE_FLOOR` | M4 routing: fields whose self-reported confidence is below this are eligible for review. `0.70` is [ADR-010](docs/adr/adr-010-review-queue-routing.md) §3's provisional operating point, kept as configuration so M5's threshold sweep is a config change, not a code change. Not tuned against any data yet |
| `APP_REVIEW_BUDGET` | optional cap on fields awaiting a reviewer at once, applied after ranking; unset = uncapped, which is the mode M5 evaluates routing in |

`provider`, `model` and `thinking` are stored on every `extractions` row
and are part of its idempotency key, so the same document re-run at a
different thinking setting is a new extraction, not a no-op.
`build_llm_client` is the only place a provider is chosen; a second
provider is a new entry in that registry and no change to the worker or
pipeline.

Register a source:

```
curl -X POST http://localhost:8000/sources \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/some-postmortem"}'
```

Response includes `job_id`; check its status:

```
curl http://localhost:8000/jobs/<job_id>
```

`queued` -> `running` -> `succeeded`, with a new row in `documents`
(title, normalized text, format, content_hash, text_hash, fetched_at). A 404/403/etc.
sends the job straight to `dead_letter` (no retry wasted on a URL that
doesn't exist); a timeout or 5xx requeues it. An extraction that produces
empty text is treated as a **hard failure** (`dead_letter`, no `documents`
row written) rather than a partial record — see
[`spike/FINDINGS.md`](spike/FINDINGS.md) §2 item 1.

On success the ingest job enqueues an `extract` job (`jobs.kind`) for the
document in the same transaction. That job's success is a `complete` row
in `extractions`.

**Document identity** ([ADR-007](docs/adr/007-document-identity.md)): a
document is its extracted text, not its bytes. `content_hash` (sha256 of
the raw bytes) is the storage key; `text_hash` (sha256 of the normalized
text) is the identity and the ingest idempotency key. Re-ingesting a
source whose bytes changed but whose text did not — the AWS and Google
Cloud status pages re-serve with new script nonces on every fetch —
updates the existing row's provenance (final URL, `fetched_at`,
`content_hash`, `raw_bytes`) and writes no new document; different text
is a new document. Migration 0005 backfills `text_hash` and merges
documents that already shared a text.

## Extraction

`app/extract/` — see its `__init__.py` for the module map.

**Schema v0.6** (`app/extract/schema.py`) is PROJECT_BRIEF §6's draft after
the spike's corrections, each cited in the module docstring:

- `trigger` (nullable: initiating change/event) + `mechanism` (required,
  single-valued: what failed), per [ADR-001](docs/adr/001-trigger-taxonomy.md).
  `change_induced` is gone — it is `trigger is not null`.
- `mechanism.label` is a closed enum of sixteen classes including `other`,
  per [ADR-006](docs/adr/006-taxonomy-classes.md) §3 and
  [ADR-008](docs/adr/008-taxonomy-revision.md) §3 (`software_defect`
  restored, plus `security_compromise`, `hardware_data_loss`,
  `consistency_anomaly`); each class's one-line definition is in
  `app/extract/taxonomy.py` and is sent to the model as the field's
  description. `trigger.label` is also a closed enum as of ADR-008 §3 —
  ten classes derived by clustering the vocabulary observed across the
  30-document corpus, excluding `traffic_spike` / `operational_delay`
  (anomalous conditions, ADR-006 §5) and `race_condition` (a mechanism,
  not an initiating event). A label outside either list fails validation
  and is retried with the class list in the error. Both taxonomies'
  wire descriptions are built by one shared function
  (`taxonomy._enum_description`). v0.5 and v0.6 each changed the
  validation contract, so each is a new version; no migration, since
  `record` is JSONB and earlier rows keep their labels under their own
  `schema_version`.
- `detection_method` ∈ monitoring | customer_report | internal_manual |
  operator | ambiguous | unknown, per [ADR-002](docs/adr/002-detection-method.md).
- five typed time anchors with precision + original timezone string
  instead of `occurred_at`; durations are *derived* from anchors
  (`app/extract/derive.py`) and name the anchor pair they used, never
  extracted (FINDINGS §4.1, §4.2).
- `affected` with `list_is_complete` / `all_services`; `publisher_org` /
  `affected_org` / `vendor_org`; `title_source`; `mitigations` vs
  `remediations` with status; `contributing_factors[].source_section`
  (FINDINGS §4.5–§4.9).
- the free text the model *writes* — `trigger.description`,
  `mechanism.description`, each `contributing_factors[].text` — is one
  sentence of at most 400 characters (v0.3 introduced the rule at 200;
  v0.4 doubled it after run 04 measured the 200 cap causing 3 of its 4
  retries): a field constraint plus a sentence-count validator, so an
  over-long value fails validation and is retried with the error fed
  back. `quote` fields are provenance and are never constrained.

**Model call** (`app/extract/llm.py`): Anthropic Messages API. The JSON
schema is sent as text in the cached system block and the output is
validated client-side by the Pydantic models. It was designed to use the
API's grammar-constrained structured output (`output_config.format =
json_schema`), but the first real run
([`spike/extraction_run_01.json`](spike/extraction_run_01.json)) had every
request rejected with `400 The compiled grammar is too large`. Probing
showed the limit is roughly "the record without its time anchors and
without confidence"; flattening the anchors to plain fields does not
help. The tradeoff is recorded in
[ADR-005](docs/adr/005-structured-output.md). Schema conformance
therefore rests on the retry loop below. Schema v0.2 cut the descriptions
the model reads to one sentence to shrink that cached block; run 03
measured it as a net loss (mean attempts 1.20 → 1.30, cost $0.96 → $0.99,
because a retry resends the document and cache reads are the cheapest
tokens there are), and v0.3 reverted it — see ADR-005 §4. Provider and
model come from configuration (see above).

**Retry loop** (`app/extract/extractor.py`): on a validation failure the
model is shown its own output and the validator's errors and asked for the
corrected object, up to `APP_EXTRACTION_MAX_ATTEMPTS` (default 3). Every
attempt — raw text, validation error, token usage — is kept in
`extractions.attempt_log`.

**Failures** (`app/extract/errors.py`): 429 / 5xx / network → transient
(job requeued); 4xx / refusal / truncated output / validation exhausted →
permanent (dead letter). Either way an `extractions` row with
`status='failed'`, the error, and the attempt log is committed in the same
transaction as the job's state change. Failed rows never block a later
success; one `complete` row per (document, schema version, provider,
model, thinking) is enforced by a partial unique index, which is what makes
the extract job idempotent under at-least-once delivery.

**Confidence**: `FieldConfidence` is one self-reported number per
top-level field, stored in `extractions.per_field_confidence` with
`confidence_source='self_report'`. [ADR-009](docs/adr/adr-009-confidence-source.md)
(DERIVE-05) keeps self-report as the routing signal for M4 on the evidence
that it was lower on the fields that flipped between runs 10 and 11 (a
0.11–0.12 gap on the unstable fields, small sample); whether it is
*calibrated* is measured in M5, not assumed here. M4 uses it only to
rank.

## Review

M4, per [ADR-010](docs/adr/adr-010-review-queue-routing.md) (DERIVE-06).
What the review page shows the reviewer, and why it shows candidate
passages rather than the model's evidence, is
[ADR-011](docs/adr/011-review-evidence.md). `app/review/` — see its
`__init__.py` for the module map.

**Unit of review is the field.** Every complete extraction gets one
`field_reviews` row per top-level record field (23 of them), written in
the same transaction as the extraction (migration 0008 backfilled the
rows for earlier extractions). Each row carries the field's review state —
`unreviewed` → `routed` → `reviewed` — its self-reported confidence, and a
copy of the model's value. A reviewed field is never routed again;
re-extracting the document is a new extraction with its own rows.

**Routing** (`app/review/routing.py`): eligible = unreviewed fields with
confidence strictly below `APP_REVIEW_CONFIDENCE_FLOOR`; rank them by
ascending confidence across all extractions; then, only if
`APP_REVIEW_BUDGET` is set, route as many of the lowest as fit under the
cap on fields awaiting review. The budget decides how many, never which —
it is an operational constraint applied after the policy, and M5 scores
the policy uncapped. The worker runs a pass after every completed
extraction; `POST /review/route` runs one on demand (after changing the
floor, or right after the migration backfill, whose rows start
unrouted). A pass is idempotent. The floor is deliberately not a code
constant and has not been tuned against any data: 0.70 is the ADR's
provisional operating point between the mean confidence of stable (≈0.75)
and unstable (≈0.64) extractions in runs 10/11, and M5 replaces it from
the gold-set sweep.

**Review UI** (`/ui/review`, `app/api/review_ui.py`): server-rendered,
one field per page — the field, its definition, its value, its
confidence, three to five candidate passages from the source, and the
full normalized text below. Three actions, one field each: **Accept**,
**Correct** (a form derived from the field's own Pydantic type, see
below; validated against that type before anything is written), **Skip**
(back to the queue, behind fields not yet passed over). No bulk actions.
The reviewer's name is a text box remembered in a cookie: provenance,
not authentication.

- *Definition* (`app/review/definitions.py`): read from the schema itself
  — the field description and object docstring the model receives in the
  wire schema, plus the blocks of the system prompt that name the field —
  so it cannot drift from what the model was told. The sentences that say
  what the field is *not* ("an anomalous condition is never the trigger";
  "do not infer factors from the remediation list") are listed
  separately, and each field is shown next to the siblings it is confused
  with: `impact_start` (when users were first affected) against
  `change_at` (when the triggering change was applied), `mitigations`
  against `remediations`. Five fields have no description in the schema
  (`title`, `title_source`, `affected_org_kind`; `affected` and
  `blast_radius` are described only through their parts) and the page
  says so rather than inventing one.
- *Candidate passages* (`app/review/candidates.py`): the source is split
  into sentence windows of at most 400 characters and scored by fixed,
  field-specific keyword cues (deploy/config/rollout for `trigger`,
  alert/paged/noticed/reported for `detection_method`, clock times plus
  rollback/restored for `mitigated_at`, …); the top five with at least one
  cue, or the document's lead when fewer than three score, shown in
  document order with the cues that chose them. Deterministic and
  keyword-only — no embeddings, no retrieval model (PROJECT_BRIEF §3).
  **The selection never sees the extraction.** The quote the model cites
  is not located, highlighted, ranked, or marked among the candidates:
  corrections are gold-set input for M5, and a reviewer shown the model's
  justification is not an independent judge — biased corrections would
  silently corrupt the ground truth. The only thing the page says about a
  cited quote is when it cannot be found in the source at all.
- *Search in the source*: the full-text panel has a client-side search
  (literal, case-insensitive) with a match count, Enter / Shift+Enter to
  step through matches, and a "find in source" link on each candidate
  passage that scrolls to its exact span. Inline script, no build step.
- *Typed corrections* (`app/review/forms.py`): the correction input is
  built from the field's JSON schema — a select for every closed
  vocabulary, a labelled input per part of an object (a time anchor is
  `at`, `precision`, `timezone`, `quote`, `source_section`, each with the
  schema's own hint), one line per item for string lists, add/remove rows
  for lists of objects, and an explicit null toggle for nullable values —
  pre-filled with the extracted value. A reviewer never types JSON. The
  submitted inputs are parsed back into the field's value and validated
  against its Pydantic type before anything is written; a rejected
  correction re-renders with the error and the reviewer's input intact.

**Write-back** (`app/review/fields.py`): a decision marks the field
`reviewed` with reviewer and timestamp and removes it from the queue. A
correction is stored in `corrected_value` next to `model_value`; the
model's answer is never overwritten, in `field_reviews` or in
`extractions.record`. A "correction" equal to the model's value is refused
as an accept. Reviewed fields are the gold-set input:

```
GET /review/corrections                    # decision=corrected (default) | accepted | all
```

returns each decision with `text_hash` (the document's identity under
ADR-007), the extraction's schema version / provider / model / thinking /
run, the field, the confidence it was routed at, both values, and who
decided when. `app.review.queries.list_reviewed` is the same query in
Python for M5.

**API** (`app/api/review.py`, Pydantic contracts like the M1 handlers):

```
GET  /review/queue                  routed fields, lowest confidence first
GET  /review/fields/{id}            one field: definition, value, confidence, candidate passages, cited-quote locations, source text
POST /review/fields/{id}/decision   {"action": "accept"|"correct"|"skip", "reviewer", "corrected_value", "note"}
POST /review/route                  routing pass at the configured floor/budget
GET  /review/corrections            reviewed fields as gold-set input
```

Decisions on a reviewed field return 409; an invalid correction 422 and
changes nothing. Review precision and recall — is the queue routing the
fields that are actually wrong? — are M5's metrics and are not computed
here.

### First real runs (2026-09-15, anthropic / claude-sonnet-5)

`scripts/extraction_run.py` registers the 10 spike documents through the
API, waits for the jobs, and writes a per-document report (record,
confidence, derived durations, validation attempts, tokens, cost, error).

| Run | Succeeded | Dead-lettered | Mean validation attempts | Total cost |
|---|---|---|---|---|
| [`run_01`](spike/extraction_run_01.json) | 0/10 | 10/10 | 0 | $0 (400 before any tokens) |
| [`run_02`](spike/extraction_run_02.json) | 10/10 | 0/10 | 1.20 | $0.96 ($0.06–$0.13 per document) |
| [`run_03`](spike/extraction_run_03.json) (schema v0.2) | 10/10 | 0/10 | 1.30 | $0.99 ($0.07–$0.15 per document) |
| [`run_04`](spike/extraction_run_04.json) (schema v0.3) | 10/10 | 0/10 | 1.40 | $1.04 ($0.07–$0.14 per document) |
| [`run_05`](spike/extraction_run_05.json) (v0.4, thinking `adaptive:low`) | 10/10 | 0/10 | 1.40 | $0.63 ($0.03–$0.11 per document) |
| [`run_06`](spike/extraction_run_06.json) (v0.4, thinking `disabled`) | 10/10 | 0/10 | 1.10 | $0.57 ($0.04–$0.13 per document) |
| [`run_07`](spike/extraction_run_07.json) (v0.4, thinking `default`) | 10/10 | 0/10 | 1.40 | $1.01 ($0.07–$0.15 per document) |
| [`run_08`](spike/extraction_run_08.json) (v0.5, thinking `adaptive:low`) | 10/10 | 0/10 | 1.50 | $0.65 ($0.04–$0.10 per document) |
| [`run_09`](spike/extraction_run_09.json) (v0.5, `adaptive:low`, corpus of 30: 20 new + 10 reused from run 08) | 30/30 | 0/30 | 1.45 (20 new) | $1.06 ($0.02–$0.12 per new document) |
| [`run_10`](spike/extraction_run_10.json) (v0.6, `adaptive:low`, ADR-008 taxonomy, all 30 re-extracted) | 30/30 | 0/30 | 1.13 | $1.28 ($0.02–$0.10 per document) |

| Run | Uncached input | Cache read | Output | Written-description chars | Mean attempts | Cost / document |
|---|---|---|---|---|---|---|
| run_02 (v0.1) | 88,570 | 60,852 | 75,927 | 11,644 | 1.20 | $0.096 |
| run_03 (v0.2) | 98,593 | 62,376 | 76,526 | 11,442 | 1.30 | $0.099 |
| run_04 (v0.3) | 106,126 | 72,293 | 80,412 | 8,713 | 1.40 | $0.104 |
| run_05 (v0.4, `adaptive:low`) | 118,782 | 72,293 | 36,805 | 8,905 | 1.40 | $0.063 |
| run_06 (v0.4, `disabled`) | 87,899 | 61,171 | 36,629 | 11,342 | 1.10 | $0.057 |
| run_07 (v0.4, `default`) | 106,797 | 72,293 | 76,938 | 11,324 | 1.40 | $0.101 |
| run_08 (v0.5, `adaptive:low`) | 115,835 | 90,790 | 38,849 | 8,421 | 1.50 | $0.065 |
| run_09 (v0.5, `adaptive:low`, 20 new documents) | 308,579 | 272,370 | 100,876 | 14,483 | 1.45 | $0.053 |
| run_10 (v0.6, `adaptive:low`, all 30 documents) | 218,236 | 247,071 | 77,505 | n/a | 1.13 | $0.043 |

Run 01 is the grammar-limit failure described above. Run 02, after the
schema moved into the system block, produced a schema-valid record for
every document; the two second attempts were both an invented extra key
(`detected_at_unused`, `publisher_org_confidence`) that the fed-back
validation error corrected. The prompt was not changed between runs or
in response to the output. Prompt caching worked: after the first
document every request read ~5.5k tokens (system prompt + schema) from
cache.

Run 03 is the same corpus under schema v0.2 (one-sentence descriptions;
no other change). The cached block shrank from 5,532 to 5,198 tokens;
total cost is within noise of run 02 because per-document cost is
dominated by whether a validation retry happens (a retry resends the
document). Its three retries were an empty-string key in `record`
(twice) and `"Q1"` in an ISO date field. Two sources (AWS, Google Cloud)
came back with a different content hash but byte-identical text length,
so they were re-ingested as new documents — the DERIVE-07 case
(re-ingest semantics) showing up on the second day. Roblox again derives
a negative `time_to_detect` (detection 2h58m before impact), now
recorded as a signed value by design.

Run 04 is schema v0.3: the v0.2 read-side trim reverted, and the
descriptions the model *writes* capped at one sentence / 200
characters. **Second measured negative result.** The written
descriptions did shrink (11,442 → 8,713 characters, −24%; the compact
record −6%), but three of the four retries were the new cap itself
firing on `mechanism.description` (the fourth was an invented extra key
again), and a retry resends the document. Output tokens went *up*
(76,526 → 80,412) and cost per document rose to $0.104. The written
fields were never the lever: across all three runs the model's visible
JSON is ~70k characters (roughly 20k tokens) against 76–80k billed
output tokens, because the adapter sent no `thinking` parameter and
Sonnet 5 then runs adaptive thinking, billed as output. That setting is
now configuration (`APP_EXTRACTION_THINKING`, stored on the row); whether
to lower effort or disable thinking is a quality question, measured on
the same 10 documents below and left for M5's eval to decide, not a
trim. Under ADR-007, AWS and Google Cloud
came back with new nonces a third time and were matched on `text_hash`:
provenance updated, no new documents, 10 rows in `documents` (down from
12 after migration 0005 merged the run-03 duplicates).

**Thinking experiment (runs 05, 06, 07).** The same 10 documents and the
same prompt at schema v0.4: `APP_EXTRACTION_THINKING=adaptive:low`
(run 05), `disabled` (run 06) and `default` (run 07, the baseline),
compared document by document in
[`spike/thinking_experiment.json`](spike/thinking_experiment.json)
(rendered by `scripts/thinking_experiment.py`). Run 07 exists to
deconfound the comparison: the arms were first compared against run 04,
which was schema v0.3 (description cap 200), so the cap change was mixed
into the attempt counts; all three runs now share one schema. Output
tokens fell to 0.48× of the baseline in both arms (76,938 → 36,805 /
36,629); on first attempts only, 0.40× at `adaptive:low` and 0.49× at
`disabled`. Cost per document $0.101 → $0.063 / $0.057. Mean attempts
1.40 (baseline) / 1.40 / 1.10; run 07's four retries were three invented
extra keys and one malformed JSON object (the first non-schema validation
failure in seven runs), none the description cap. Exact-match agreement
with run 07 on `trigger.label` / `mechanism.label` / `detection_method`:
10 / 7 / 9 of 10 at `adaptive:low`, 8 / 5 / 8 at `disabled`. Most of the
mechanism mismatches in both arms are different wording for one idea
(`race_condition` vs `network_route_deletion` for Datadog is not; the
class values are still open). The exception that matters is AWS at
`disabled`: trigger became `race_condition` and mechanism
`dns_resolution_failure`, so the mechanism moved into the trigger slot
and the symptom into the mechanism slot, which is the two-field
distinction ADR-001 exists to keep; `adaptive:low` kept the trigger null.
Each arm has 28 value↔null flips against run 07, 22 of them shared by
both arms (GitLab and Slack `mitigations[].date`, `list_is_complete` on
three documents, Google Cloud `detected_at`, CrowdStrike's anchors), so
the baseline is not a fixed reference either. The AWS trigger, which
ADR-001 uses as its example of a null trigger, is null in runs 02, 03, 05
and 07, `operational_delay` in run 04 and `race_condition` in run 06.
Agreement here is with run 07, not with ground truth; M5's eval against
the gold set is what measures quality.

**Run 08 (schema v0.5, `adaptive:low`)** is the first run with the
closed mechanism enum, the corpus leak removed and the ADR-006 §5 rule in
the trigger description; compare with run 05 (v0.4, same thinking
setting). Every mechanism label is a class from the list by
construction, and none is `other` (ADR-006 §8's ceiling is 20%). The
labels: Cloudflare `limit_violation` and Google Cloud
`null_pointer_failure` (the two the ADR split out of
`crash_on_bad_input`), Datadog `route_deletion`, Roblox
`lock_contention`, Kubernetes `workload_misrouting`; the other five match
run 05 string for string. The AWS trigger is null (self-reported
confidence 0.55), consistent with the §5 rule, but this is one run and
the null rate over repeats is still the number to watch. Mean attempts
1.50 (five retries, up from four); none was an out-of-enum label — all
five were invented extra keys, and three of them sat on the trigger and
mechanism objects (`source_section`, `at`, `label_ignore`), which had not
happened in runs 05–07 and may be a side effect of the longer label
description; noted, not concluded. Cost $0.65, within noise of run 05's
$0.63. ADR-006 §8's pre-registered checks (a second `adaptive:low` run
agreeing on 9 of 10 mechanism labels, and 8 of 10 agreement with the M0
blind labels on a re-label pass) have not been run yet.

**Corpus expansion to 30 and run 09 (2026-09-16).** The mechanism enum
was derived from documents A–J, which are all large infrastructure
vendors. Twenty documents it was not built from were added to
[`spike/corpus_manifest.json`](spike/corpus_manifest.json) (selection
filter recorded there: at most 5 large vendors, at least 8 smaller
companies or individual blogs, at least 4 where users noticed first,
markdown/HTML/PDF kept, no incident already in the corpus): 3 large
vendors, 2 mid-size companies, 6 small companies, 3 individual blogs, 4
open-source projects, 2 non-software organisations (a university's
external storage-outage review and an air-accident report on an
airline's load-sheet software); 16 HTML, 2 markdown, 2 PDF. Run 09
extracted the 20 at v0.5 / `adaptive:low` and reports A–J from their
run-08 rows (same schema, provider, model and thinking, so the pipeline
treats them as duplicates; the report marks them `reused_from_earlier_run`
and bills nothing for them). Findings, reported and not acted on:

- **`other`: 7 of 30 (23%), all seven among the new 20 (35%).** ADR-006
  §8 set 20% as the ceiling above which the class list is too narrow.
  Four of the seven are software defects that crash, hang, or compute a
  wrong answer without being a null dereference, a limit breach, or an
  out-of-bounds read (incident.io's poison-pill panic, Firefox's infinite
  loop on an unexpected header, Boskos's crash loop on a latent startup
  bug, the load-sheet system mis-weighting passengers titled "Miss"); one
  is a supply-chain compromise (ESLint); one is a storage-firmware flaw
  that lost data during a hardware replacement (King's College London);
  one is a Postgres sequence gap after failover (incident.io) that
  produced no outage at all. The retired `crash_on_bad_input` would have
  covered the first group. Self-reported mechanism confidence on the
  seven is 0.50–0.60. Every one of the twelve classes was used at least
  once across the 30.
- **Trigger collisions.** One confirmed same-concept/different-string
  pair appeared: `internal_config_change` (LaunchDarkly) against
  `config_change` (Cloudflare, Google Cloud, Cloudflare 2019). Borderline:
  `infrastructure_config_change` (Firefox: an external provider's default
  change), `infrastructure_downgrade` (Buildkite: an instance-size
  change), and Twilio's on-call host restart labelled
  `infrastructure_maintenance` where GitLab's and Facebook's operator
  actions are `manual_command`. ADR-006 §1 said to revisit the open
  trigger vocabulary if confirmed collisions appeared. Also, the
  Kubernetes 1.15 code-freeze load is labelled `traffic_spike`, which
  ADR-006 §5 classes as an anomalous condition; the description names
  the code freeze it is attributable to, so the label sits on the wrong
  side of the rule while the description sits on the right one. Trigger
  was null on 4 of 30 (AWS, Honeycomb, incident.io's poison pill,
  PythonAnywhere).
- **`customer_report` fired for the first time: 6 of 30**, all in the new
  20 (Atlassian, incident.io, Buildkite, Turso, and both new Kubernetes
  test-infra postmortems). Distribution across 30: monitoring 10,
  internal_manual 7, customer_report 6, unknown 4, operator 2,
  ambiguous 1. The absence in runs 02–08 was the corpus, not the schema.
- **Attempts and cost.** Mean 1.45 over the 20 new documents (9 retries).
  The extra-key pattern on the trigger and mechanism objects persisted
  and grew: 7 of the 9 retries added an invented key there
  (`quote_source` three times, `source_section` twice, `quote2`,
  `label_alt`, `trigger_at`, `quote_ok`, a nested `trigger`), against 3
  of 5 in run 08 and 0 in runs 05–07. Three runs now point the same way;
  the v0.5 label description is the likely cause and is worth a measured
  fix. The other two retries were one malformed JSON object and one
  record-level extra key. $1.06 for the 20 ($0.053 per document; the
  51k-character Atlassian review cost $0.12).
- **Reproducibility of A–J was not re-measured**: their run-09 rows are
  run-08's rows. ADR-006 §8's two-run agreement check still needs a
  second `adaptive:low` run, which the unique index on complete rows
  prevents at the same schema version without a deliberate re-run path.
  (Fixed separately: `extractions.run_id` — migration 0007 — joins the
  idempotency key so a repeat run at identical settings now gets its own
  row instead of no-oping against the first.)

**ADR-008 taxonomy revision and run 10 (2026-09-16).** Run 09 put
mechanism `other` at 23% (above ADR-006 §8's 20% ceiling) and produced a
confirmed trigger same-concept collision, so ADR-008 restored a general
`software_defect` mechanism class (plus `security_compromise`,
`hardware_data_loss`, `consistency_anomaly` — sixteen classes total) and
closed `trigger` into a ten-class enum, both with definitions in
`app/extract/taxonomy.py`. Schema bumped to v0.6. **Prediction, recorded
before running** run 10: the v0.5 investigation (run 09 vs. schema v0.4)
found the extra-key retries correlated with the length of the enum-
definition block inserted between `label` and `quote` on the `Mechanism`
object. ADR-008 roughly doubles that block (2,502 characters, up from
~1,450) and adds an equivalent ~1,360-character block to `Trigger`, which
had none. Predicted: mean validation attempts above run 09's 1.45, and
invented keys on both the trigger and mechanism objects, not only
mechanism.

**Actual: the prediction was wrong on both counts.** All 30 documents at
v0.6 / `adaptive:low`:

- **Mechanism: `other` 1/30 (3.3%)**, back under the 20% ceiling — Firefox
  only (a header case-sensitivity bug causing an infinite loop; it does
  not cleanly fit any of the fifteen named classes). `software_defect`
  fired on 5/30 (16.7%): incident.io (a poison-pill panic), Knight
  Capital (Doug Seven's account), the Kubernetes test-infra Boskos crash
  loop, the King's College London storage review, and the TUI Airways
  load-sheet mis-weighting — four of these five are exactly the
  `other`-in-run-09 cases ADR-008 §3 named as the reason to restore the
  class. Every one of the sixteen classes except `other` was used more
  than once except `race_condition`, `out_of_bounds_read`,
  `null_pointer_failure`, `limit_violation`, `workload_misrouting`,
  `hardware_data_loss`, and `security_compromise` (one each) — no class
  went completely unused.
- **Trigger: closed enum held on all 30**, zero documents needed a class
  outside it. Distribution: `config_change` 5, `manual_command` 5, null
  4 (13.3%), `infrastructure_maintenance` 4, `code_deploy` 4,
  `content_update` 2, `external_service_degradation` 2,
  `os_auto_update` 1, `feature_rollout` 1, `database_failover` 1,
  `account_compromise` 1. `internal_config_change` (run 09's confirmed
  collision case, LaunchDarkly) did not recur as a free string — the
  document now returns `config_change`, ADR-008's intended
  normalization.
- **detection_method**: monitoring 10 (33.3%), customer_report 7 (23.3%),
  internal_manual 6 (20.0%), operator 3 (10.0%), unknown 3 (10.0%),
  ambiguous 1 (3.3%). Comparable in shape to run 09's distribution.
- **Mean validation attempts: 1.13 (4 retries), lower than run 09's
  1.45, not higher.** The trigger/mechanism extra-key pattern did **not**
  persist: of the 4 retries, only one (CrowdStrike) touched the trigger
  object at all, and its error (`record.trigger.}: Extra inputs are not
  permitted`) is a malformed-JSON artifact — a stray brace parsed as a
  key — not an invented field name like run 09's `quote_source` /
  `source_section` family. The other three invented keys
  (`affected_org_kind_confidence_placeholder`, `resolved_at_source`,
  `detection_quote_secondary`) sit on unrelated top-level or time-anchor
  fields. The correlation the v0.5 investigation reported (longer
  inserted enum block near `label`/`quote` → more retries there) did not
  hold when the block grew further and a second one was added elsewhere
  in the same schema; whatever run 08→09's proximity effect was, it is
  not a simple function of enum-block length. Not re-investigated further
  per this task's instructions.
- **Cost: $1.28 for all 30** ($0.043/document mean, $0.02–$0.10 range) —
  higher in total than run 09's $1.06 because run 09 billed only its 20
  new documents (10 were reused from run 08 at $0), but lower per
  document than run 09's $0.053 and than run 08's $0.065, consistent with
  fewer retries.
- **Not done, per this task**: the enums were not revised based on this
  result. ADR-008 §5's held-out-evaluation discipline applies to the
  *next* corpus expansion, not to re-scoring this one; run 10 is itself
  the frozen-taxonomy evaluation ADR-008 committed to, and its numbers
  are reported, not acted on further here.

### Open — flagged, not decided

- **Taxonomy checks against the M0 blind labels are still outstanding.**
  ADR-008 (schema v0.6) closed both `mechanism` (sixteen classes) and
  `trigger` (ten classes) after the 30-document corpus showed ADR-006's
  classes were overfit to the ten they were derived from — see run 10
  above. ADR-006 §8's 8-of-10 agreement check against the M0 blind labels
  (`eval/blind_labels_m0.json`) has not been run against either enum's
  current values. ADR-008 §5 pre-registers the discipline for the *next*
  corpus expansion: freeze the enums before inspecting the new documents,
  treat the new documents as held out, and record the 20%-`other` /
  80%-trigger-agreement results before revising anything.
- **ADR-006 §8's two-run reproducibility check is still unmeasured.** The
  `run_id` fix (migration 0007) makes it possible — two `adaptive:low`
  runs at the same schema version now each get their own row — but no
  second run at one fixed schema version has been executed for the sole
  purpose of that comparison; run 10 changed the schema version from
  run 09, so it is not a same-settings repeat.
- **What counts as an initiating "event"** — decided by ADR-006 §5, kept
  here until it is measured with repeats. The AWS trigger flipped between
  null and a value across runs on identical read-side text because the
  prompt's "change or event" let an anomalous delay qualify
  ([`spike/nullable_trigger_regression.md`](spike/nullable_trigger_regression.md)).
  Since schema v0.5 the trigger description and the prompt state the rule:
  an anomalous condition is never the trigger; the change or external
  event it is attributable to is; with neither, null. One run per setting
  cannot separate a wording effect from a coin flip on a borderline
  document, so the null rate over repeated runs is the number to watch.
- **Document ≠ incident** (FINDINGS §4.11, §4.12). `extractions` keys on
  `document_id`; there is no `incident_id`, and a multi-period document
  yields one record for its primary incident. Needs a schema decision.
- **Partial records** (DERIVE-03). `extractions.status` is `complete |
  failed` only; the draft's `partial` is not implemented until the ADR
  says what it means.

## Parsing

`app/ingest/parse.py` normalizes markdown/HTML/PDF to text. Every fix in
it traces to a numbered finding in [`spike/FINDINGS.md`](spike/FINDINGS.md)
§2, discovered by running the M0 spike's throwaway extractor against a
real 10-document, 5-format corpus (`spike/raw/`):

- longest `<article>`/`<main>` candidate, not the first (GitHub's first
  `<article>` is an author-bio card; Slack's is a related-post card)
- title from `<title>`/`og:title` metadata, never from body text (the
  `<h1>` is often inside a stripped `<header>`)
- truncate at the first related-post/next-post/newsletter marker, so a
  different incident's date or headline can't leak into the text
- strip PDF running-header/footer furniture (`Page 4 of 12  2024-08-06`)
  that pypdf otherwise injects mid-sentence

`tests/test_parse.py` runs the parser against all 10 documents in
`spike/raw/` and asserts each of the above by name.

## Storage

Raw bytes and normalized text both live in `documents.raw_bytes` /
`documents.text` in Postgres for now, not MinIO — the brief's object-store
requirement (§9) is deferred to a later milestone. `app/storage/` is a
narrow interface (`save_raw`) so that swap touches one module, not the
ingest pipeline; `documents.storage_backend` already records which backend
wrote a given row.

## Queue

Implemented per [ADR-003](docs/adr/003-queue.md): Postgres `SELECT ... FOR
UPDATE SKIP LOCKED`, no Redis/Celery. The entire queue implementation is
isolated behind `app/queue/` — nothing outside that package touches the
`jobs` table directly. See the ADR for the job lifecycle and the claim
query.

## Development

```
pip install -e ".[dev]"
docker compose up -d postgres
pytest
ruff check .
mypy app
```

Tests run against real Postgres (no SQLite, per the brief) but in their
own database: `tests/__init__.py` forces `APP_DATABASE_URL` to
`APP_TEST_DATABASE_URL` (default `incident_intel_test` on the compose
Postgres), conftest creates it if missing and rebuilds the tables from
the models, and refuses any database whose name does not end in `_test`.
The worker/API database (`incident_intel`) is never touched by tests, so
the compose stack can stay up while they run. Tests truncate
`extractions`/`documents`/`jobs`/`sources` between tests. HTTP fetches are
mocked with `respx`; the LLM is a scripted fake (`tests/fake_llm.py`) in
pipeline and worker tests, and the Anthropic adapter is tested through the
real SDK over an in-process mocked HTTP transport
(`tests/test_extract_llm.py`), so no test needs an API key or makes a
network call. `tests/test_parse.py`
reads real bytes from `spike/raw/` but performs no network I/O itself.

Without a local Python 3.12, the same checks run in a container against the
compose Postgres:

```
docker build -f Dockerfile.dev -t incident-intel-dev .
docker run --rm --network host -v "$PWD:/srv" \
  incident-intel-dev sh -c "ruff check . && mypy app && pytest -q"
```
