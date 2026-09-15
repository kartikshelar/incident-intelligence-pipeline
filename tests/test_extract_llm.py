"""The Anthropic adapter: request shape and error classification, using a
stub in place of the SDK client. No network, no credentials."""

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.extract.errors import PermanentExtractionError, TransientExtractionError
from app.extract.llm import AnthropicClient, response_to_llm_response


def _message(text: str, stop_reason: str = "end_turn", **usage: int) -> SimpleNamespace:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        model="claude-opus-5",
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=usage.get("input_tokens", 10),
            output_tokens=usage.get("output_tokens", 5),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
        ),
    )


class _StubMessages:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _adapter(outcome: Any) -> tuple[AnthropicClient, _StubMessages]:
    messages = _StubMessages(outcome)
    stub = SimpleNamespace(messages=messages)
    return AnthropicClient(model="claude-opus-5", max_tokens=1234, client=stub), messages


_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls: type[anthropic.APIStatusError], status: int, err_type: str) -> Any:
    # A real SDK status error, built the way the SDK builds it (anthropic
    # 1.x is on httpx2, not httpx).
    return cls(
        f"{status} {err_type}",
        response=httpx2.Response(status, request=_REQUEST),
        body={"type": "error", "error": {"type": err_type, "message": err_type}},
    )


def test_request_uses_structured_output_and_cached_system_prompt() -> None:
    adapter, messages = _adapter(_message('{"a": 1}'))
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    response = adapter.complete(
        system="SYSTEM", messages=[{"role": "user", "content": "hi"}], schema=schema
    )

    assert response.text == '{"a": 1}'
    assert response.model == "claude-opus-5"
    assert response.usage()["input_tokens"] == 10
    assert messages.kwargs is not None
    assert messages.kwargs["model"] == "claude-opus-5"
    assert messages.kwargs["max_tokens"] == 1234
    assert messages.kwargs["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert messages.kwargs["system"][0]["text"] == "SYSTEM"
    assert messages.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in messages.kwargs  # adaptive by default on claude-opus-5


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            _status_error(anthropic.RateLimitError, 429, "rate_limit_error"),
            TransientExtractionError,
        ),
        (_status_error(anthropic.InternalServerError, 500, "api_error"), TransientExtractionError),
        (
            _status_error(anthropic.InternalServerError, 529, "overloaded_error"),
            TransientExtractionError,
        ),
        (
            _status_error(anthropic.BadRequestError, 400, "invalid_request_error"),
            PermanentExtractionError,
        ),
        (
            _status_error(anthropic.AuthenticationError, 401, "authentication_error"),
            PermanentExtractionError,
        ),
        (_status_error(anthropic.NotFoundError, 404, "not_found_error"), PermanentExtractionError),
        (TypeError("Could not resolve authentication method"), PermanentExtractionError),
    ],
)
def test_sdk_errors_are_classified(exc: BaseException, expected: type[Exception]) -> None:
    adapter, _ = _adapter(exc)
    with pytest.raises(expected):
        adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})


@pytest.mark.parametrize(
    "exc",
    [
        anthropic.APIConnectionError(request=_REQUEST),
        anthropic.APITimeoutError(request=_REQUEST),
    ],
)
def test_connection_and_timeout_errors_are_transient(exc: BaseException) -> None:
    adapter, _ = _adapter(exc)
    with pytest.raises(TransientExtractionError):
        adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})


def test_missing_credentials_is_permanent() -> None:
    adapter, _ = _adapter(anthropic.CredentialsError("no credentials found"))
    with pytest.raises(PermanentExtractionError, match="no credentials"):
        adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})


def test_refusal_is_permanent_and_names_the_category() -> None:
    message = _message("", stop_reason="refusal")
    message.stop_details = SimpleNamespace(category="cyber", explanation="...")
    with pytest.raises(PermanentExtractionError, match="category=cyber"):
        response_to_llm_response(message)


def test_truncated_output_is_permanent() -> None:
    with pytest.raises(PermanentExtractionError, match="max_tokens"):
        response_to_llm_response(
            _message('{"partial', stop_reason="max_tokens", output_tokens=16000)
        )


def test_only_text_blocks_are_concatenated() -> None:
    response = response_to_llm_response(_message('{"x": 1}'))
    assert response.text == '{"x": 1}'
