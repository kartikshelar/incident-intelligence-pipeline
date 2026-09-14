"""Ingest & parse (M2): fetch a source URL, normalize its content to text,
with provenance. No extraction, no LLM calls — that's M3.

Public surface: `app.ingest.pipeline.ingest_source`, called from the worker.
Everything else in this package is an implementation detail of that call.
"""
