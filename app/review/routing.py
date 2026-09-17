"""Routing policy (ADR-010 §1, §3): which fields get a human.

Policy, in this order:
  1. Eligible = `unreviewed` fields whose self-reported confidence is
     strictly below the floor. Reviewed fields are never eligible again
     (ADR-010 §6); already-routed fields stay routed.
  2. Rank eligible fields by ascending confidence (lowest first — the
     field most likely to be wrong is the most valuable to look at).
  3. The budget, if any, is applied AFTER ranking: it decides how many of
     the ranked fields become `routed` in this pass, never which ones rank
     first. The cap is on fields *awaiting* review (state `routed`), so a
     pass tops the queue back up to the budget as the reviewer works
     through it. Unset means uncapped — the mode M5 evaluates the policy
     in (ADR-010 §5: "precision and recall are evaluated on the uncapped
     threshold sweep, not under the operational review-budget cap").

The floor and budget are parameters here and configuration at the call
sites (app.settings: APP_REVIEW_CONFIDENCE_FLOOR, APP_REVIEW_BUDGET). The
0.70 default is ADR-010's provisional operating point; M5 sweeps it by
changing the configuration, and `rank_eligible` is the read-only "what
would route at floor X" that sweep needs. Nothing here tunes the floor
against data, and nothing here computes review precision or recall.

A pass is idempotent and re-runnable: rerunning it at the same floor
routes nothing new. Lowering the floor later does not un-route fields
that were routed under the higher one — a routed field stays routed.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import Connection, text

# Serialises concurrent passes (two workers finishing extractions at the
# same moment) so the budget cannot be overshot by both counting the same
# outstanding set. Released at the caller's commit/rollback.
_ROUTING_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext('field_reviews.routing'))")

# Ties broken by age then field name so the order is stable across calls.
_RANK_ORDER = "ORDER BY confidence ASC, created_at ASC, field ASC"


@dataclasses.dataclass(frozen=True)
class RankedField:
    id: uuid.UUID
    extraction_id: uuid.UUID
    field: str
    confidence: float


@dataclasses.dataclass(frozen=True)
class RoutingResult:
    floor: float
    budget: int | None
    eligible: int  # unreviewed fields below the floor before this pass
    outstanding_before: int  # fields already awaiting review before this pass
    routed: list[RankedField]  # what this pass moved to `routed`, in rank order


def rank_eligible(conn: Connection, *, floor: float) -> list[RankedField]:
    """Read-only: the unreviewed fields below `floor`, in routing order."""
    rows = conn.execute(
        text(
            "SELECT id, extraction_id, field, confidence FROM field_reviews "
            "WHERE review_state = 'unreviewed' AND confidence < :floor " + _RANK_ORDER
        ),
        {"floor": floor},
    ).mappings()
    return [RankedField(**dict(r)) for r in rows]


def outstanding(conn: Connection) -> int:
    """Fields currently awaiting a reviewer."""
    return int(
        conn.execute(
            text("SELECT count(*) FROM field_reviews WHERE review_state = 'routed'")
        ).scalar_one()
    )


def route_pending(conn: Connection, *, floor: float, budget: int | None = None) -> RoutingResult:
    """Run one routing pass inside the caller's transaction."""
    if budget is not None and budget < 0:
        raise ValueError("review budget must be non-negative or None (uncapped)")
    conn.execute(_ROUTING_LOCK_SQL)

    eligible = int(
        conn.execute(
            text(
                "SELECT count(*) FROM field_reviews "
                "WHERE review_state = 'unreviewed' AND confidence < :floor"
            ),
            {"floor": floor},
        ).scalar_one()
    )
    outstanding_before = outstanding(conn)

    if budget is None:
        limit: int | None = None  # LIMIT NULL is LIMIT ALL in Postgres
    else:
        limit = max(0, budget - outstanding_before)

    routed: list[RankedField] = []
    if limit is None or limit > 0:
        rows = conn.execute(
            text(
                "UPDATE field_reviews SET review_state = 'routed', routed_at = now() "
                "WHERE id IN ("
                "  SELECT id FROM field_reviews "
                "  WHERE review_state = 'unreviewed' AND confidence < :floor "
                f"  {_RANK_ORDER} LIMIT :limit"
                ") RETURNING id, extraction_id, field, confidence"
            ),
            {"floor": floor, "limit": limit},
        ).mappings()
        routed = sorted(
            (RankedField(**dict(r)) for r in rows), key=lambda f: (f.confidence, f.field)
        )

    return RoutingResult(
        floor=floor,
        budget=budget,
        eligible=eligible,
        outstanding_before=outstanding_before,
        routed=routed,
    )
