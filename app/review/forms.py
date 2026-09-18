"""Typed correction input (M4 follow-up, task 4).

The first review page took corrections as raw JSON, so correcting a time
anchor meant recalling the wire format of a TimeAnchor. This module
derives a form from the field's own Pydantic type — via the JSON schema
IncidentRecord emits, the same source app/review/definitions.py reads —
so the reviewer sees a labelled input per part, a select for every closed
vocabulary, one line per item for string lists, add/remove rows for lists
of objects, and an explicit null toggle for nullable fields. Nothing in
the page depends on knowing the JSON shape; the shape is the form.

Three steps, all pure functions of the schema:
  form_spec(field)          the input tree for a field: kind, label,
                            description (the schema's own text, shown as a
                            hint), options, nullability, children
  bind(spec, name, value)   the same tree with concrete input names and
                            the current value filled in — the model's
                            value on first render, the reviewer's own
                            submission when it fails validation, so a
                            rejected correction is never lost
  parse(spec, name, data)   the submitted form fields back into the JSON
                            value the field type expects; app.review.
                            fields.validate_correction then validates it
                            as before, and the page re-renders with the
                            error next to the bound form on failure

Conventions the parser applies, stated on the page next to each input:
  - a nullable string left empty is null; an explicit "null" checkbox
    exists for nullable top-level values and nullable objects, because
    "the document does not state this" is a deliberate answer, not an
    omission
  - a string list is one item per line, blank lines ignored
  - a row of a list of objects is dropped when all its text inputs are
    empty (the page always offers one blank row)
  - a select shows an empty "choose" option whenever the current value is
    not one of the options, so a correction never silently inherits the
    first option
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from functools import cache
from typing import Any, Literal

from app.extract.schema import RECORD_FIELDS
from app.review.definitions import record_schema, resolve_node

Kind = Literal["text", "textarea", "select", "bool", "lines", "object", "rows"]

# Property names whose values are prose rather than a phrase.
_LONG_TEXT = frozenset({"summary", "description", "text", "qualitative"})
_ROW_INDEX = "__i__"  # placeholder index in the row template the page clones


@dataclasses.dataclass(frozen=True)
class Spec:
    kind: Kind
    label: str
    description: str | None
    nullable: bool
    options: tuple[str, ...] = ()
    children: tuple[Spec, ...] = ()  # object members
    row: Spec | None = None  # rows: the object spec of one row


@dataclasses.dataclass(frozen=True)
class Bound:
    spec: Spec
    name: str
    value: Any  # scalar kinds: the value; lines: list[str]; others: None
    is_null: bool  # nullable scalar/object: whether the null toggle is on
    children: tuple[Bound, ...]  # object members, or the rows of a list
    template: Bound | None  # rows: one empty row with _ROW_INDEX in its names

    @property
    def needs_choose_option(self) -> bool:
        """A select whose current value is not an option must not default
        to the first option."""
        return self.spec.kind == "select" and self.value not in self.spec.options


def _build(label: str, node: dict[str, Any]) -> Spec:
    core, nullable = resolve_node(node)
    description = node.get("description") or core.get("description")
    if description:
        description = " ".join(description.split())
    if "enum" in core:
        return Spec("select", label, description, nullable, options=tuple(core["enum"]))
    kind = core.get("type")
    if kind == "boolean":
        return Spec("bool", label, description, nullable)
    if kind == "string":
        return Spec("textarea" if label in _LONG_TEXT else "text", label, description, nullable)
    if kind == "array":
        items, _ = resolve_node(core["items"])
        if "properties" in items:
            return Spec("rows", label, description, nullable, row=_build(label, core["items"]))
        return Spec("lines", label, description, nullable)
    if "properties" in core:
        children = tuple(_build(name, sub) for name, sub in core["properties"].items())
        return Spec("object", label, description, nullable, children=children)
    raise ValueError(f"no input for schema node {label}: {core}")


@cache
def form_spec(field: str) -> Spec:
    if field not in RECORD_FIELDS:
        raise KeyError(f"{field!r} is not an IncidentRecord field")
    return _build(field, record_schema()["properties"][field])


def bind(spec: Spec, name: str, value: Any) -> Bound:
    """Attach concrete input names and the current value. A value of the
    wrong shape (a string where an object is expected) is treated as
    absent rather than raising: the form must render for any stored
    value, including one an earlier schema version wrote."""
    if spec.kind == "object":
        members = value if isinstance(value, dict) else {}
        children = tuple(bind(c, f"{name}.{c.label}", members.get(c.label)) for c in spec.children)
        return Bound(spec, name, None, value is None, children, None)
    if spec.kind == "rows":
        assert spec.row is not None
        items = value if isinstance(value, list) else []
        rows = tuple(bind(spec.row, f"{name}.{i}", item) for i, item in enumerate(items))
        template = bind(spec.row, f"{name}.{_ROW_INDEX}", None)
        return Bound(spec, name, None, False, rows, template)
    if spec.kind == "lines":
        lines = [str(v) for v in value] if isinstance(value, list) else []
        return Bound(spec, name, lines, False, (), None)
    return Bound(spec, name, value, value is None, (), None)


def _text_of(data: Mapping[str, str], name: str) -> str:
    return (data.get(name) or "").strip()


def parse(spec: Spec, name: str, data: Mapping[str, str]) -> Any:
    """The submitted fields under `name` as the value the field type
    expects. Never raises on reviewer input: an unfilled required input
    comes back as "" or None and fails validation with the field's own
    message."""
    if spec.kind == "object":
        if spec.nullable and data.get(f"{name}.null"):
            return None
        return {c.label: parse(c, f"{name}.{c.label}", data) for c in spec.children}
    if spec.kind == "rows":
        assert spec.row is not None
        prefix = re.compile(rf"^{re.escape(name)}\.(\d+)\.")
        indices = sorted({int(m.group(1)) for key in data if (m := prefix.match(key))})
        rows = []
        for i in indices:
            row = parse(spec.row, f"{name}.{i}", data)
            texts = [row[c.label] for c in spec.row.children if c.kind in ("text", "textarea")]
            if texts and all(t in (None, "") for t in texts):
                continue  # the blank row the page always offers
            rows.append(row)
        return rows
    if spec.kind == "lines":
        return [line.strip() for line in (data.get(name) or "").splitlines() if line.strip()]
    if spec.nullable and data.get(f"{name}.null"):
        return None
    raw = _text_of(data, name)
    if spec.kind == "bool":
        return {"true": True, "false": False}.get(raw)
    if raw == "" and spec.nullable:
        return None
    return raw
