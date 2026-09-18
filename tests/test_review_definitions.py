"""Field definitions on the review page come from the schema, not a copy."""

import pytest

from app.extract.prompt import SYSTEM_PROMPT
from app.extract.schema import RECORD_FIELDS, IncidentRecord
from app.extract.taxonomy import DETECTION_METHODS, MECHANISM_CLASSES, TRIGGER_CLASSES
from app.review.definitions import _FAMILIES, field_definition, prompt_rules


@pytest.mark.parametrize("field", RECORD_FIELDS)
def test_every_field_has_a_definition_object(field: str) -> None:
    definition = field_definition(field)
    assert definition.field == field
    # The schema has no top-level description for five fields — two of them
    # (affected, blast_radius) are described only through their parts; the
    # page must say so rather than invent one.
    if field in ("title", "title_source", "affected_org_kind"):
        assert definition.has_text is False and definition.parts == ()
    elif field in ("affected", "blast_radius"):
        assert definition.has_text is False and definition.parts
    else:
        assert definition.has_text
        assert definition.summary


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(KeyError):
        field_definition("nope")


def test_description_is_the_schema_text_verbatim() -> None:
    schema = IncidentRecord.model_json_schema()
    assert field_definition("impact_start").description == (
        schema["properties"]["impact_start"]["description"]
    )
    assert field_definition("affected_org").description == (
        "Whose service was down. Usually the publisher."
    )


def test_object_docstring_is_shown_for_object_fields() -> None:
    mechanism = field_definition("mechanism")
    assert mechanism.description is None  # no Field(description=...) on it
    assert mechanism.object_description is not None
    assert mechanism.object_description.startswith(
        "ADR-001: the immediate failure mechanism — what actually broke."
    )
    assert "\n" not in mechanism.object_description  # hard-wrapping undone
    assert field_definition("trigger").object_description is not None
    assert field_definition("trigger").object_description.startswith("ADR-001:")
    assert field_definition("contributing_factors").object_description is None
    # Trigger is the only field holding a Trigger: its docstring is the
    # field's identity and comes first.
    trigger = field_definition("trigger")
    assert trigger.object_is_shared is False
    assert trigger.texts[0] == trigger.object_description
    assert trigger.summary == (
        "ADR-001: the initiating change or event that activated the failing path."
    )


def test_shared_object_docstring_comes_second() -> None:
    """All five anchors are TimeAnchor, so its docstring says what an
    anchor is, not which one; the field's own line comes first."""
    anchor = field_definition("impact_start")
    assert anchor.object_is_shared is True
    assert anchor.texts[0] == "When users/customers were first affected."
    assert anchor.texts[1] is not None and anchor.texts[1].startswith("One moment in the incident")
    assert anchor.summary == "When users/customers were first affected."


def test_exclusions_are_the_not_never_sentences_of_the_definition() -> None:
    trigger = field_definition("trigger")
    assert any("is never the trigger" in s for s in trigger.exclusions)
    assert any("operational_delay are therefore NOT" in s for s in trigger.exclusions)
    assert any("race_condition is NOT a trigger class" in s for s in trigger.exclusions)
    assert field_definition("contributing_factors").exclusions == (
        "Do not infer factors from the remediation list.",
    )
    assert field_definition("summary").exclusions == ()


def test_impact_start_is_told_apart_from_the_other_anchors() -> None:
    related = {r.field: r.summary for r in field_definition("impact_start").related}
    assert set(related) == {"change_at", "detected_at", "mitigated_at", "resolved_at"}
    assert related["change_at"] == "When the triggering change was applied."
    assert related["detected_at"] == (
        "When the org first acknowledged the incident (see detection rules)."
    )


def test_related_uses_the_object_docstring_first_sentence() -> None:
    related = {r.field: r.summary for r in field_definition("contributing_factors").related}
    assert related["trigger"] == (
        "ADR-001: the initiating change or event that activated the failing path."
    )
    assert related["mechanism"] == "ADR-001: the immediate failure mechanism — what actually broke."


def test_families_name_real_fields_once() -> None:
    members = [f for family in _FAMILIES for f in family]
    assert set(members) <= set(RECORD_FIELDS)
    assert len(members) == len(set(members))


def test_enums_and_nullability() -> None:
    detection = field_definition("detection_method")
    assert detection.enum == DETECTION_METHODS
    assert detection.nullable is False
    assert field_definition("vendor_org").nullable is True
    assert field_definition("trigger").nullable is True
    assert field_definition("mechanism").nullable is False
    assert field_definition("affected_org_kind").enum == ("company", "oss_project", "other")


def test_parts_carry_the_class_definitions() -> None:
    parts = {p.path: p for p in field_definition("mechanism").parts}
    assert set(parts) == {"label", "description", "quote"}
    assert parts["label"].enum == MECHANISM_CLASSES
    assert parts["label"].description is not None
    assert "\n- limit_violation: An input or state exceeds" in parts["label"].description
    assert parts["quote"].nullable is True

    trigger_parts = {p.path: p for p in field_definition("trigger").parts}
    assert trigger_parts["label"].enum == TRIGGER_CLASSES


def test_parts_recurse_into_list_items() -> None:
    parts = {p.path: p for p in field_definition("blast_radius").parts}
    assert set(parts) == {
        "qualitative",
        "quantitative",
        "quantitative[].metric",
        "quantitative[].value",
        "quantitative[].quote",
    }
    factors = {p.path: p for p in field_definition("contributing_factors").parts}
    assert set(factors) == {"[].text", "[].source_section"}
    assert factors["[].source_section"].enum == ("summary", "timeline", "body", "appendix", "other")
    assert field_definition("title").parts == ()


def test_prompt_rules_are_the_blocks_that_name_the_field() -> None:
    (block,) = prompt_rules("mitigations")
    assert block.startswith("mitigations vs remediations:")
    assert block in SYSTEM_PROMPT
    # `trigger` must not match `triggering` (the time-anchor block says
    # "triggering change"), `affected` must not match `affected_org`.
    (trigger_block,) = prompt_rules("trigger")
    assert trigger_block.startswith("trigger / mechanism")
    assert [b.split(":", 1)[0] for b in prompt_rules("affected")] == ["affected"]
    assert [b.split(":", 1)[0] for b in prompt_rules("title_source")] == ["title"]
    assert prompt_rules("detection_method")[0].startswith("detection_method — the FIRST signal")
    assert prompt_rules("blast_radius") == ()
    assert field_definition("detection_method").prompt_rules == prompt_rules("detection_method")
