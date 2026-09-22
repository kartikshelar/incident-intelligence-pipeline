# ADR 013: Multi-tenancy

## Status

Accepted — DERIVE-04 is not implemented because it is not needed.

## Context

The system has one user. Every source is a public postmortem, so no tenant's data is private from another. The gold set is one shared artifact by design.

The Basic Auth gate is a lock on the write surface to protect gold-set integrity. It is not tenancy: there is one credential pair, no accounts, and no data isolation.

## Decision

Do not implement multi-tenancy.

## What I rejected

Implementing tenancy anyway. Tenant IDs and per-user scoping would make the system look more production-grade, but with one user and an all-public corpus they would isolate nothing. Unused isolation is untested isolation, and presenting it as a feature would be misleading.

## Consequences

The system has no tenant accounts or tenant-level data isolation. The Basic Auth gate protects writes but must not be described as a multi-tenancy mechanism.

## What would change this decision

Private sources or more than one team labeling would make isolation a real requirement. Either change would require revisiting this decision.
