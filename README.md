# Incident Intelligence Pipeline

Ingests public engineering postmortems, extracts a structured incident
record from each, routes low-confidence fields to human review, and
measures its own extraction quality against a frozen hand-labeled gold set.

See [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) for the full contract, milestone
plan, and open [DERIVE] decisions. Architectural decisions are recorded in
[`docs/adr/`](docs/adr/).

## Status: M3 — Extraction v0

A registered source URL is fetched and normalized (M2), then a second job
sends the text to Claude and stores one validated incident record per
document in `extractions`. Output is validated against the schema; invalid
output is retried with the validation error fed back; every failure is a
row, not a log line. Per-field confidence is captured but not yet
thresholded (that's M4).

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
| `APP_EXTRACTION_THINKING` | thinking/effort, interpreted by the provider and stored verbatim. For `anthropic`: `default` (send nothing; the API's default, which on Sonnet 5 is adaptive thinking), `adaptive`, `disabled`, each optionally `:<low\|medium\|high\|xhigh\|max>` for `output_config.effort`, e.g. `adaptive:low`. The project default, `default`, lives in `.env.example` and `docker-compose.yml` |
| `ANTHROPIC_API_KEY` | the provider's key, under the SDK's own name |
| `APP_EXTRACTION_MAX_TOKENS`, `APP_EXTRACTION_MAX_ATTEMPTS` | 16000 / 3 |

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

**Schema v0.4** (`app/extract/schema.py`) is PROJECT_BRIEF §6's draft after
the spike's corrections, each cited in the module docstring:

- `trigger` (nullable: initiating change/event) + `mechanism` (required,
  single-valued: what failed), per [ADR-001](docs/adr/001-trigger-taxonomy.md).
  `change_induced` is gone — it is `trigger is not null`.
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
`confidence_source='self_report'`. Nothing reads it yet. Whether
self-report is a usable confidence source is DERIVE-05 and gets measured in
M5, not assumed here.

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

| Run | Uncached input | Cache read | Output | Written-description chars | Mean attempts | Cost / document |
|---|---|---|---|---|---|---|
| run_02 (v0.1) | 88,570 | 60,852 | 75,927 | 11,644 | 1.20 | $0.096 |
| run_03 (v0.2) | 98,593 | 62,376 | 76,526 | 11,442 | 1.30 | $0.099 |
| run_04 (v0.3) | 106,126 | 72,293 | 80,412 | 8,713 | 1.40 | $0.104 |
| run_05 (v0.4, `adaptive:low`) | 118,782 | 72,293 | 36,805 | 8,905 | 1.40 | $0.063 |
| run_06 (v0.4, `disabled`) | 87,899 | 61,171 | 36,629 | 11,342 | 1.10 | $0.057 |
| run_07 (v0.4, `default`) | 106,797 | 72,293 | 76,938 | 11,324 | 1.40 | $0.101 |

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

### Open — flagged, not decided

- **Trigger / mechanism class values.** ADR-001 fixes the *structure* and
  defers the class lists to "01b", which does not exist yet. Until it does
  the two labels are open snake_case strings (`app/extract/taxonomy.py`),
  stored verbatim; swapping in a `Literal[...]` is a one-line change there.
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
