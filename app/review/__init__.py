"""M4: confidence-based review queue with correction write-back (ADR-010).

The unit of review is the field, not the document. Every top-level field
of every complete extraction has a `field_reviews` row (app/db/schema.py)
carrying its review state; the queue is the set of rows in state
`routed`.

Layout:
  fields.py     one row per field, created in the extraction's transaction
  routing.py    the routing policy — rank eligible fields by ascending
                self-reported confidence under the configured floor — and
                the budget cap applied after ranking

Nothing in this package tunes the floor: it is `settings.review_confidence
_floor`, and M5 sweeps it (ADR-010 §3). Review precision and recall are
M5's metrics and are not computed here.
"""
