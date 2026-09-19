"""Typed correction forms: derived from the field type, round-trip through
form fields, and lenient about what they are asked to render."""

from typing import Any

import pytest

from app.extract.schema import RECORD_FIELDS
from app.extract.taxonomy import DETECTION_METHODS, MECHANISM_CLASSES, TRIGGER_CLASSES
from app.review.fields import validate_correction
from app.review.forms import Bound, bind, form_spec, parse
from tests.fake_llm import valid_output

RECORD = valid_output()["record"]


def encode(bound: Bound) -> dict[str, str]:
    """What a browser would submit for a bound form, untouched."""
    data: dict[str, str] = {}
    spec = bound.spec
    if spec.kind == "object":
        if spec.nullable and bound.is_null:
            data[f"{bound.name}.null"] = "1"
        for child in bound.children:
            data.update(encode(child))
    elif spec.kind == "rows":
        for row in bound.children:
            data.update(encode(row))
    elif spec.kind == "lines":
        data[bound.name] = "\n".join(bound.value)
    elif spec.kind == "bool":
        data[bound.name] = {True: "true", False: "false"}.get(bound.value, "")
    else:
        if spec.nullable and bound.is_null:
            data[f"{bound.name}.null"] = "1"
        data[bound.name] = "" if bound.value is None else str(bound.value)
    return data


def roundtrip(field: str, value: Any) -> Any:
    spec = form_spec(field)
    return parse(spec, "v", encode(bind(spec, "v", value)))


@pytest.mark.parametrize("field", RECORD_FIELDS)
def test_every_field_round_trips_through_its_form(field: str) -> None:
    value = RECORD[field]
    parsed = roundtrip(field, value)
    assert parsed == value
    assert validate_correction(field, parsed) == value


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(KeyError):
        form_spec("nope")


def test_kinds_follow_the_schema() -> None:
    assert form_spec("publisher_org").kind == "text"
    assert form_spec("summary").kind == "textarea"
    assert form_spec("vendor_org").nullable is True
    detection = form_spec("detection_method")
    assert (detection.kind, detection.options) == ("select", DETECTION_METHODS)

    trigger = form_spec("trigger")
    assert (trigger.kind, trigger.nullable) == ("object", True)
    assert [c.label for c in trigger.children] == ["label", "description", "quote"]
    assert trigger.children[0].options == TRIGGER_CLASSES
    assert trigger.children[1].kind == "textarea"
    assert trigger.children[2].nullable is True
    assert form_spec("mechanism").children[0].options == MECHANISM_CLASSES

    anchor = form_spec("change_at")
    assert [c.label for c in anchor.children] == [
        "at",
        "precision",
        "timezone",
        "quote",
        "source_section",
    ]
    assert anchor.children[1].options == (
        "exact",
        "minute",
        "hour",
        "day",
        "month",
        "year",
        "approximate",
    )
    assert anchor.children[0].description is not None  # the schema's hint

    affected = form_spec("affected")
    kinds = {c.label: c.kind for c in affected.children}
    assert kinds == {
        "services": "lines",
        "regions": "lines",
        "all_services": "bool",
        "list_is_complete": "bool",
    }
    assert [c.nullable for c in affected.children] == [False, False, False, True]

    actions = form_spec("mitigations")
    assert actions.kind == "rows" and actions.row is not None
    assert [c.label for c in actions.row.children] == ["text", "status", "date"]
    assert actions.row.children[1].options == ("done", "planned", "proposed")

    radius = form_spec("blast_radius")
    assert radius.children[1].kind == "rows"
    assert radius.children[1].row is not None
    assert [c.label for c in radius.children[1].row.children] == ["metric", "value", "quote"]


def test_bind_fills_the_current_value_and_names_inputs_by_path() -> None:
    bound = bind(form_spec("trigger"), "v", RECORD["trigger"])
    assert bound.is_null is False
    assert [c.name for c in bound.children] == ["v.label", "v.description", "v.quote"]
    assert bound.children[0].value == "config_change"
    rows = bind(form_spec("mitigations"), "v", RECORD["mitigations"])
    assert [r.name for r in rows.children] == ["v.0"]
    assert rows.children[0].children[0].name == "v.0.text"
    assert rows.template is not None and rows.template.name == "v.__i__"
    lines = bind(form_spec("affected"), "v", RECORD["affected"]).children[0]
    assert lines.value == ["Core CDN", "Bot Management", "Workers KV"]


def test_bind_renders_a_null_or_mis_shaped_value_without_raising() -> None:
    null_trigger = bind(form_spec("trigger"), "v", None)
    assert null_trigger.is_null is True
    assert null_trigger.children[0].value is None
    assert null_trigger.children[0].needs_choose_option is True
    odd = bind(form_spec("blast_radius"), "v", "not an object")
    assert odd.children[1].children == ()
    assert bind(form_spec("affected"), "v", {"services": "not a list"}).children[0].value == []


def test_parse_nullable_string_left_empty_is_null_and_toggle_wins() -> None:
    assert parse(form_spec("vendor_org"), "v", {"v": ""}) is None
    assert parse(form_spec("vendor_org"), "v", {"v": "  HashiCorp "}) == "HashiCorp"
    assert parse(form_spec("vendor_org"), "v", {"v": "HashiCorp", "v.null": "1"}) is None
    # A required string left empty stays "" so validation names the problem.
    assert parse(form_spec("publisher_org"), "v", {"v": ""}) == ""


def test_parse_object_null_toggle_and_nested_empty_quote() -> None:
    spec = form_spec("trigger")
    assert parse(spec, "v", {"v.null": "1", "v.label": "code_deploy"}) is None
    value = parse(
        spec, "v", {"v.label": "code_deploy", "v.description": "A deploy did it.", "v.quote": ""}
    )
    assert value == {"label": "code_deploy", "description": "A deploy did it.", "quote": None}
    assert validate_correction("trigger", value) == value


def test_parse_rows_drops_blank_rows_and_keeps_submitted_order() -> None:
    spec = form_spec("mitigations")
    data = {
        "v.2.text": "Second",
        "v.2.status": "planned",
        "v.2.date": "",
        "v.0.text": "First",
        "v.0.status": "done",
        "v.0.date": "2025-11-18",
        "v.5.text": "",
        "v.5.status": "done",  # the blank row the page offers
        "v.5.date": "",
    }
    assert parse(spec, "v", data) == [
        {"text": "First", "status": "done", "date": "2025-11-18"},
        {"text": "Second", "status": "planned", "date": None},
    ]
    assert parse(spec, "v", {}) == []


def test_parse_lines_and_bools() -> None:
    value = parse(
        form_spec("affected"),
        "v",
        {
            "v.services": " API \n\nDashboard\n",
            "v.regions": "",
            "v.all_services": "false",
            "v.list_is_complete": "",
        },
    )
    assert value == {
        "services": ["API", "Dashboard"],
        "regions": [],
        "all_services": False,
        "list_is_complete": None,
    }
    assert validate_correction("affected", value) == value
    # An unanswered required bool is None, which validation rejects by name.
    missing = parse(form_spec("affected"), "v", {"v.services": "", "v.regions": ""})
    assert missing["all_services"] is None


def test_parse_select_left_unchosen_fails_validation_by_name() -> None:
    from app.review.fields import InvalidCorrectionError

    value = parse(form_spec("detection_method"), "v", {"v": ""})
    assert value == ""
    with pytest.raises(InvalidCorrectionError, match="detection_method"):
        validate_correction("detection_method", value)
