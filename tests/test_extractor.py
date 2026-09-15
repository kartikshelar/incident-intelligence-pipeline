"""The validate-and-retry loop, with a scripted fake model. No network."""

import json

import pytest

from app.extract.errors import (
    PermanentExtractionError,
    SchemaValidationExhaustedError,
    TransientExtractionError,
)
from app.extract.extractor import extract
from app.extract.prompt import SYSTEM_PROMPT
from tests.fake_llm import (
    FakeLLMClient,
    invalid_output_json,
    overlong_description_output_json,
    valid_output_json,
)

DOC = "The service was down. A change caused it. Monitoring caught it."


def _run(client: FakeLLMClient, max_attempts: int = 3):  # type: ignore[no-untyped-def]
    return extract(
        document_text=DOC,
        document_title="Some Outage",
        source_url="https://example.com/pm",
        client=client,
        max_attempts=max_attempts,
    )


def test_valid_output_succeeds_first_try() -> None:
    client = FakeLLMClient([valid_output_json()])
    result = _run(client)
    assert len(result.attempts) == 1
    assert result.attempts[0].ok is True
    assert result.output.record.detection_method == "monitoring"
    assert result.usage_totals["input_tokens"] == 1000

    call = client.calls[0]
    assert call["system"] == SYSTEM_PROMPT
    assert len(call["messages"]) == 1
    assert "https://example.com/pm" in call["messages"][0]["content"]
    assert '"document_title": "Some Outage"' in call["messages"][0]["content"]
    assert DOC in call["messages"][0]["content"]
    assert call["schema"]["type"] == "object"


def test_invalid_output_is_retried_with_the_validation_error_fed_back() -> None:
    client = FakeLLMClient([invalid_output_json(), valid_output_json()])
    result = _run(client)

    assert len(client.calls) == 2
    assert [a.ok for a in result.attempts] == [False, True]
    assert result.attempts[0].validation_error is not None
    assert "record.detection_method" in result.attempts[0].validation_error

    retry_messages = client.calls[1]["messages"]
    assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
    assert retry_messages[1]["content"] == invalid_output_json()  # the model sees its own output
    assert "record.detection_method" in retry_messages[2]["content"]  # ...and the error
    assert "failed schema validation" in retry_messages[2]["content"]


def test_overlong_written_description_is_retried_with_the_cap_fed_back() -> None:
    """Schema v0.3: a description over the cap is a validation failure like
    any other — it costs an attempt and the model is told which field and
    what the limit is."""
    client = FakeLLMClient([overlong_description_output_json(), valid_output_json()])
    result = _run(client)

    assert [a.ok for a in result.attempts] == [False, True]
    error = result.attempts[0].validation_error or ""
    assert "record.mechanism.description" in error
    assert "at most 200 characters" in error
    assert "record.mechanism.description" in client.calls[1]["messages"][2]["content"]


def test_non_json_output_is_fed_back_as_a_parse_error() -> None:
    client = FakeLLMClient(["not json at all", valid_output_json()])
    result = _run(client)
    assert result.attempts[0].validation_error is not None
    assert "not valid JSON" in result.attempts[0].validation_error
    assert "not valid JSON" in client.calls[1]["messages"][2]["content"]


@pytest.mark.parametrize(
    "wrapped",
    [
        "```json\n{body}\n```",
        "```\n{body}\n```",
        "  ```json\n{body}\n```  \n",
    ],
)
def test_code_fenced_output_is_accepted_without_spending_an_attempt(wrapped: str) -> None:
    client = FakeLLMClient([wrapped.format(body=valid_output_json())])
    result = _run(client)
    assert len(result.attempts) == 1
    assert result.attempts[0].ok is True


def test_exhausting_attempts_is_a_permanent_error_carrying_every_attempt() -> None:
    client = FakeLLMClient([invalid_output_json()] * 3)
    with pytest.raises(SchemaValidationExhaustedError) as exc:
        _run(client, max_attempts=3)
    assert len(client.calls) == 3
    assert len(exc.value.attempts) == 3
    assert all(a.ok is False for a in exc.value.attempts)
    assert isinstance(exc.value, PermanentExtractionError)
    assert "all 3 attempts" in str(exc.value)


def test_transient_api_error_propagates_with_attempts_so_far() -> None:
    client = FakeLLMClient([invalid_output_json(), TransientExtractionError("rate limited (429)")])
    with pytest.raises(TransientExtractionError) as exc:
        _run(client)
    assert len(exc.value.attempts) == 1  # the invalid attempt is not lost
    assert exc.value.attempts[0].ok is False


def test_permanent_api_error_propagates_unchanged() -> None:
    client = FakeLLMClient([PermanentExtractionError("model refused (category=None)")])
    with pytest.raises(PermanentExtractionError, match="refused"):
        _run(client)


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError):
        _run(FakeLLMClient([]), max_attempts=0)


def test_attempt_log_is_json_serialisable() -> None:
    client = FakeLLMClient([invalid_output_json(), valid_output_json()])
    result = _run(client)
    dumped = json.dumps([a.to_json() for a in result.attempts])
    assert '"number": 1' in dumped and '"number": 2' in dumped
