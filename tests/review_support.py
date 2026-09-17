"""Helpers for the M4 review tests: a document, and a complete extraction
with chosen per-field confidences, made the way the worker makes them
(through extract_document with the scripted fake LLM), so the
field_reviews rows come from the real pipeline path."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import Engine, text

from app.extract.pipeline import extract_document
from tests.fake_llm import FakeLLMClient, valid_output

DOCUMENT_TEXT = (
    "Cloudflare outage on November 18, 2025.\n\n"
    "At 11:05 a change to one of our database systems' permissions caused the "
    "Bot Management feature file to double in size. Impact starts 11:28. "
    "At 11:31 automated tests flagged the errors; our first automated test detected "
    "the issue within minutes. When the file exceeded its limit the software panicked "
    "and the core proxy returned HTTP 5xx errors. By 14:30 core traffic flowing "
    "again; at 17:06 all services were restored.\n"
)


def insert_document(
    engine: Engine, *, title: str | None = "Cloudflare outage", text_body: str = DOCUMENT_TEXT
) -> uuid.UUID:
    source_id, document_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO sources (id, url) VALUES (:id, :url)"),
            {"id": source_id, "url": "https://example.com/pm"},
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, source_id, source_url, content_hash, text_hash, "
                "format, fetched_at, raw_bytes, text, title) VALUES (:id, :source_id, :url, "
                ":hash, :text_hash, 'html', now(), :raw, :text, :title)"
            ),
            {
                "id": document_id,
                "source_id": source_id,
                "url": "https://example.com/pm",
                "hash": uuid.uuid4().hex,
                "text_hash": uuid.uuid4().hex,
                "raw": b"<html/>",
                "text": text_body,
                "title": title,
            },
        )
    return document_id


def output_with_confidence(overrides: dict[str, float]) -> str:
    out = valid_output()
    out["confidence"].update(overrides)
    return json.dumps(out)


def complete_extraction(
    engine: Engine,
    *,
    document_id: uuid.UUID | None = None,
    confidence: dict[str, float] | None = None,
) -> uuid.UUID:
    """Run the real pipeline against the fake LLM and return the extraction
    id. Each call is its own run (fresh FakeLLMClient), so repeated calls on
    one document produce separate complete rows."""
    if document_id is None:
        document_id = insert_document(engine)
    client = FakeLLMClient([output_with_confidence(confidence or {})])
    with engine.begin() as conn:
        outcome = extract_document(conn, document_id=document_id, client=client, max_attempts=1)
    assert outcome.was_duplicate is False
    return outcome.extraction_id


def field_rows(engine: Engine, extraction_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM field_reviews WHERE extraction_id = :e"), {"e": extraction_id}
        ).mappings()
        return {r["field"]: dict(r) for r in rows}


def field_id(engine: Engine, extraction_id: uuid.UUID, field: str) -> uuid.UUID:
    row = field_rows(engine, extraction_id)[field]
    return row["id"]
