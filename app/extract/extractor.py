"""The validate-and-retry loop. No database, no SDK — pure orchestration.

PROJECT_BRIEF M3: "Structured output validated against the schema. Invalid
output retries with the validation error fed back." Concretely:

  attempt 1: user(document) -> model -> parse JSON -> validate
  attempt n: ... + assistant(previous output) + user(validation errors)

Every attempt — good or bad — is recorded as an `Attempt` with the raw
text, the validation error (if any), and token usage, and the list rides
along on both the success result and any raised ExtractionError, so the
pipeline can persist it either way. Nothing is swallowed: a transient API
error mid-loop propagates with the attempts made so far attached.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from pydantic import ValidationError

from app.extract.errors import ExtractionError, SchemaValidationExhaustedError
from app.extract.llm import LLMClient
from app.extract.prompt import (
    SYSTEM_PROMPT,
    build_retry_message,
    build_user_message,
    initial_messages,
)
from app.extract.schema import ExtractionOutput, format_validation_error, wire_schema


@dataclasses.dataclass(frozen=True)
class Attempt:
    number: int
    raw_output: str
    ok: bool
    validation_error: str | None
    usage: dict[str, int]
    model: str

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ExtractionResult:
    output: ExtractionOutput
    attempts: list[Attempt]
    model: str

    @property
    def usage_totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for attempt in self.attempts:
            for key, value in attempt.usage.items():
                totals[key] = totals.get(key, 0) + value
        return totals


def _strip_code_fence(raw: str) -> str:
    """Tolerate a ```json ... ``` wrapper. Without grammar-constrained output
    (see app.extract.llm) the model may fence the object; that is a
    transport artefact, not a schema violation worth an attempt."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline != -1 else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _validate(raw: str) -> tuple[ExtractionOutput | None, str | None]:
    try:
        data = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError as exc:
        return None, f"- <root>: output is not valid JSON ({exc.msg} at char {exc.pos})"
    try:
        return ExtractionOutput.model_validate(data), None
    except ValidationError as exc:
        return None, format_validation_error(exc)


def extract(
    *,
    document_text: str,
    document_title: str | None,
    source_url: str,
    client: LLMClient,
    max_attempts: int,
) -> ExtractionResult:
    """Run the loop. Returns on the first schema-valid output.

    Raises SchemaValidationExhaustedError after `max_attempts` invalid
    outputs, or whatever Transient/PermanentExtractionError the client
    raised — in both cases with `.attempts` populated.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    schema = wire_schema()
    messages = initial_messages(
        build_user_message(
            document_text=document_text, document_title=document_title, source_url=source_url
        )
    )
    attempts: list[Attempt] = []

    for number in range(1, max_attempts + 1):
        try:
            response = client.complete(system=SYSTEM_PROMPT, messages=messages, schema=schema)
        except ExtractionError as exc:
            exc.attempts = attempts + exc.attempts
            raise

        output, error = _validate(response.text)
        attempts.append(
            Attempt(
                number=number,
                raw_output=response.text,
                ok=output is not None,
                validation_error=error,
                usage=response.usage(),
                model=response.model,
            )
        )
        if output is not None:
            return ExtractionResult(output=output, attempts=attempts, model=response.model)

        # Feed the error back: the model sees its own output and the
        # validator's complaint, and is asked for the corrected object.
        messages = messages + [
            {"role": "assistant", "content": response.text},
            {"role": "user", "content": build_retry_message(error or "")},
        ]

    raise SchemaValidationExhaustedError(
        f"output failed schema validation on all {max_attempts} attempts; "
        f"last error:\n{attempts[-1].validation_error}",
        attempts=attempts,
    )
