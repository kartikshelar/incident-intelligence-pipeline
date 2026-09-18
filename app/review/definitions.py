"""Field definitions for the review page (M4 follow-up, task 1).

A reviewer judging one field needs to know what the field means and, just
as much, what it does NOT mean: `impact_start` is when users were first
affected, not when the initiating change was applied (that is `change_at`);
`mitigations` are actions during the incident, `remediations` after it.

Everything shown is read from the schema itself — `IncidentRecord.
model_json_schema()`, the same field descriptions and class docstrings the
model receives in the wire schema (app/extract/schema.py) — plus the blocks
of the frozen system prompt (app/extract/prompt.py) that name the field,
because `detection_method`'s description ends in "See rules." and the rules
live there. Nothing in this module is a second copy of a definition, so the
page cannot drift from what the model was told; when the schema has no
description for a field (today: `title`, `title_source`,
`affected_org_kind`), the page says so instead of inventing one.

One `FieldDefinition` per top-level record field:
  description         the field's own `Field(description=...)`, if any
  object_description  the docstring of the object the field holds
                      (Trigger, Mechanism, TimeAnchor, ...), if any
  enum                the closed value list, for enum-typed fields
  nullable            whether null is a legal value
  exclusions          the sentences of the two texts above that say what
                      the field is not ("... is never the trigger",
                      "Do not infer factors from the remediation list")
  related             sibling fields a reviewer could confuse this one
                      with, each with the first sentence of its own
                      definition (the five time anchors; mitigations vs
                      remediations; the three org fields; ...)
  parts               sub-fields of an object field, with descriptions and
                      value lists (`mechanism.label` carries the sixteen
                      class definitions from app/extract/taxonomy.py)
  prompt_rules        the paragraphs of SYSTEM_PROMPT that mention the
                      field by name, verbatim
"""

from __future__ import annotations

import dataclasses
import re
from functools import cache
from typing import Any

from app.extract.prompt import SYSTEM_PROMPT
from app.extract.schema import RECORD_FIELDS, IncidentRecord

# Fields a reviewer is likely to confuse with one another. Membership is
# the only thing declared here; the text shown for each member is read
# from the schema. tests/test_review_definitions.py asserts every member
# is a real record field.
_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("change_at", "impact_start", "detected_at", "mitigated_at", "resolved_at"),
    ("mitigations", "remediations"),
    ("detection_method", "detection_quote"),
    ("time_to_detect_text", "time_to_mitigate_text"),
    ("publisher_org", "affected_org", "vendor_org"),
    ("trigger", "mechanism", "contributing_factors"),
    ("title", "title_source"),
    ("affected", "blast_radius"),
)

# A sentence that draws a boundary: says what the field is not, or when
# it must be null. Matched case-insensitively so "NOT trigger classes"
# and "not stated" both count.
_BOUNDARY_WORD = re.compile(r"\b(?:not|never)\b", re.IGNORECASE)

# Abbreviations whose period is not a sentence end, masked before
# splitting. Same idea as schema.py's list, kept short: these are the
# ones that occur in the descriptions.
_ABBREVIATION = re.compile(r"\b(?:e\.g|i\.e|etc|vs|cf)\.", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclasses.dataclass(frozen=True)
class SubField:
    path: str  # "label", "quantitative[].quote"
    description: str | None
    enum: tuple[str, ...]
    nullable: bool


@dataclasses.dataclass(frozen=True)
class Related:
    field: str
    summary: str | None  # first sentence of that field's definition


@dataclasses.dataclass(frozen=True)
class FieldDefinition:
    field: str
    description: str | None
    object_description: str | None
    # True when several fields hold the same object type (the five time
    # anchors are all TimeAnchor): its docstring then describes the type,
    # not this field, and the field's own description is what tells them
    # apart, so it is shown first and used as the summary.
    object_is_shared: bool
    enum: tuple[str, ...]
    nullable: bool
    exclusions: tuple[str, ...]
    related: tuple[Related, ...]
    parts: tuple[SubField, ...]
    prompt_rules: tuple[str, ...]

    @property
    def has_text(self) -> bool:
        return bool(self.description or self.object_description)

    @property
    def texts(self) -> tuple[str, ...]:
        """The definition paragraphs in display order: the field-specific
        one first."""
        ordered = (
            (self.description, self.object_description)
            if self.object_is_shared
            else (self.object_description, self.description)
        )
        return tuple(t for t in ordered if t)

    @property
    def summary(self) -> str | None:
        """One sentence that identifies the field, for the 'not to be
        confused with' list of its siblings."""
        return _first_sentence(self.texts[0]) if self.texts else None


@cache
def _schema() -> dict[str, Any]:
    return IncidentRecord.model_json_schema()


def _resolve(node: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Unwrap `X | None` and follow a `$ref`. Returns the concrete node and
    whether null was one of the options."""
    nullable = False
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        nullable = len(options) < len(node["anyOf"])
        if len(options) == 1:
            node = options[0]
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        node = _schema()["$defs"][name]
    return node, nullable


def _object_name(node: dict[str, Any]) -> str | None:
    """The `$defs` name of the object a field holds (directly, or as an
    array's item type), or None for scalar fields."""
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        if len(options) == 1:
            node = options[0]
    if node.get("type") == "array" and "items" in node:
        node = node["items"]
    ref = node.get("$ref")
    return ref.rsplit("/", 1)[-1] if isinstance(ref, str) else None


def _object_of(node: dict[str, Any]) -> dict[str, Any] | None:
    """The object schema a field holds: the node itself, or an array's item
    type, when that is an object with properties."""
    core, _ = _resolve(node)
    if core.get("type") == "array" and "items" in core:
        core, _ = _resolve(core["items"])
    return core if "properties" in core else None


@cache
def _shared_objects() -> frozenset[str]:
    """Object types held by more than one top-level field."""
    names = [_object_name(node) for node in _schema()["properties"].values()]
    return frozenset(n for n in names if n is not None and names.count(n) > 1)


def _unwrap(text: str | None) -> str | None:
    """Docstrings arrive hard-wrapped at ~72 columns. Join the wrapped lines
    back into prose, but keep a `- name: definition` bullet on its own
    line so the taxonomy class lists stay readable."""
    if not text:
        return None
    lines: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if line.startswith("- ") or not lines:
            lines.append(line)
        else:
            lines[-1] = f"{lines[-1]} {line}"
    return "\n".join(lines) or None


def _sentences(text: str) -> list[str]:
    masked = _ABBREVIATION.sub(lambda m: m.group(0)[:-1] + "\x00", text)
    return [s.replace("\x00", ".") for s in _SENTENCE_END.split(masked) if s.strip()]


def _first_sentence(text: str | None) -> str | None:
    if not text:
        return None
    first_line = text.split("\n", 1)[0]
    sentences = _sentences(first_line)
    return sentences[0] if sentences else None


def _exclusions(*texts: str | None) -> tuple[str, ...]:
    out: list[str] = []
    for text in texts:
        if not text:
            continue
        # Only the prose part: a class list's bullets are definitions of
        # other values, not boundaries of this field.
        prose = text.split("\n- ", 1)[0]
        out.extend(s for s in _sentences(prose) if _BOUNDARY_WORD.search(s))
    return tuple(out)


def _parts(node: dict[str, Any], prefix: str, depth: int) -> list[SubField]:
    obj = _object_of(node)
    if obj is None or depth > 3:
        return []
    core, _ = _resolve(node)
    if core.get("type") == "array":
        prefix = f"{prefix}[]"
    out: list[SubField] = []
    for name, sub in obj["properties"].items():
        sub_core, nullable = _resolve(sub)
        path = f"{prefix}.{name}" if prefix else name
        out.append(
            SubField(
                path=path,
                description=_unwrap(sub.get("description") or sub_core.get("description")),
                enum=tuple(sub_core.get("enum", ())),
                nullable=nullable,
            )
        )
        out.extend(_parts(sub, path, depth + 1))
    return out


@cache
def _prompt_blocks() -> tuple[str, ...]:
    """The blank-line-separated blocks of the system prompt after the
    'Field rules:' heading."""
    _, _, rules = SYSTEM_PROMPT.partition("Field rules:")
    return tuple(block.strip() for block in re.split(r"\n\s*\n", rules) if block.strip())


def prompt_rules(field: str) -> tuple[str, ...]:
    """Every rule block that names `field` as a whole word (so `trigger`
    does not match `triggering`, and `affected` does not match
    `affected_org`), in prompt order."""
    mention = re.compile(rf"(?<!\w){re.escape(field)}(?!\w)")
    return tuple(block for block in _prompt_blocks() if mention.search(block))


def _texts(field: str) -> tuple[str | None, str | None, bool]:
    """(field description, object docstring, object is shared) for a field."""
    node = _schema()["properties"][field]
    obj = _object_of(node)
    description = _unwrap(node.get("description"))
    object_description = _unwrap(obj.get("description")) if obj is not None else None
    return description, object_description, _object_name(node) in _shared_objects()


def _summary(field: str) -> str | None:
    """Standalone so siblings can be summarised without building their
    own definitions (which would recurse through the family)."""
    description, object_description, shared = _texts(field)
    ordered = (description, object_description) if shared else (object_description, description)
    for text in ordered:
        if text:
            return _first_sentence(text)
    return None


@cache
def field_definition(field: str) -> FieldDefinition:
    if field not in RECORD_FIELDS:
        raise KeyError(f"{field!r} is not an IncidentRecord field")
    node = _schema()["properties"][field]
    core, nullable = _resolve(node)
    description, object_description, shared = _texts(field)
    related = tuple(
        Related(field=other, summary=_summary(other))
        for family in _FAMILIES
        if field in family
        for other in family
        if other != field
    )
    return FieldDefinition(
        field=field,
        description=description,
        object_description=object_description,
        object_is_shared=shared,
        enum=tuple(core.get("enum", ())),
        nullable=nullable,
        exclusions=_exclusions(object_description, description),
        related=related,
        parts=tuple(_parts(node, "", 1)),
        prompt_rules=prompt_rules(field),
    )
