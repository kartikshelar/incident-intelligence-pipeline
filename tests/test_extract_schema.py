"""The v0.1 incident record: every FINDINGS §4 shape change and ADR-001/002
rule that M3 was asked to implement, asserted by name."""

import json

import pytest
from pydantic import ValidationError

from app.extract.derive import derive_durations
from app.extract.schema import (
    RECORD_FIELDS,
    ExtractionOutput,
    FieldConfidence,
    IncidentRecord,
    format_validation_error,
    wire_schema,
)
from app.extract.taxonomy import DETECTION_METHODS
from tests.fake_llm import valid_output


def test_valid_output_validates() -> None:
    out = ExtractionOutput.model_validate(valid_output())
    assert out.record.trigger is not None
    assert out.record.trigger.label == "config_change"


# --- ADR-001: trigger nullable, mechanism required, change_induced gone ----


def test_trigger_is_nullable_and_mechanism_is_required() -> None:
    data = valid_output()
    data["record"]["trigger"] = None
    out = ExtractionOutput.model_validate(data)
    assert out.record.trigger is None

    data["record"]["mechanism"] = None
    with pytest.raises(ValidationError) as exc:
        ExtractionOutput.model_validate(data)
    assert "record.mechanism" in format_validation_error(exc.value)


def test_mechanism_is_single_valued_not_a_list() -> None:
    data = valid_output()
    data["record"]["mechanism"] = [data["record"]["mechanism"]]
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(data)


def test_change_induced_is_not_a_field() -> None:
    assert "change_induced" not in RECORD_FIELDS
    assert "trigger_class" not in RECORD_FIELDS
    data = valid_output()
    data["record"]["change_induced"] = True
    with pytest.raises(ValidationError):  # extra="forbid"
        ExtractionOutput.model_validate(data)


def test_trigger_and_mechanism_labels_are_snake_case() -> None:
    data = valid_output()
    data["record"]["trigger"]["label"] = "A database permissions change"
    with pytest.raises(ValidationError) as exc:
        ExtractionOutput.model_validate(data)
    assert "record.trigger.label" in format_validation_error(exc.value)


# --- ADR-002: detection_method ---------------------------------------------


def test_detection_method_enum_matches_adr_002() -> None:
    assert set(DETECTION_METHODS) == {
        "monitoring",
        "customer_report",
        "internal_manual",
        "operator",
        "ambiguous",
        "unknown",
    }


def test_draft_value_automated_is_rejected() -> None:
    data = valid_output()
    data["record"]["detection_method"] = "automated"
    with pytest.raises(ValidationError) as exc:
        ExtractionOutput.model_validate(data)
    assert "record.detection_method" in format_validation_error(exc.value)


# --- FINDINGS §4.1 / §4.2: typed anchors, durations derived not extracted ---


def test_time_anchors_are_typed_and_nullable() -> None:
    data = valid_output()
    for anchor in ("change_at", "impact_start", "detected_at", "mitigated_at", "resolved_at"):
        assert anchor in RECORD_FIELDS
        data["record"][anchor] = None
    ExtractionOutput.model_validate(data)

    data["record"]["impact_start"] = {
        "at": "yesterday",
        "precision": "day",
        "timezone": None,
        "quote": None,
        "source_section": "body",
    }
    with pytest.raises(ValidationError) as exc:
        ExtractionOutput.model_validate(data)
    assert "record.impact_start.at" in format_validation_error(exc.value)


def test_occurred_at_and_interval_fields_are_gone() -> None:
    for gone in ("occurred_at", "time_to_detect", "time_to_mitigate"):
        assert gone not in RECORD_FIELDS
    assert "time_to_detect_text" in RECORD_FIELDS
    assert "time_to_mitigate_text" in RECORD_FIELDS


def test_durations_derived_from_anchors_name_their_pair() -> None:
    record = IncidentRecord.model_validate(valid_output()["record"])
    derived = derive_durations(record)
    assert derived["time_to_detect"] == {
        "seconds": 180.0,
        "precision": "minute",
        "from": "impact_start",
        "to": "detected_at",
    }
    assert derived["time_to_mitigate"]["seconds"] == (14 * 60 + 30 - 11 * 60 - 28) * 60


def test_absent_or_day_precision_anchors_give_null_not_a_coerced_number() -> None:
    data = valid_output()["record"]
    data["detected_at"] = None
    assert derive_durations(IncidentRecord.model_validate(data))["time_to_detect"] is None

    data["detected_at"] = {
        "at": "2025-11-18",
        "precision": "day",
        "timezone": None,
        "quote": None,
        "source_section": "body",
    }
    assert derive_durations(IncidentRecord.model_validate(data))["time_to_detect"] is None


# --- FINDINGS §4.5 / §4.6 / §4.7 / §4.9 -------------------------------------


def test_affected_services_has_completeness_and_all_services() -> None:
    assert "affected_services" not in RECORD_FIELDS
    data = valid_output()
    data["record"]["affected"] = {
        "services": [],
        "regions": ["us-east-1"],
        "all_services": True,
        "list_is_complete": None,
    }
    out = ExtractionOutput.model_validate(data)
    assert out.record.affected.list_is_complete is None


def test_org_is_split_into_publisher_affected_vendor() -> None:
    assert "org" not in RECORD_FIELDS
    data = valid_output()
    data["record"]["vendor_org"] = "HashiCorp"
    data["record"]["affected_org_kind"] = "oss_project"
    ExtractionOutput.model_validate(data)


def test_title_carries_its_source() -> None:
    data = valid_output()
    data["record"]["title_source"] = "guessed"
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(data)


def test_remediation_actions_split_into_mitigations_and_remediations() -> None:
    assert "remediation_actions" not in RECORD_FIELDS
    data = valid_output()
    data["record"]["remediations"][0]["status"] = "maybe"
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(data)
    data["record"]["remediations"][0]["status"] = "done"
    data["record"]["remediations"][0]["date"] = "2024-08-06"
    ExtractionOutput.model_validate(data)


def test_contributing_factors_have_source_section_and_no_normalized_class() -> None:
    data = valid_output()
    data["record"]["contributing_factors"][0]["normalized_class"] = "process"
    with pytest.raises(ValidationError):
        ExtractionOutput.model_validate(data)


# --- Confidence: captured per field, not thresholded ---------------------------


def test_confidence_keys_mirror_record_fields() -> None:
    assert tuple(FieldConfidence.model_fields) == RECORD_FIELDS


def test_confidence_out_of_range_is_a_validation_error() -> None:
    data = valid_output()
    data["confidence"]["trigger"] = 1.5
    with pytest.raises(ValidationError) as exc:
        ExtractionOutput.model_validate(data)
    assert "confidence.trigger" in format_validation_error(exc.value)


# --- Wire schema shape --------------------------------------------------------


def _walk(node: object) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item))
    return found


def test_wire_schema_has_no_unsupported_keywords_and_closed_objects() -> None:
    schema = wire_schema()
    text = json.dumps(schema)
    for keyword in ("minLength", "maxLength", "pattern", "minimum", "maximum", "default"):
        assert f'"{keyword}"' not in text, keyword
    for node in _walk(schema):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False
            assert set(node.get("required", [])) == set(node.get("properties", {}))
    assert "$defs" in schema
    assert "description" in schema["$defs"]["Trigger"]["properties"]["label"]
