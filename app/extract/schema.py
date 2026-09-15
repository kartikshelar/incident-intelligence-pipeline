"""Incident record schema v0.3 — the extraction contract.

This is PROJECT_BRIEF §6's draft schema after the corrections the M0 spike
demanded. Every departure from the draft cites its source:

  ADR-001   `trigger_class` -> `trigger` (nullable: initiating change/event)
            + `mechanism` (required, single-valued: what actually failed).
            `change_induced` is REMOVED — derivable as `trigger is not None`.
            Class *values* for both are still open (ADR-001 defers to 01b),
            see app/extract/taxonomy.py.
  ADR-002   `detection_method` enum: monitoring | customer_report |
            internal_manual | operator | ambiguous | unknown. Single-valued;
            means the first signal that caused the org to acknowledge the
            incident, not the earliest reconstructable symptom.
  §4.1      `occurred_at` -> five typed, nullable time anchors, each with a
            precision marker and the original timezone string.
  §4.2      `time_to_detect` / `time_to_mitigate` are NOT extracted. They
            are derived from the anchors (app/extract/derive.py). Only the
            author's own phrase ("within minutes") is captured, as text.
  §4.5      `affected_services text[]` -> `affected` with services, regions,
            an all-services flag, and `list_is_complete` (nullable: the
            document may not say).
  §4.6      `org` -> `publisher_org` / `affected_org` / `vendor_org` +
            `affected_org_kind` (company vs OSS project).
  §4.7      `title` carries `title_source`: document metadata when the
            parser found one, synthesised only as a fallback.
  §4.8      `contributing_factors[].normalized_class` dropped (resolved by
            ADR-001's reading: a factor taxonomy is a second DERIVE-01 and
            does not exist); `source_section` added.
  §4.9      `remediation_actions text[]` -> `mitigations` (during) and
            `remediations` (after), each with status and optional date.
  §4.10     `blast_radius` kept as designed; `quantitative` is a list of
            typed quantities so absent-vs-wrong can be scored separately.

Changelog
---------
v0.1 (2026-09-14): first extraction run that produced records (run 02,
10/10, mean 1.20 validation attempts, 75,927 output tokens, $0.96).

v0.2 (2026-09-15): same shape; every description the model READS (field
descriptions and the class docstrings Pydantic emits as object
descriptions) was cut to one sentence to shrink the cached schema block.
MEASURED NEGATIVE RESULT (run 03, same corpus, same prompt): the cached
block shrank 5,532 -> 5,198 tokens, mean validation attempts rose
1.20 -> 1.30, output tokens 75,927 -> 76,526, cost $0.96 -> $0.99. A
validation retry resends the whole document, so one extra retry costs
more than the 334 cache-read tokens per request the trim saved. It also
targeted the cheap side: cache reads are 1/50 the price of output tokens.
Reverted in v0.3; ADR-005 §4 records it.

v0.3 (2026-09-15): descriptions the model reads are back to the v0.1
wording. The constraint moved to the text the model WRITES:
`trigger.description`, `mechanism.description` and each
`contributing_factors[].text` must be one sentence of at most
MAX_DESCRIPTION_CHARS characters — a field constraint (max_length) plus a
sentence-count validator, so an over-long value fails validation and is
retried with the error fed back. Every `quote` field is untouched: quotes
are provenance, not prose. Bumped because the validation contract changed
and the version is part of the extraction idempotency key.
MEASURED (run 04, same corpus and prompt): written descriptions
11,442 -> 8,713 chars, but 3 of 4 retries were the cap itself firing on
`mechanism.description`; mean attempts 1.30 -> 1.40, output tokens
76,526 -> 80,412, cost $0.99 -> $1.04. Also a net loss. The visible JSON
is ~20k tokens per run against ~78k billed output tokens: the rest is
adaptive thinking (no `thinking` parameter is sent, see llm.py), which no
schema change can reach.

OPEN, deliberately not decided here (FINDINGS §4.11, §4.12): one record per
DOCUMENT. Nothing in this schema links two documents to one incident, and a
document describing several impact periods yields one record for the
primary incident. The `extractions` table keys on `document_id`; there is
no `incident_id`. Whichever way that is resolved is a schema decision for
Kartik, not an extraction decision. (Document *identity* — which fetches
are the same document — is ADR-007 and lives in the ingest layer.)

Confidence: `FieldConfidence` holds one self-reported number per top-level
record field. It is CAPTURED here and NOT thresholded — routing is M4, and
whether self-report is even the right confidence source is DERIVE-05, to be
measured in M5. `confidence_source` on the stored row says where the
numbers came from so M5 can compare sources.

Schema-shape rules kept from the structured-output design (ADR-005): every
object forbids extra keys, every field is required (nullable fields are
`X | None`, never defaulted), no recursion. Client-side-only constraints
(min/max length, ranges, patterns) are kept here for validation and
stripped from the wire schema; where the model needs to know a limit it
is stated in the field's description text.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from app.extract.taxonomy import DetectionMethod, MechanismClass, TriggerClass

SCHEMA_VERSION = "0.3"

# Upper bound on the free-text descriptions the model writes (v0.3). Run 03
# medians were ~178 characters with a third over 200; the cap is what "one
# sentence" is taken to mean, and it is repeated in the wire description
# because maxLength itself is stripped from the wire schema.
MAX_DESCRIPTION_CHARS = 200

Precision = Literal["exact", "minute", "hour", "day", "approximate"]
SourceSection = Literal["summary", "timeline", "body", "appendix", "other"]
ActionStatus = Literal["done", "planned", "proposed"]
OrgKind = Literal["company", "oss_project", "other"]
TitleSource = Literal["document_metadata", "synthesized"]

# Short snake_case label, e.g. "config_change", "latent_race_condition".
_LABEL_PATTERN = r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"


# Abbreviations whose trailing period is not a sentence end. Masked before
# counting so "e.g. HashiCorp", "Nov. 18" and "U.S. East" stay one sentence.
# Leniency is deliberate: a false split costs a validation retry (a full
# resend of the document), a missed split costs nothing.
_ABBREVIATION = re.compile(
    r"\b(?:e\.g|i\.e|etc|vs|cf|ca|approx|fig|inc|ltd|corp|mr|ms|dr|prof"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|u\.s|a\.m|p\.m)\.",
    re.IGNORECASE,
)
# A sentence ends at . ! or ? (optionally followed by a closing quote or
# bracket), then whitespace, then a capital letter or digit.
_SENTENCE_BREAK = re.compile(r"[.!?]+[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def sentence_count(text: str) -> int:
    text = text.strip()
    if not text:
        return 0
    masked = _ABBREVIATION.sub(lambda m: m.group(0)[:-1] + "\x00", text)
    return len(_SENTENCE_BREAK.split(masked))


def _one_sentence(value: str) -> str:
    count = sentence_count(value)
    if count != 1:
        raise ValueError(
            f"must be exactly one sentence (got {count}); shorten it, and put "
            "supporting evidence in `quote`, not here"
        )
    return value


# The free-text fields the model writes (v0.3): one sentence, capped. The
# max_length constraint is checked first; the sentence validator runs only
# on values that fit, so the model sees one error at a time.
OneSentence = Annotated[str, AfterValidator(_one_sentence)]


class _Strict(BaseModel):
    # extra="forbid" -> `additionalProperties: false` in the JSON schema,
    # which the structured-output API requires on every object.
    model_config = ConfigDict(extra="forbid")


class TimeAnchor(_Strict):
    """One moment in the incident, as the document states it (FINDINGS §4.1).

    `at` is an ISO-8601 date ("2025-10-20") or datetime
    ("2025-10-20T11:20:00" / "...+00:00"). Precision `day` means only the
    date is known and `at` should be date-only. The timezone is the string
    the author wrote ("UTC", "PST", "+02:00"), not a normalisation — the
    spike found documents that mix zones, and coercing them is a lossy
    decision that belongs in analysis, not extraction.
    """

    at: str = Field(description="ISO-8601 date or datetime as stated in the document.")
    precision: Precision
    timezone: str | None = Field(
        description="Timezone exactly as written in the document, or null if not stated."
    )
    quote: str | None = Field(description="The document phrase this anchor is taken from.")
    source_section: SourceSection = Field(
        description="Where in the document the anchor came from. Prefer timeline over prose "
        "when they disagree, and record which one you used."
    )

    @field_validator("at")
    @classmethod
    def _iso8601(cls, value: str) -> str:
        parsed = parse_anchor(value)
        if parsed is None:
            raise ValueError(f"not an ISO-8601 date or datetime: {value!r}")
        return value


def parse_anchor(value: str) -> datetime | date | None:
    """Parse a TimeAnchor.at string. Returns None if it is neither form."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return (
            datetime.fromisoformat(text) if "T" in text or " " in text else date.fromisoformat(text)
        )
    except ValueError:
        return None


class Trigger(_Strict):
    """ADR-001: the initiating change or event that activated the failing
    path. Nullable at the record level — AWS's latent DNS race had none.
    When several changes could count, the one closest to the failure that
    was necessary to activate it wins (ADR-001 §4)."""

    label: TriggerClass = Field(
        pattern=_LABEL_PATTERN,
        description="Short snake_case label for the KIND of initiating change/event, "
        "e.g. config_change, code_deploy, os_auto_update, manual_command, "
        "infrastructure_maintenance, external_input.",
    )
    description: OneSentence = Field(
        min_length=1,
        max_length=MAX_DESCRIPTION_CHARS,
        description=f"One sentence of at most {MAX_DESCRIPTION_CHARS} characters: "
        "what changed, and who/what did it.",
    )
    quote: str | None = Field(description="Supporting phrase from the document, or null.")


class Mechanism(_Strict):
    """ADR-001: the immediate failure mechanism — what actually broke.
    Required and single-valued; further mechanisms go in
    contributing_factors."""

    label: MechanismClass = Field(
        pattern=_LABEL_PATTERN,
        description="Short snake_case label for the KIND of failure, e.g. resource_exhaustion, "
        "crash_on_bad_input, race_condition, cascading_overload, data_loss, "
        "unsafe_failover.",
    )
    description: OneSentence = Field(
        min_length=1,
        max_length=MAX_DESCRIPTION_CHARS,
        description=f"One sentence of at most {MAX_DESCRIPTION_CHARS} characters: "
        "what failed and how.",
    )
    quote: str | None = Field(description="Supporting phrase from the document, or null.")


class ContributingFactor(_Strict):
    text: OneSentence = Field(
        min_length=1,
        max_length=MAX_DESCRIPTION_CHARS,
        description=f"One sentence of at most {MAX_DESCRIPTION_CHARS} characters: "
        "the factor as the author states it.",
    )
    source_section: SourceSection


class Action(_Strict):
    text: str = Field(min_length=1)
    status: ActionStatus = Field(
        description="done = completed (past tense / dated), planned = committed to, "
        "proposed = 'we will investigate' / under consideration."
    )
    date: str | None = Field(description="ISO-8601 date if the document dates the action.")

    @field_validator("date")
    @classmethod
    def _iso_date(cls, value: str | None) -> str | None:
        if value is not None and parse_anchor(value) is None:
            raise ValueError(f"not an ISO-8601 date: {value!r}")
        return value


class Quantity(_Strict):
    metric: str = Field(
        min_length=1, description="What is being counted, e.g. 'projects', 'webhooks dropped'."
    )
    value: str = Field(
        min_length=1,
        description="The stated value, verbatim ('5,000', '~50%', 'tens of thousands').",
    )
    quote: str | None


class BlastRadius(_Strict):
    qualitative: str | None = Field(
        description="The author's qualitative description of impact, or null."
    )
    quantitative: list[Quantity] = Field(
        description="Every customer- or infra-facing number the document states. Empty if none."
    )


class AffectedScope(_Strict):
    services: list[str] = Field(
        description="Named services/products affected, as the document names them."
    )
    regions: list[str] = Field(description="Regions/zones named as the axis of impact, if any.")
    all_services: bool = Field(
        description="True if the document says all services / the whole platform were affected."
    )
    list_is_complete: bool | None = Field(
        description="True if the document presents its list as exhaustive, false if it says "
        "the list is partial ('see event history for the full list'), null if it does not say."
    )


class IncidentRecord(_Strict):
    """One document's primary incident. See module docstring for provenance."""

    publisher_org: str = Field(min_length=1, description="Who published the postmortem.")
    affected_org: str = Field(
        min_length=1, description="Whose service was down. Usually the publisher."
    )
    vendor_org: str | None = Field(
        description="Third party whose component failed (e.g. HashiCorp for Consul), or null."
    )
    affected_org_kind: OrgKind
    title: str = Field(min_length=1)
    title_source: TitleSource
    summary: str = Field(
        min_length=1, description="Two to four sentences: what happened, in order."
    )
    affected: AffectedScope
    # The draft's `change_induced` boolean is exactly `trigger is not None`
    # and is not stored separately — ADR-001 §1.
    trigger: Trigger | None = Field(
        description="Null ONLY when the document identifies no initiating change or event."
    )
    mechanism: Mechanism
    contributing_factors: list[ContributingFactor] = Field(
        description="Factors the AUTHOR claims contributed. "
        "Do not infer factors from the remediation list."
    )
    detection_method: DetectionMethod = Field(
        description="The first signal that caused the organisation to recognise the incident. "
        "See rules."
    )
    detection_quote: str | None = Field(
        description="Supporting phrase for detection_method, or null."
    )
    change_at: TimeAnchor | None = Field(
        description="When the triggering change was applied. Null if no trigger or not stated."
    )
    impact_start: TimeAnchor | None = Field(description="When users/customers were first affected.")
    detected_at: TimeAnchor | None = Field(
        description="When the org first acknowledged the incident (see detection rules)."
    )
    mitigated_at: TimeAnchor | None = Field(description="When impact stopped for most users.")
    resolved_at: TimeAnchor | None = Field(
        description="When the org declared the incident over / fully recovered."
    )
    time_to_detect_text: str | None = Field(
        description="The author's own phrase for detection latency, if any ('within 2 minutes')."
    )
    time_to_mitigate_text: str | None = Field(
        description="The author's own phrase for time to mitigation, if any."
    )
    blast_radius: BlastRadius
    mitigations: list[Action] = Field(
        description="Actions taken DURING the incident to reduce impact "
        "(rollback, throttle, failover)."
    )
    remediations: list[Action] = Field(
        description="Actions taken or planned AFTER the incident to prevent recurrence."
    )


RECORD_FIELDS: tuple[str, ...] = tuple(IncidentRecord.model_fields)


class FieldConfidence(_Strict):
    """Self-reported confidence per top-level IncidentRecord field, 0..1.

    Written out explicitly (rather than generated) so the wire schema is a
    closed object with every key required — the API rejects dict-typed
    `additionalProperties`. tests/test_extract_schema.py asserts these keys
    stay equal to IncidentRecord's fields.
    """

    publisher_org: float = Field(ge=0.0, le=1.0)
    affected_org: float = Field(ge=0.0, le=1.0)
    vendor_org: float = Field(ge=0.0, le=1.0)
    affected_org_kind: float = Field(ge=0.0, le=1.0)
    title: float = Field(ge=0.0, le=1.0)
    title_source: float = Field(ge=0.0, le=1.0)
    summary: float = Field(ge=0.0, le=1.0)
    affected: float = Field(ge=0.0, le=1.0)
    trigger: float = Field(ge=0.0, le=1.0)
    mechanism: float = Field(ge=0.0, le=1.0)
    contributing_factors: float = Field(ge=0.0, le=1.0)
    detection_method: float = Field(ge=0.0, le=1.0)
    detection_quote: float = Field(ge=0.0, le=1.0)
    change_at: float = Field(ge=0.0, le=1.0)
    impact_start: float = Field(ge=0.0, le=1.0)
    detected_at: float = Field(ge=0.0, le=1.0)
    mitigated_at: float = Field(ge=0.0, le=1.0)
    resolved_at: float = Field(ge=0.0, le=1.0)
    time_to_detect_text: float = Field(ge=0.0, le=1.0)
    time_to_mitigate_text: float = Field(ge=0.0, le=1.0)
    blast_radius: float = Field(ge=0.0, le=1.0)
    mitigations: float = Field(ge=0.0, le=1.0)
    remediations: float = Field(ge=0.0, le=1.0)


class ExtractionOutput(_Strict):
    """What the model must return: the record plus per-field confidence."""

    record: IncidentRecord
    confidence: FieldConfidence


# Keywords the structured-output API does not accept. They stay in the
# Pydantic models (validated client-side); only the wire copy drops them.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "format",
        "default",
        "title",
    }
)


def wire_schema() -> dict[str, Any]:
    """JSON schema for `ExtractionOutput` in the shape sent to the model."""
    return _strip(ExtractionOutput.model_json_schema())


def _strip(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _UNSUPPORTED_KEYWORDS:
                continue
            if key in {"properties", "$defs"} and isinstance(value, dict):
                # Keys here are field/model names, never keywords.
                out[key] = {name: _strip(sub) for name, sub in value.items()}
            else:
                out[key] = _strip(value)
        if out.get("type") == "object" and "additionalProperties" not in out:
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_strip(item) for item in node]
    return node


def format_validation_error(exc: ValidationError) -> str:
    """Render a ValidationError as a compact list the model can act on."""
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"- {loc}: {err['msg']}")
    return "\n".join(lines)
