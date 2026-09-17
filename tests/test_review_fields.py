"""field_reviews rows: created with every complete extraction (ADR-010 §1)."""

import uuid

import pytest
from sqlalchemy import Engine, text

from app.extract.pipeline import extract_document
from app.extract.schema import RECORD_FIELDS
from tests.fake_llm import FakeLLMClient, invalid_output_json, valid_output_json
from tests.review_support import complete_extraction, field_rows, insert_document


def test_every_top_level_field_gets_an_unreviewed_row(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.55, "vendor_org": 0.4})
    rows = field_rows(engine, extraction_id)

    assert set(rows) == set(RECORD_FIELDS)
    assert len(rows) == 23
    assert {r["review_state"] for r in rows.values()} == {"unreviewed"}
    assert all(r["decision"] is None and r["reviewer"] is None for r in rows.values())
    # Confidence is the self-reported number for that field.
    assert rows["trigger"]["confidence"] == 0.55
    assert rows["vendor_org"]["confidence"] == 0.4
    assert rows["mechanism"]["confidence"] == 0.9


def test_model_value_is_the_records_value_including_json_null(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    rows = field_rows(engine, extraction_id)
    with engine.connect() as conn:
        record = conn.execute(
            text("SELECT record FROM extractions WHERE id = :id"), {"id": extraction_id}
        ).scalar_one()

    for field in RECORD_FIELDS:
        assert rows[field]["model_value"] == record[field], field
    # A null field is JSON null (the value), never SQL NULL (no value).
    assert rows["vendor_org"]["model_value"] is None
    with engine.connect() as conn:
        sql_null = conn.execute(
            text(
                "SELECT model_value IS NULL, model_value = 'null'::jsonb FROM field_reviews "
                "WHERE extraction_id = :e AND field = 'vendor_org'"
            ),
            {"e": extraction_id},
        ).one()
    assert tuple(sql_null) == (False, True)


def test_failed_extraction_creates_no_review_rows(engine: Engine) -> None:
    document_id = insert_document(engine)
    with engine.begin() as conn:
        try:
            extract_document(
                conn,
                document_id=document_id,
                client=FakeLLMClient([invalid_output_json()]),
                max_attempts=1,
            )
        except Exception:  # noqa: BLE001 - the failed row is what we are checking
            pass
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM field_reviews")).scalar_one()
        failed = conn.execute(
            text("SELECT count(*) FROM extractions WHERE status = 'failed'")
        ).scalar_one()
    assert (n, failed) == (0, 1)


def test_review_rows_commit_with_the_extraction_and_not_without_it(engine: Engine) -> None:
    """Same transaction: a rolled-back extraction leaves no orphan states."""
    document_id = insert_document(engine)
    with engine.connect() as conn:
        with conn.begin() as tx:
            extract_document(
                conn,
                document_id=document_id,
                client=FakeLLMClient([valid_output_json()]),
                max_attempts=1,
            )
            tx.rollback()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM field_reviews")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM extractions")).scalar_one() == 0


def test_each_run_has_its_own_review_rows(engine: Engine) -> None:
    """ADR-010 §6: review state belongs to the extraction that was reviewed;
    a second run of the same document has its own, unreviewed."""
    document_id = insert_document(engine)
    first = complete_extraction(engine, document_id=document_id)
    second = complete_extraction(engine, document_id=document_id)
    assert first != second
    assert len(field_rows(engine, first)) == 23
    assert len(field_rows(engine, second)) == 23


def test_one_row_per_field_per_extraction(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    with engine.begin() as conn, pytest.raises(Exception, match="field_reviews_extraction_field"):
        conn.execute(
            text(
                "INSERT INTO field_reviews (id, extraction_id, field, confidence, model_value) "
                "VALUES (:id, :e, 'trigger', 0.5, 'null'::jsonb)"
            ),
            {"id": uuid.uuid4(), "e": extraction_id},
        )
