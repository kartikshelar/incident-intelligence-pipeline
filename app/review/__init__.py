"""M4: confidence-based review queue with correction write-back (ADR-010).

The unit of review is the field, not the document. Every top-level field
of every complete extraction has a `field_reviews` row (app/db/schema.py)
carrying its review state.

Layout:
  fields.py     one row per field, created in the extraction's transaction
"""
