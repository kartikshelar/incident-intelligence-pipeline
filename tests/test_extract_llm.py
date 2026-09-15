"""The Anthropic adapter, driven through the real SDK over a mocked HTTP
transport. Nothing here opens a socket: `httpx2.MockTransport` answers
every request in-process, so the wire format the SDK produces (body,
headers) and the way it parses responses and raises typed errors are all
exercised for real, without credentials or cost.
"""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic import DefaultHttpxClient

from app.extract.errors import PermanentExtractionError, TransientExtractionError
from app.extract.llm import (
    AnthropicClient,
    response_to_llm_response,
    system_with_schema,
    thinking_request_params,
)

MODEL = "some-configured-model"
THINKING = "default"
Handler = Callable[[httpx2.Request], httpx2.Response]


def _message_json(text: str, stop_reason: str = "end_turn", **usage: int) -> dict[str, Any]:
    """A Messages API response body, as the API sends it."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "model-as-echoed-by-api",
        "content": [
            {"type": "thinking", "thinking": "", "signature": ""},
            {"type": "text", "text": text},
        ],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("input_tokens", 10),
            "output_tokens": usage.get("output_tokens", 5),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        },
    }


def _error_json(err_type: str, message: str = "nope") -> dict[str, Any]:
    return {"type": "error", "error": {"type": err_type, "message": message}}


class _Transport:
    """Records the last request and answers with the scripted response."""

    def __init__(self, respond: Handler) -> None:
        self.respond = respond
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.respond(request)

    def last_body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def _adapter(
    respond: Handler, *, max_tokens: int = 1234, thinking: str = THINKING
) -> tuple[AnthropicClient, _Transport]:
    transport = _Transport(respond)
    sdk_client = anthropic.Anthropic(
        api_key="test-key-never-sent-anywhere",
        max_retries=0,  # the SDK would otherwise back off and retry 429/5xx in-test
        http_client=DefaultHttpxClient(transport=httpx2.MockTransport(transport)),
    )
    adapter = AnthropicClient(
        model=MODEL, thinking=thinking, max_tokens=max_tokens, client=sdk_client
    )
    return adapter, transport


def _json_response(status: int, body: dict[str, Any]) -> Handler:
    return lambda _request: httpx2.Response(status, json=body)


def _raise(exc: Exception) -> Handler:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        raise exc

    return handler


def test_request_wire_format_and_response_parsing() -> None:
    adapter, transport = _adapter(_json_response(200, _message_json('{"a": 1}', input_tokens=42)))
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}

    response = adapter.complete(
        system="SYSTEM", messages=[{"role": "user", "content": "hi"}], schema=schema
    )

    assert response.text == '{"a": 1}'
    assert response.model == "model-as-echoed-by-api"
    assert response.usage()["input_tokens"] == 42
    assert response.stop_reason == "end_turn"

    request = transport.requests[-1]
    assert request.url.path == "/v1/messages"
    assert request.headers["x-api-key"] == "test-key-never-sent-anywhere"
    body = transport.last_body()
    assert body["model"] == MODEL  # the configured string, verbatim
    assert body["max_tokens"] == 1234
    # The schema travels as text in the cached system block, not as a
    # grammar: the v0.1 schema exceeds the API's grammar limit (run 01).
    assert "output_config" not in body
    (system_block,) = body["system"]
    assert system_block["cache_control"] == {"type": "ephemeral"}
    assert system_block["text"] == system_with_schema("SYSTEM", schema)
    assert system_block["text"].startswith("SYSTEM\n\n")
    assert json.dumps(schema, sort_keys=True) in system_block["text"]
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert "thinking" not in body  # mode `default`: the API decides; nothing sent


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("default", {}),
        ("adaptive", {"thinking": {"type": "adaptive"}}),
        ("disabled", {"thinking": {"type": "disabled"}}),
        ("adaptive:low", {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}),
        ("disabled:high", {"thinking": {"type": "disabled"}, "output_config": {"effort": "high"}}),
        ("default:medium", {"output_config": {"effort": "medium"}}),
        ("adaptive:max", {"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}}),
    ],
)
def test_thinking_setting_maps_onto_the_request(spec: str, expected: dict[str, Any]) -> None:
    """APP_EXTRACTION_THINKING is one string; the adapter turns it into
    `thinking` and/or `output_config.effort`, or into nothing at all."""
    assert thinking_request_params(spec) == expected

    adapter, transport = _adapter(_json_response(200, _message_json("{}")), thinking=spec)
    adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})
    body = transport.last_body()
    for key in ("thinking", "output_config"):
        assert body.get(key) == expected.get(key), key
    assert "format" not in body.get("output_config", {})  # still no grammar (ADR-005)


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "enabled",
        "ADAPTIVE",  # stored verbatim as identity, so spellings are not folded
        "adaptive:",
        "adaptive:ultra",
        "adaptive:low:extra",
        "budget:1024",  # budget_tokens is rejected by the API on Sonnet 5 anyway
        "low",
    ],
)
def test_invalid_thinking_setting_fails_at_construction_not_per_request(spec: str) -> None:
    transport = _Transport(_json_response(200, _message_json("{}")))
    sdk_client = anthropic.Anthropic(
        api_key="k", http_client=DefaultHttpxClient(transport=httpx2.MockTransport(transport))
    )
    with pytest.raises(PermanentExtractionError, match="APP_EXTRACTION_THINKING"):
        AnthropicClient(model=MODEL, thinking=spec, client=sdk_client)
    assert transport.requests == []


def test_system_with_schema_is_byte_stable_for_caching() -> None:
    a = system_with_schema("S", {"b": 1, "a": {"y": 2, "x": 1}})
    b = system_with_schema("S", {"a": {"x": 1, "y": 2}, "b": 1})
    assert a == b


def test_client_identity_is_provider_configured_model_and_thinking() -> None:
    adapter, _ = _adapter(_json_response(200, _message_json("{}")), thinking="adaptive:low")
    assert adapter.provider == "anthropic"
    assert adapter.model == MODEL
    assert adapter.thinking == "adaptive:low"


@pytest.mark.parametrize(
    ("status", "err_type", "expected"),
    [
        (429, "rate_limit_error", TransientExtractionError),
        (500, "api_error", TransientExtractionError),
        (529, "overloaded_error", TransientExtractionError),
        (400, "invalid_request_error", PermanentExtractionError),
        (401, "authentication_error", PermanentExtractionError),
        (403, "permission_error", PermanentExtractionError),
        (404, "not_found_error", PermanentExtractionError),
    ],
)
def test_http_errors_are_classified(
    status: int, err_type: str, expected: type[Exception]
) -> None:
    adapter, _ = _adapter(_json_response(status, _error_json(err_type)))
    with pytest.raises(expected, match=err_type):
        adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})


@pytest.mark.parametrize(
    "exc",
    [httpx2.ConnectError("refused"), httpx2.ReadTimeout("slow"), httpx2.ConnectTimeout("slow")],
)
def test_connection_and_timeout_errors_are_transient(exc: Exception) -> None:
    adapter, _ = _adapter(_raise(exc))
    with pytest.raises(TransientExtractionError):
        adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})


def test_missing_credentials_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key passed and none in the environment: the SDK fails at request
    time and the adapter records that as a configuration (permanent) error."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    transport = _Transport(_json_response(200, _message_json("{}")))
    sdk_client = anthropic.Anthropic(
        api_key=None,
        max_retries=0,
        http_client=DefaultHttpxClient(transport=httpx2.MockTransport(transport)),
    )
    adapter = AnthropicClient(model=MODEL, thinking=THINKING, client=sdk_client)
    with pytest.raises(PermanentExtractionError):
        adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})
    assert transport.requests == []  # never reached the wire


def test_refusal_is_permanent_and_names_the_category() -> None:
    body = _message_json("", stop_reason="refusal")
    body["stop_details"] = {"type": "refusal", "category": "cyber", "explanation": "..."}
    adapter, _ = _adapter(_json_response(200, body))
    with pytest.raises(PermanentExtractionError, match="category=cyber"):
        adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})


def test_truncated_output_is_permanent() -> None:
    body = _message_json('{"partial', stop_reason="max_tokens", output_tokens=16000)
    adapter, _ = _adapter(_json_response(200, body))
    with pytest.raises(PermanentExtractionError, match="max_tokens"):
        adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})


def test_only_text_blocks_are_concatenated() -> None:
    adapter, _ = _adapter(_json_response(200, _message_json('{"x": 1}')))
    response = adapter.complete(system="s", messages=[{"role": "user", "content": "x"}], schema={})
    assert response.text == '{"x": 1}'


def test_response_conversion_tolerates_missing_usage_fields() -> None:
    class _Block:
        type = "text"
        text = "{}"

    class _Msg:
        content = [_Block()]
        model = "m"
        stop_reason = "end_turn"
        usage = None

    converted = response_to_llm_response(_Msg())
    assert converted.usage() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
