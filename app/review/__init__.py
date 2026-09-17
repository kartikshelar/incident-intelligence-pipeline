"""M4: confidence-based review queue with correction write-back (ADR-010).

The unit of review is the field, not the document. Every top-level field
of every complete extraction has a `field_reviews` row (app/db/schema.py)
carrying its review state; the queue is the set of rows in state
`routed`, and a human decision moves a row to `reviewed` and records
either acceptance or a corrected value next to the model's, never over
it. Reviewed rows are the gold-set input the brief's M5 eval consumes.

Layout:
  fields.py     one row per field: creation at extraction time, decisions
                (accept / correct / skip) and the correction validator
  routing.py    the routing policy — rank eligible fields by ascending
                self-reported confidence under the configured floor — and
                the budget cap applied after ranking
  queries.py    read side: the queue in presentation order, one field with
                its document, and reviewed decisions as gold-set input

Nothing in this package tunes the floor: it is `settings.review_confidence
_floor`, and M5 sweeps it (ADR-010 §3). Review precision and recall are
M5's metrics and are not computed here.
"""
