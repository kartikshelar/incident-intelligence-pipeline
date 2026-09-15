### 1. Decision

A document is its **extracted text**, not its bytes. Two hashes, two jobs:

| Column | Over | Role |
|---|---|---|
| `content_hash` | raw bytes as fetched | storage key: names what is in `raw_bytes`; unique |
| `text_hash` | normalized text the parser produced | document identity and the ingest idempotency key; unique |

Re-ingest semantics (DERIVE-07), in the order the pipeline checks them:

1. **Same bytes** → no-op. The existing document is returned before anything
   is parsed.
2. **New bytes, same text** → the same document. Its provenance columns —
   `source_url` (the final URL), `fetched_at`, `content_hash`, `raw_bytes`,
   `storage_backend` — are updated in place to describe this fetch. No new
   row, no new extraction; the document keeps its id and everything keyed
   on it.
3. **New bytes, new text** → a new document. The old row is kept as it was.

Both hashes are stored on every row (`app/db/schema.py`; migration 0005
backfills `text_hash` and merges rows that already shared a text). The
runtime rule is `app/ingest/pipeline.py`; `tests/test_pipeline.py` pins
all three cases.

Drafted by Claude Code on 2026-09-15 from the run-03 data at Kartik's
request; to be rewritten in Kartik's own words if kept.

### 2. Why: the AWS / Google Cloud case

Run 02 and run 03 registered the same ten URLs a day apart. Eight came
back byte-identical and were correctly treated as duplicates. Two did not:

| Source | run 02 `content_hash` | run 03 `content_hash` | raw bytes | text chars |
|---|---|---|---|---|
| aws.amazon.com/message/101925/ | `fbbd07538daf…` | `35c8be935c01…` | 365,938 / 365,938 | 24,080 / 24,080 |
| status.cloud.google.com/incidents/ow5i… | `e83ddcec1a0d…` | `0939b5e79c02…` | 49,860 / 49,860 | 20,855 / 20,855 |

Diffing the stored bytes: AWS differs in exactly two lines, a
`Content-Security-Policy` meta tag and an `esms-options` script block,
each carrying a per-response nonce; Google Cloud differs in two lines
that each carry a `<script nonce="…">` attribute. The extracted text is
identical byte-for-byte (verified: `sha256(text)` equal for both pairs).
These pages will never hash the same way twice, so under the M2 rule
("same `content_hash` never produces a duplicate") every re-registration
of them creates a new document, and the extract job for that document
finds no `complete` row for its id and runs again. Run 03 happened to
change the schema version, which would have re-extracted anyway; under a
fixed schema it would have been the same text extracted and paid for
twice (~$0.10 per document per run), with two rows in `documents` that
downstream code has no way to know are one document.

This is what the brief's DERIVE-07 ("re-ingest semantics when a source
is edited upstream") turned out to mean in practice: the common
re-ingest is not an upstream edit at all, it is the same document
re-served with different chrome.

### 3. What I rejected

**Normalizing the bytes before hashing** (strip nonces, CSP headers,
timestamps). It is a whack-a-mole list per publisher, and the stored
bytes are supposed to be the bytes as fetched — hashing something other
than what is stored breaks the one thing `content_hash` is for.

**Keying identity on the URL.** Both observed cases are the same URL, but
a URL is not content: the same page is edited over time (GitLab's 2017
post has been), and FINDINGS §4.12 already lists one incident published
at two URLs (blog post + Google Doc). The URL is provenance, not
identity, and it is what this decision updates rather than keys on.

**A fuzzier text identity** (whitespace-collapsed, or a similarity
threshold). Not needed by any observed case — exact text matched in
both — and a threshold turns "is this the same document" into a tunable,
which is worse than a definition. If it becomes necessary (§6) it is a
change to `text_hash_of` plus a migration, not a schema change.

**Keeping both rows and linking them.** A second row for identical text
carries no information. Linking is the right shape for *different* text
about the same incident, which is FINDINGS §4.12 (document ≠ incident)
and stays open; this ADR does not add a `supersedes` pointer or an
`incident_id`.

### 4. What this costs and changes

- `content_hash` is no longer stable for a document across fetches. Code
  that wants a stable handle uses `text_hash` or the row id. The report
  script (`scripts/extraction_run.py`) now records both.
- `fetched_at` now means "when the bytes currently in `raw_bytes` were
  fetched"; `created_at` is when the document was first seen. The
  provenance tuple (`content_hash`, `raw_bytes`, `source_url`,
  `fetched_at`, `storage_backend`) always describes the stored bytes; the
  identity tuple (`text`, `text_hash`, `title`, `created_at`) never
  changes after insert.
- `source_id` stays the first registrant. The extract job enqueued by a
  later registration still targets the same document, so a document whose
  extraction dead-lettered gets another attempt on re-registration, as
  before.
- `title` is not updated on the provenance path. It comes from metadata,
  not from the text, so in principle bytes could change the title while
  the text stays the same; the row keeps its first-seen title. No observed
  case; noted so it is not mistaken for an oversight.
- Same bytes short-circuit before parsing, so a parser fix is not applied
  to documents already stored. That was already true; a re-parse job does
  not exist and is not this decision.
- Migration 0005 merges existing duplicates into the oldest row and copies
  the newest duplicate's provenance onto it. If a merge would put two
  `complete` extractions for one (schema, provider, model) on one document
  the migration stops and names them instead of deleting a record; on the
  real database this did not occur (the pairs were schema 0.1 and 0.2).

### 5. Upstream edits

The other half of DERIVE-07. An edit that changes the extracted text is a
**new document**: the old row, its extractions, and its confidence stay
untouched, and the new text is extracted on its own. Nothing links the
two. That is deliberate — an edited postmortem is a different piece of
evidence, and whether two documents describe one incident is the §4.12
decision, which needs an incident table, not a document flag.

### 6. How I'd know I was wrong

- Re-fetches of one URL keep producing new documents whose texts differ
  by a handful of characters (a "last updated" stamp or a view counter
  inside the article body). The check is one query — documents per
  `source_url`, with the text diff size for pairs — and the fix is a
  normalized `text_hash_of` plus a migration, per §3.
- A publisher issues a correction that should *replace* the earlier
  text for evaluation purposes rather than sit beside it. Then §5's
  "no link" is the wrong default and a `supersedes` pointer is needed
  before the incident table exists.
- Provenance updates start hiding something we needed: if `raw_bytes`
  history matters (which chrome a page had on a given day), the update
  in place should become an append to a per-fetch table. No current use
  case reads old bytes.
