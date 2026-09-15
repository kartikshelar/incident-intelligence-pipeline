"""M3: extraction v0 — one validated incident record per document.

Layout:
  schema.py     the incident record (v0.1) as Pydantic models — the contract
  taxonomy.py   enum values: detection_method (ADR-002) and the still-open
                trigger/mechanism class lists (ADR-001 defers them to 01b)
  prompt.py     system prompt + per-document user message + retry feedback
  llm.py        narrow LLM client interface and the Anthropic implementation
  extractor.py  the validate-and-retry loop (no database)
  derive.py     values computed from the record, not extracted (durations)
  pipeline.py   load document -> extract -> persist `extractions` row
  errors.py     transient / permanent extraction failures
"""
