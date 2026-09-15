"""Narrow LLM client interface and its Anthropic implementation.

Only this module imports the `anthropic` SDK. The extractor talks to an
`LLMClient` (a Protocol) so tests inject a fake and the retry loop is
exercised without network, credentials, or cost. The Anthropic adapter's
job is: build the structured-output request, and map SDK failures onto
Transient/PermanentExtractionError per shared/error-codes.md:

  429, >=500 (incl. 529 overloaded), connection error, timeout -> transient
  400/401/403/404/413 -> permanent (our request/credentials are wrong;
                          retrying the same job cannot fix it)
  stop_reason == "refusal"    -> permanent (documented, not retried)
  stop_reason == "max_tokens" -> permanent (output truncated; the same
                                 request would truncate again)

Model choice: `claude-opus-5` by default (settings.extraction_model).
Thinking is adaptive by default on this model, so no `thinking` parameter
is sent. Non-streaming with max_tokens=16000 stays under the SDK's 10-minute
timeout for the largest document in the spike corpus.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Protocol

from app.extract.errors import PermanentExtractionError, TransientExtractionError


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
    """One structured-output completion.

    `messages` is the conversation so far (user/assistant alternation);
    `schema` is the JSON schema the response must satisfy. Raises
    TransientExtractionError / PermanentExtractionError; never returns a
    response whose text is not intended to be the JSON object.
    """

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]
    ) -> LLMResponse: ...


class AnthropicClient:
    """LLMClient backed by the Anthropic Messages API."""

    def __init__(self, *, model: str, max_tokens: int = 16000, client: Any | None = None) -> None:
        self.model = model
        self.max_tokens = max_tokens
        if client is None:
            import anthropic

            try:
                client = anthropic.Anthropic()
            except anthropic.AnthropicError as exc:
                # No API key / profile: a configuration failure, not a
                # retryable one. Recorded on the extraction row so it's
                # visible where it happened rather than logged and lost.
                raise PermanentExtractionError(f"anthropic client not configured: {exc}") from exc
        # Deliberately untyped: tests substitute a stub with the same
        # `.messages.create` surface.
        self._client: Any = client

    def complete(
        self, *, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]
    ) -> LLMResponse:
        import anthropic

        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                # Frozen system prompt first, cached; the per-document
                # message follows — see shared/prompt-caching.md.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": schema}},
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
