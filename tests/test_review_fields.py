"""field_reviews rows: created with every complete extraction, and the
three reviewer actions with their write-back rules (ADR-010 §1, §6)."""

import uuid

import pytest
from sqlalchemy import Engine, text

from app.extract.pipeline import extract_document
from app.extract.schema import RECORD_FIELDS
from app.review.fields import (
    AlreadyReviewedError,
    FieldReviewNotFoundError,
    InvalidCorrectionError,
    NotRoutedError,
    get_field_review,
    record_decision,
    validate_correction,
)
from app.review.routing import route_pending
from tests.fake_llm import FakeLLMClient, invalid_output_json, valid_output_json
from tests.review_support import complete_extraction, field_id, field_rows, insert_document


def _route_all(engine: Engine) -> None:
    with engine.begin() as conn:
        route_pending(conn, floor=1.01, budget=None)


# --- creation --------------------------------------------------------------


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


# --- accept ----------------------------------------------------------------


def test_accept_marks_reviewed_with_reviewer_and_time(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5})
    _route_all(engine)
    fid = field_id(engine, extraction_id, "trigger")

    with engine.begin() as conn:
        review = record_decision(conn, fid, action="accept", reviewer="kartik", note="looks right")

    assert review.review_state == "reviewed"
    assert review.decision == "accepted"
    assert review.reviewer == "kartik"
    assert review.reviewer_note == "looks right"
    assert review.reviewed_at is not None
    assert review.routed_at is not None  # it was routed; that history is kept
    assert review.corrected_value is None
    assert review.model_value["label"] == "config_change"  # untouched


def test_accept_and_correct_require_a_reviewer(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    fid = field_id(engine, extraction_id, "trigger")
    for action in ("accept", "correct"):
        with engine.begin() as conn, pytest.raises(InvalidCorrectionError, match="reviewer"):
            record_decision(conn, fid, action=action, reviewer="  ", corrected_value=None)  # type: ignore[arg-type]
    assert get_field_review_state(engine, fid) == "unreviewed"


def get_field_review_state(engine: Engine, fid: uuid.UUID) -> str:
    with engine.connect() as conn:
        return get_field_review(conn, fid).review_state


# --- correct ---------------------------------------------------------------


def test_correct_stores_the_human_value_next_to_the_models(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"detection_method": 0.5})
    fid = field_id(engine, extraction_id, "detection_method")

    with engine.begin() as conn:
        review = record_decision(
            conn, fid, action="correct", reviewer="kartik", corrected_value="customer_report"
        )

    assert review.review_state == "reviewed"
    assert review.decision == "corrected"
    assert review.model_value == "monitoring"  # the model's answer, kept
    assert review.corrected_value == "customer_report"  # the human's, alongside
    with engine.connect() as conn:
        record = conn.execute(
            text("SELECT record FROM extractions WHERE id = :id"), {"id": extraction_id}
        ).scalar_one()
    assert record["detection_method"] == "monitoring"  # extractions.record never overwritten


def test_correct_validates_against_the_fields_schema(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    fid = field_id(engine, extraction_id, "detection_method")
    with engine.begin() as conn, pytest.raises(InvalidCorrectionError, match="detection_method"):
        record_decision(conn, fid, action="correct", reviewer="k", corrected_value="automated")
    assert get_field_review_state(engine, fid) == "unreviewed"


def test_correct_validates_structured_fields(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    fid = field_id(engine, extraction_id, "mechanism")
    # Missing `quote`, and a label outside the closed enum.
    with engine.begin() as conn, pytest.raises(InvalidCorrectionError, match="mechanism"):
        record_decision(
            conn,
            fid,
            action="correct",
            reviewer="k",
            corrected_value={"label": "crash_on_bad_input", "description": "It crashed."},
        )
    with engine.begin() as conn:
        review = record_decision(
            conn,
            fid,
            action="correct",
            reviewer="k",
            corrected_value={
                "label": "software_defect",
                "description": "A latent bug in the proxy surfaced.",
                "quote": None,
            },
        )
    assert review.corrected_value["label"] == "software_defect"
    assert review.model_value["label"] == "limit_violation"


def test_correct_to_null_is_a_real_correction(engine: Engine) -> None:
    """A nullable field corrected to null stores JSON null, which the
    check constraint distinguishes from 'no correction' (SQL NULL)."""
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5})
    fid = field_id(engine, extraction_id, "trigger")
    with engine.begin() as conn:
        review = record_decision(conn, fid, action="correct", reviewer="k", corrected_value=None)
    assert review.decision == "corrected"
    assert review.corrected_value is None
    with engine.connect() as conn:
        is_json_null = conn.execute(
            text("SELECT corrected_value = 'null'::jsonb FROM field_reviews WHERE id = :id"),
            {"id": fid},
        ).scalar_one()
    assert is_json_null is True


def test_correction_equal_to_the_model_value_is_refused(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    fid = field_id(engine, extraction_id, "detection_method")
    with engine.begin() as conn, pytest.raises(InvalidCorrectionError, match="use accept"):
        record_decision(conn, fid, action="correct", reviewer="k", corrected_value="monitoring")


def test_validate_correction_normalises_to_json(engine: Engine) -> None:
    value = validate_correction("change_at", None)
    assert value is None
    value = validate_correction(
        "mitigations", [{"text": "Rolled back", "status": "done", "date": "2025-11-18"}]
    )
    assert value == [{"text": "Rolled back", "status": "done", "date": "2025-11-18"}]
    with pytest.raises(InvalidCorrectionError, match="not an IncidentRecord field"):
        validate_correction("nope", 1)


# --- once reviewed ---------------------------------------------------------


def test_a_reviewed_field_is_not_reviewed_again(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5})
    _route_all(engine)
    fid = field_id(engine, extraction_id, "trigger")
    with engine.begin() as conn:
        record_decision(conn, fid, action="accept", reviewer="a")
    for action, value in (("accept", None), ("correct", None), ("skip", None)):
        with engine.begin() as conn, pytest.raises(AlreadyReviewedError):
            record_decision(conn, fid, action=action, reviewer="b", corrected_value=value)  # type: ignore[arg-type]


def test_a_reviewed_field_is_never_re_routed(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.3})
    fid = field_id(engine, extraction_id, "trigger")
    with engine.begin() as conn:
        record_decision(conn, fid, action="accept", reviewer="a")
    with engine.begin() as conn:
        result = route_pending(conn, floor=0.99, budget=None)
    assert fid not in {f.id for f in result.routed}
    assert get_field_review_state(engine, fid) == "reviewed"


# --- skip ------------------------------------------------------------------


def test_skip_keeps_the_field_routed_and_counts(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5})
    _route_all(engine)
    fid = field_id(engine, extraction_id, "trigger")
    with engine.begin() as conn:
        review = record_decision(conn, fid, action="skip")
    assert review.review_state == "routed"
    assert review.skip_count == 1
    assert review.last_skipped_at is not None
    assert review.decision is None


def test_skip_needs_a_routed_field(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    fid = field_id(engine, extraction_id, "trigger")
    with engine.begin() as conn, pytest.raises(NotRoutedError):
        record_decision(conn, fid, action="skip")


def test_unknown_field_review(engine: Engine) -> None:
    with engine.begin() as conn, pytest.raises(FieldReviewNotFoundError):
        record_decision(conn, uuid.uuid4(), action="accept", reviewer="k")
