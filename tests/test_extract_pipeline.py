"""extract_document against real Postgres: complete rows, failed rows,
idempotency, and the metadata-title override. LLM is the scripted fake."""

import json
import uuid

import pytest
from sqlalchemy import Engine, text

from app.extract.errors import (
    PermanentExtractionError,
    SchemaValidationExhaustedError,
    TransientExtractionError,
)
from app.extract.pipeline import DocumentNotFoundError, extract_document
from app.extract.schema import SCHEMA_VERSION
from tests.fake_llm import FakeLLMClient, invalid_output_json, valid_output_json

MODEL = "fake-model"


def _insert_document(engine: Engine, *, title: str | None = "Outage Title") -> uuid.UUID:
    source_id, document_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO sources (id, url) VALUES (:id, :url)"),
            {"id": source_id, "url": "https://example.com/pm"},
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, source_id, source_url, content_hash, format, "
                "fetched_at, raw_bytes, text, title) VALUES (:id, :source_id, :url, :hash, "
                "'html', now(), :raw, :text, :title)"
            ),
            {
                "id": document_id,
                "source_id": source_id,
                "url": "https://example.com/pm",
                "hash": uuid.uuid4().hex,
                "raw": b"<html/>",
                "text": "The service was down. A change caused it.",
                "title": title,
            },
        )
    return document_id


def _rows(engine: Engine, document_id: uuid.UUID) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text("SELECT * FROM extractions WHERE document_id=:d ORDER BY created_at"),
                {"d": document_id},
            ).mappings()
        ]


def _extract(engine: Engine, document_id: uuid.UUID, client: FakeLLMClient, max_attempts: int = 3):  # type: ignore[no-untyped-def]
    """Mirror the worker: the failure is caught INSIDE the transaction so the
    failed `extractions` row commits alongside the job's status update. An
    exception escaping `engine.begin()` would roll the row back with it."""
    failure: BaseException | None = None
    with engine.begin() as conn:
        try:
            return extract_document(
                conn, document_id=document_id, client=client, max_attempts=max_attempts
            )
        except Exception as exc:  # noqa: BLE001 - re-raised below, after commit
            failure = exc
    assert failure is not None
    raise failure


def test_complete_extraction_is_persisted_with_confidence_and_derived(engine: Engine) -> None:
    document_id = _insert_document(engine)
    outcome = _extract(engine, document_id, FakeLLMClient([valid_output_json()]))

    assert outcome.was_duplicate is False
    rows = _rows(engine, document_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "complete"
    assert row["schema_version"] == SCHEMA_VERSION
    assert row["provider"] == "fake"  # from the client, not a caller argument
    assert row["model"] == MODEL
    assert row["record"]["mechanism"]["label"] == "crash_on_bad_input"
    assert row["record"]["trigger"]["label"] == "config_change"
    assert row["per_field_confidence"]["detection_method"] == 0.9
    assert row["confidence_source"] == "self_report"
    assert row["derived"]["time_to_detect"]["seconds"] == 180.0
    assert row["attempts"] == 1
    assert row["attempt_log"][0]["ok"] is True
    assert row["usage"] == {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    assert row["error"] is None


def test_metadata_title_overrides_the_model_title(engine: Engine) -> None:
    """FINDINGS §4.7: title comes from document metadata when the parser
    found one; the model's title is only the fallback."""
    document_id = _insert_document(engine, title="Cloudflare outage on November 18, 2025 - Blog")
    _extract(engine, document_id, FakeLLMClient([valid_output_json()]))
    row = _rows(engine, document_id)[0]
    assert row["record"]["title"] == "Cloudflare outage on November 18, 2025 - Blog"
    assert row["record"]["title_source"] == "document_metadata"


def test_synthesized_title_is_kept_when_document_has_none(engine: Engine) -> None:
    document_id = _insert_document(engine, title=None)
    _extract(engine, document_id, FakeLLMClient([valid_output_json()]))
    row = _rows(engine, document_id)[0]
    assert row["record"]["title"] == json.loads(valid_output_json())["record"]["title"]
    assert row["record"]["title_source"] == "synthesized"


def test_retry_then_success_records_both_attempts(engine: Engine) -> None:
    document_id = _insert_document(engine)
    outcome = _extract(
        engine, document_id, FakeLLMClient([invalid_output_json(), valid_output_json()])
    )
    assert outcome.attempts == 2
    row = _rows(engine, document_id)[0]
    assert row["status"] == "complete"
    assert [a["ok"] for a in row["attempt_log"]] == [False, True]
    assert "record.detection_method" in row["attempt_log"][0]["validation_error"]
    assert row["usage"]["input_tokens"] == 2000


def test_validation_exhausted_is_recorded_as_failed_and_reraised(engine: Engine) -> None:
    """PROJECT_BRIEF M3: failures recorded, not swallowed — both halves."""
    document_id = _insert_document(engine)
    with pytest.raises(SchemaValidationExhaustedError):
        _extract(engine, document_id, FakeLLMClient([invalid_output_json()] * 3))

    rows = _rows(engine, document_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "failed"
    assert row["record"] is None
    assert (row["provider"], row["model"]) == ("fake", MODEL)  # recorded on failures too
    assert row["error_kind"] == "permanent"
    assert "SchemaValidationExhaustedError" in row["error"]
    assert row["attempts"] == 3
    assert len(row["attempt_log"]) == 3
    assert all("detection_method" in a["validation_error"] for a in row["attempt_log"])


def test_transient_api_failure_is_recorded_as_failed_transient(engine: Engine) -> None:
    document_id = _insert_document(engine)
    client = FakeLLMClient([invalid_output_json(), TransientExtractionError("rate limited (429)")])
    with pytest.raises(TransientExtractionError):
        _extract(engine, document_id, client)
    row = _rows(engine, document_id)[0]
    assert row["status"] == "failed"
    assert row["error_kind"] == "transient"
    assert row["attempts"] == 1  # the invalid attempt before the 429 is kept


def test_unexpected_exception_is_recorded_then_reraised(engine: Engine) -> None:
    document_id = _insert_document(engine)
    with pytest.raises(RuntimeError, match="kaboom"):
        _extract(engine, document_id, FakeLLMClient([RuntimeError("kaboom")]))
    row = _rows(engine, document_id)[0]
    assert row["status"] == "failed"
    assert row["error_kind"] == "unexpected"
    assert row["attempt_log"] == []


def test_failed_rows_do_not_block_a_later_success(engine: Engine) -> None:
    document_id = _insert_document(engine)
    with pytest.raises(PermanentExtractionError):
        _extract(engine, document_id, FakeLLMClient([PermanentExtractionError("refused")]))
    _extract(engine, document_id, FakeLLMClient([valid_output_json()]))
    statuses = [r["status"] for r in _rows(engine, document_id)]
    assert statuses == ["failed", "complete"]


def test_complete_extraction_is_idempotent(engine: Engine) -> None:
    """At-least-once delivery (ADR-003 §4): a second run for the same
    document/schema/model is a no-op that calls no model."""
    document_id = _insert_document(engine)
    first = _extract(engine, document_id, FakeLLMClient([valid_output_json()]))
    second_client = FakeLLMClient([])
    second = _extract(engine, document_id, second_client)

    assert second.was_duplicate is True
    assert second.extraction_id == first.extraction_id
    assert second_client.calls == []
    assert len(_rows(engine, document_id)) == 1


def test_unique_index_enforces_one_complete_row_per_document_schema_provider_model(
    engine: Engine,
) -> None:
    document_id = _insert_document(engine)
    _extract(engine, document_id, FakeLLMClient([valid_output_json()]))
    with engine.begin() as conn, pytest.raises(Exception, match="ux_extractions_complete"):
        conn.execute(
            text(
                "INSERT INTO extractions (id, document_id, schema_version, provider, model, "
                "status, record, attempts, attempt_log, usage) "
                "VALUES (:id, :d, :v, 'fake', :m, 'complete', '{}', 1, '[]', '{}')"
            ),
            {"id": uuid.uuid4(), "d": document_id, "v": SCHEMA_VERSION, "m": MODEL},
        )


def test_same_model_on_another_provider_is_a_separate_extraction(engine: Engine) -> None:
    """The idempotency key includes the provider: a second provider serving
    the same model string gets its own complete row, not a no-op."""
    document_id = _insert_document(engine)
    _extract(engine, document_id, FakeLLMClient([valid_output_json()]))

    other = FakeLLMClient([valid_output_json()])
    other.provider = "other"
    outcome = _extract(engine, document_id, other)

    assert outcome.was_duplicate is False
    assert len(other.calls) == 1
    assert sorted(r["provider"] for r in _rows(engine, document_id)) == ["fake", "other"]


def test_missing_document_is_an_error(engine: Engine) -> None:
    with engine.begin() as conn, pytest.raises(DocumentNotFoundError):
        extract_document(conn, document_id=uuid.uuid4(), client=FakeLLMClient([]), max_attempts=1)
