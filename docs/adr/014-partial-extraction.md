# ADR 014: Partial extraction

## Status

Accepted — DERIVE-03 uses all-or-nothing extraction.

## Context

Validation retry recovered every failure from run 02 onward. Mean attempts are 1.13–1.50 across production-setting runs; run 06, with thinking disabled, reached 1.10. After the run-01 grammar bug, every run from 02 through 12 completed with zero dead-lettered documents. A partial record in the database is harder to reason about than a failed job with a recorded reason.

Per-field review, defined in ADR 010, already handles the uncertain-field case after a successful extraction. Partial extraction would be a second mechanism for a similar problem.

## Decision

Use only `complete` and `failed` extraction states. Do not add a `partial` state.

## Consequences

The database contains either a complete extraction or a failed job with a recorded reason. The cost is explicit: one bad field loses twenty-two good ones.

## What would change this decision

A document type where one field is structurally unextractable would require revisiting this decision. In that case, all-or-nothing becomes all-nothing.
