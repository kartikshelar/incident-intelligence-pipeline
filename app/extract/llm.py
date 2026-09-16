"""Narrow LLM client interface, the provider registry, and the Anthropic
implementation.

Only this module imports the `anthropic` SDK. The extractor and pipeline
talk to an `LLMClient` (a Protocol) so tests inject a fake and the retry
loop is exercised without network, credentials, or cost. Every client
carries its own identity — `provider` and `model` — and the pipeline
stores both on the `extractions` row; nothing outside this module needs
to know which provider class is in use.

Adding a provider later: implement `LLMClient`, add a constructor to
`_PROVIDERS`, set APP_LLM_PROVIDER. Callers of `build_llm_client` do not
change.

Provider, model, thinking setting, and key are configuration
(app.settings), never constants here. The Anthropic adapter's job is:
build the structured-output request, and map SDK failures onto
Transient/PermanentExtractionError per shared/error-codes.md:

  429, >=500 (incl. 529 overloaded), connection error, timeout -> transient
  400/401/403/404/413 -> permanent (our request/credentials are wrong;
                          retrying the same job cannot fix it)
  stop_reason == "refusal"    -> permanent (documented, not retried)
  stop_reason == "max_tokens" -> permanent (output truncated; the same
                                 request would truncate again)

How the schema reaches the model: as text, appended to the cached system
block — NOT as `output_config.format = json_schema`. The first real run
(spike/extraction_run_01.json, 2026-09-15) had every request rejected with
400 "The compiled grammar is too large"; probing showed the v0.1 record
schema exceeds the grammar limit as soon as the five TimeAnchor fields
are present, with or without the confidence object. So the API's grammar
guarantee is unavailable for this schema, and schema conformance rests on
app.extract.extractor's validate-and-retry loop, which was built for
exactly that. The schema is static, so it caches with the system prompt.

Thinking is configuration too (APP_EXTRACTION_THINKING), because on
current models it is most of the bill: run 04 produced ~20k tokens of
visible JSON against ~80k billed output tokens, the rest being adaptive
thinking, which is what the API runs when no `thinking` parameter is
sent. The setting is one string, interpreted here and stored verbatim on
the extractions row (see `thinking_request_params`):

  default          send no `thinking` and no effort; the API's default
                   applies (adaptive thinking on Sonnet 5 — what runs
                   01-04 and 07 did)
  adaptive         thinking={"type": "adaptive"}
  disabled         thinking={"type": "disabled"}
  <mode>:<effort>  any of the above plus output_config={"effort": ...},
                   effort in low | medium | high | xhigh | max

The project default is `adaptive:low` (.env.example, docker-compose.yml;
since 2026-09-16, reasons in app/extract/schema.py's changelog). It is
still not a constant here: an unset value is a recorded configuration
error, not a silent fallback.

`budget_tokens` is not expressible: the API rejects it on Sonnet 5.

Non-streaming with the configured max_tokens (default 16000) stays under
the SDK's 10-minute timeout for the largest document in the spike corpus.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from typing import Any, Protocol

from app.extract.errors import PermanentExtractionError, TransientExtractionError
from app.settings import Settings

ANTHROPIC = "anthropic"

# The grammar of APP_EXTRACTION_THINKING for the Anthropic adapter (module
# docstring). `default` is a mode, not a default: the setting itself has
# no default in code, and an unset value is a recorded configuration error.
THINKING_MODES: tuple[str, ...] = ("default", "adaptive", "disabled")
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


def thinking_request_params(spec: str) -> dict[str, Any]:
    """Messages API parameters for one APP_EXTRACTION_THINKING value.

    Strict on purpose: the string is stored verbatim as part of the row's
    identity, so two spellings of one request must not both be accepted.
    """
    mode, has_effort, effort = spec.partition(":")
    if mode not in THINKING_MODES or (has_effort and effort not in EFFORT_LEVELS):
        raise PermanentExtractionError(
            f"APP_EXTRACTION_THINKING={spec!r} is not valid: expected one of "
            f"{', '.join(THINKING_MODES)}, optionally followed by ':' and one of "
            f"{', '.join(EFFORT_LEVELS)} (e.g. 'adaptive:low')"
        )
    params: dict[str, Any] = {}
    if mode != "default":
        params["thinking"] = {"type": mode}
    if has_effort:
        params["output_config"] = {"effort": effort}
    return params


@dataclasses.dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int

    def usage(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }


class LLMClient(Protocol):
    """One structured-output completion, plus the identity of what serves it.

    `provider` / `model` / `thinking` are what the pipeline records on each
    extraction row and keys idempotency on: the *configured* strings, not
    whatever the API echoes back (that goes in the attempt log).

    `messages` is the conversation so far (user/assistant alternation);
    `schema` is the JSON schema the response must satisfy. Raises
    TransientExtractionError / PermanentExtractionError; never returns a
    response whose text is not intended to be the JSON object.
    """

    provider: str
    model: str
    thinking: str

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]
    ) -> LLMResponse: ...


class AnthropicClient:
    """LLMClient backed by the Anthropic Messages API."""

    provider = ANTHROPIC

    def __init__(
        self,
        *,
        model: str,
        thinking: str,
        max_tokens: int = 16000,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.thinking = thinking
        # Validated here, not per request: a bad value is a configuration
        # error and fails the job with a recorded reason before any call.
        self._thinking_params = thinking_request_params(thinking)
        self.max_tokens = max_tokens
        if client is None:
            import anthropic

            try:
                # api_key=None lets the SDK fall back to its own resolution
                # (ANTHROPIC_AUTH_TOKEN, `ant auth login` profile).
                client = anthropic.Anthropic(api_key=api_key)
            except anthropic.AnthropicError as exc:
                # A configuration failure, not a retryable one. Recorded on
                # the extraction row so it's visible where it happened.
                raise PermanentExtractionError(f"anthropic client not configured: {exc}") from exc
        # Deliberately untyped: tests pass a real SDK client wired to a
        # mock HTTP transport.
        self._client: Any = client

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]
    ) -> LLMResponse:
        import anthropic

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                # Frozen system prompt + static schema first, cached; the
                # per-document message follows — see shared/prompt-caching.md.
                system=[
                    {
                        "type": "text",
                        "text": system_with_schema(system, schema),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
                # `thinking` / `output_config.effort`, or nothing (mode
                # `default`) — see thinking_request_params.
                **self._thinking_params,
            )
        except anthropic.RateLimitError as exc:
            raise TransientExtractionError(f"rate limited (429): {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientExtractionError(
                    f"server error ({exc.status_code} {exc.type}): {exc.message}"
                ) from exc
            raise PermanentExtractionError(
                f"request rejected ({exc.status_code} {exc.type}): {exc.message}"
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise TransientExtractionError(f"request timed out: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise TransientExtractionError(f"connection error: {exc}") from exc
        except (anthropic.AnthropicError, TypeError) as exc:
            # The SDK raises at request time (not construction) when it has
            # no credentials at all; a configuration problem, so permanent.
            raise PermanentExtractionError(f"anthropic client error: {exc}") from exc

        return response_to_llm_response(response)


def system_with_schema(system: str, schema: dict[str, Any]) -> str:
    """The frozen system prompt followed by the JSON schema the output must
    satisfy. Deterministic serialisation (sorted keys) so the cached
    prefix is byte-identical across requests."""
    return (
        f"{system}\n\n"
        "The JSON schema your output must satisfy (return exactly one JSON object, "
        "no prose, no code fence):\n"
        f"{json.dumps(schema, sort_keys=True)}"
    )


def response_to_llm_response(response: Any) -> LLMResponse:
    """Convert an SDK Message into LLMResponse, enforcing stop-reason rules."""
    usage = getattr(response, "usage", None)
    stop_reason = getattr(response, "stop_reason", None)
    text = "".join(
        block.text
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    )
    result = LLMResponse(
        text=text,
        model=getattr(response, "model", ""),
        stop_reason=stop_reason,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    )
    if stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None)
        raise PermanentExtractionError(f"model refused (category={category})")
    if stop_reason == "max_tokens":
        raise PermanentExtractionError(
            f"output truncated at max_tokens ({result.output_tokens} output tokens)"
        )
    return result


def _build_anthropic(settings: Settings, model: str, thinking: str) -> LLMClient:
    key = settings.anthropic_api_key
    return AnthropicClient(
        model=model,
        thinking=thinking,
        max_tokens=settings.extraction_max_tokens,
        api_key=key.get_secret_value() if key is not None else None,
    )


# Provider name (APP_LLM_PROVIDER) -> constructor (settings, model,
# thinking). The only place a provider is named; add a second entry here to
# add a second provider. Each provider interprets the thinking string its
# own way; the pipeline stores it verbatim either way.
_PROVIDERS: dict[str, Callable[[Settings, str, str], LLMClient]] = {
    ANTHROPIC: _build_anthropic,
}

SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(_PROVIDERS)


def build_llm_client(settings: Settings) -> LLMClient:
    """Construct the configured provider's client.

    Raises PermanentExtractionError for any configuration gap — unset or
    unknown provider, unset model, unset or invalid thinking setting,
    unusable credentials — so the worker records the reason on the
    extraction row and dead-letters the job.
    """
    if settings.llm_provider is None:
        raise PermanentExtractionError(
            f"APP_LLM_PROVIDER is not set (supported: {', '.join(SUPPORTED_PROVIDERS)})"
        )
    factory = _PROVIDERS.get(settings.llm_provider.lower())
    if factory is None:
        raise PermanentExtractionError(
            f"unknown LLM provider {settings.llm_provider!r} "
            f"(supported: {', '.join(SUPPORTED_PROVIDERS)})"
        )
    if settings.extraction_model is None:
        raise PermanentExtractionError("APP_EXTRACTION_MODEL is not set")
    if settings.extraction_thinking is None:
        raise PermanentExtractionError(
            "APP_EXTRACTION_THINKING is not set (for anthropic: one of "
            f"{', '.join(THINKING_MODES)}, optionally ':<effort>')"
        )
    return factory(settings, settings.extraction_model, settings.extraction_thinking)
