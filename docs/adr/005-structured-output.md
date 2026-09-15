### 1. Decision

Extraction v0 sends the incident-record JSON schema to the model **as text
in the cached system block** and enforces it **client-side** (Pydantic
validation with the error fed back, up to `APP_EXTRACTION_MAX_ATTEMPTS`).
It does not use the API's grammar-constrained structured output
(`output_config.format = json_schema`), which was the M3 design.

Drafted by Claude Code on 2026-09-15 from measured data at Kartik's
request; to be rewritten in Kartik's own words if kept.

### 2. Why

The first real run (`spike/extraction_run_01.json`) had all 10 requests
rejected with `400 invalid_request_error: The compiled grammar is too
large`, before any tokens were billed. Probing the API with variants of
the v0.1 wire schema (`claude-sonnet-5`, `max_tokens=16`, one request per
variant) established where the limit sits:

| Variant | Result |
|---|---|
| v0.1 as designed (record + 23-key confidence) | 400 |
| record only (confidence removed) | 400 |
| record only, minus 1 / 2 / 3 of the five TimeAnchor objects | 400 |
| record only, minus all five TimeAnchor objects | **OK** |
| record minus all anchors + confidence with 5 keys | 400 |
| record + confidence as a bare `number[]` | 400 |
| anchors flattened to 25 / 20 / 15 / 10 / 5 flat record fields | 400 |
| descriptions stripped (grammar does not compile descriptions) | 400 |

The budget is roughly "the record without its time anchors and without
confidence", so the two things the schema exists to capture beyond the
draft — typed, sourced time anchors (FINDINGS §4.1) and per-field
confidence (DERIVE-05) — are exactly what does not fit. Flattening the
anchors, which was the obvious lever, does not help at all: even five
nullable strings push it over.

### 3. What I rejected

**Splitting into two calls per document** (record without anchors and
confidence; then anchors + confidence, given the record). It would fit,
but it doubles request count and latency, roughly doubles input cost
(the document is sent twice), and asks the model to assign confidence to
values it produced in a different call, which weakens the only
confidence source we have until DERIVE-05 is measured.

**Cutting the schema to fit.** Dropping anchors or confidence to keep the
grammar guarantee inverts the priority: the guarantee exists to protect
the schema, not the other way round.

**Strict tool use.** Same grammar compiler; the error message itself
says "reduce the number of strict tools".

### 4. What this costs

- Schema conformance is no longer guaranteed at the source. It rests on
  the validate-and-retry loop (`app/extract/extractor.py`), which was
  built for constraints the API cannot express anyway. Run 02 (10/10
  complete, mean 1.20 attempts) shows the loop absorbing the difference:
  both retries were an invented extra key, corrected on the second
  attempt.
- Each validation retry is a full second request (document included);
  the attempt budget bounds the cost.
- The schema text is prompt input on every request (~4k tokens with
  descriptions), which is why descriptions are capped at one sentence and
  the block is cached (`cache_read_input_tokens` ≈ 5.5k per document
  after the first in run 02).

### 5. When I'd switch back

Restore `output_config.format` when any of these holds: the API raises
the grammar limit (re-probe with `wire_schema()` and one `max_tokens=16`
request); the schema shrinks below it; or the M5 eval shows the retry
loop leaks invalid records at a rate that matters. The adapter change is
one function (`AnthropicClient.complete`); the tests in
`tests/test_extract_llm.py` pin the current wire format.

### 6. How I'd know I was wrong

If the mean validation attempts per document rises with corpus size, or
a meaningful share of documents exhaust the attempt budget, the loop is
paying for what the grammar would have given for free, and the two-call
split should be measured against it in M5.
