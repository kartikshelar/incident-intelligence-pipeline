"""The incident record (v0.3): every FINDINGS §4 shape change and ADR-001/002
rule that M3 was asked to implement, asserted by name, plus the v0.3
constraint on the descriptions the model writes."""

import json

import pytest
from pydantic import ValidationError

from app.extract.derive import derive_durations
from app.extract.schema import (
    MAX_DESCRIPTION_CHARS,
    RECORD_FIELDS,
    SCHEMA_VERSION,
    ExtractionOutput,
    FieldConfidence,
    IncidentRecord,
    format_validation_error,
    sentence_count,
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


def test_detection_before_impact_is_recorded_as_a_negative_duration() -> None:
    """A latent trigger can be noticed before users are affected (Roblox,
    run 02). That is a valid, signed duration — not an error, not None,
    not clamped to zero."""
    data = valid_output()["record"]
    data["impact_start"]["at"] = "2025-11-18T11:28:00"
    data["detected_at"]["at"] = "2025-11-18T08:31:00"  # 2h57m earlier
    derived = derive_durations(IncidentRecord.model_validate(data))
    assert derived["time_to_detect"] == {
        "seconds": -(2 * 3600 + 57 * 60),
        "precision": "minute",
        "from": "impact_start",
        "to": "detected_at",
    }


def test_mitigation_before_impact_is_also_recorded_signed() -> None:
    data = valid_output()["record"]
    data["mitigated_at"]["at"] = "2025-11-18T11:00:00"  # 28 min before impact_start
    derived = derive_durations(IncidentRecord.model_validate(data))
    assert derived["time_to_mitigate"]["seconds"] == -28 * 60


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


def test_wire_descriptions_are_the_v01_wording_not_the_v02_trim() -> None:
    """v0.2 cut every description the model reads to one sentence and was
    measured to cost more in retries than it saved (schema.py changelog,
    ADR-005 §4). v0.3 restored the v0.1 wording; this pins the revert."""
    assert SCHEMA_VERSION == "0.4"
    defs = wire_schema()["$defs"]
    assert defs["TimeAnchor"]["description"].startswith(
        "One moment in the incident, as the document states it (FINDINGS §4.1)."
    )
    assert sentence_count(defs["TimeAnchor"]["description"]) > 1
    assert defs["Trigger"]["description"].startswith("ADR-001:")
    record = defs["IncidentRecord"]["properties"]
    assert record["detection_method"]["description"].endswith("See rules.")
    assert record["affected_org"]["description"] == "Whose service was down. Usually the publisher."
    # Quote fields are provenance and are never constrained.
    assert "detection_quote" in record
    assert "quote" in defs["Trigger"]["properties"]
    assert "quote" in defs["TimeAnchor"]["properties"]


# --- v0.3+: the descriptions the model WRITES are one short sentence -------


def test_cap_is_400_after_run_04_measured_200_as_a_net_loss() -> None:
    """v0.4: the 200-character cap caused 3 of run 04's 4 retries, and a
    retry resends the document (schema.py changelog)."""
    assert MAX_DESCRIPTION_CHARS == 400


def _written_fields(data: dict) -> list[tuple[dict, str, str]]:
    """(container, key, error-loc) for every v0.3-constrained written field."""
    record = data["record"]
    out = [
        (record["trigger"], "description", "record.trigger.description"),
        (record["mechanism"], "description", "record.mechanism.description"),
    ]
    for i, factor in enumerate(record["contributing_factors"]):
        out.append((factor, "text", f"record.contributing_factors.{i}.text"))
    return out


@pytest.mark.parametrize("index", range(4))
def test_written_description_over_the_character_cap_fails_with_its_location(index: int) -> None:
    data = valid_output()
    container, key, loc = _written_fields(data)[index]
    container[key] = (
        "A " + "very " * (MAX_DESCRIPTION_CHARS // 5) + "long single sentence about what broke."
    )
    assert len(container[key]) > MAX_DESCRIPTION_CHARS
    with pytest.raises(ValidationError) as exc:
        ExtractionOutput.model_validate(data)
    message = format_validation_error(exc.value)
    assert loc in message
    assert f"at most {MAX_DESCRIPTION_CHARS} characters" in message


@pytest.mark.parametrize("index", range(4))
def test_written_description_of_two_sentences_fails_with_its_location(index: int) -> None:
    data = valid_output()
    container, key, loc = _written_fields(data)[index]
    container[key] = "The permissions change altered the query output. The file then doubled."
    with pytest.raises(ValidationError) as exc:
        ExtractionOutput.model_validate(data)
    message = format_validation_error(exc.value)
    assert loc in message
    assert "exactly one sentence (got 2)" in message


def test_one_sentence_at_the_cap_is_accepted() -> None:
    data = valid_output()
    sentence = ("x" * (MAX_DESCRIPTION_CHARS - 1)) + "."
    data["record"]["mechanism"]["description"] = sentence
    data["record"]["contributing_factors"][0]["text"] = sentence
    ExtractionOutput.model_validate(data)


def test_quotes_and_summary_are_not_constrained() -> None:
    """Quotes are provenance; the summary is deliberately two to four
    sentences. Neither is touched by the v0.3 cap."""
    data = valid_output()
    long_quote = "we saw. Many things. Happen here " * 20
    data["record"]["mechanism"]["quote"] = long_quote
    data["record"]["trigger"]["quote"] = long_quote
    data["record"]["detection_quote"] = long_quote
    data["record"]["summary"] = "First. Second. Third. Fourth."
    ExtractionOutput.model_validate(data)


def test_wire_schema_tells_the_model_the_cap_since_maxlength_is_stripped() -> None:
    defs = wire_schema()["$defs"]
    for model, field in (
        ("Trigger", "description"),
        ("Mechanism", "description"),
        ("ContributingFactor", "text"),
    ):
        node = defs[model]["properties"][field]
        assert "maxLength" not in node
        assert f"at most {MAX_DESCRIPTION_CHARS} characters" in node["description"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("No terminal punctuation", 1),
        ("The proxy panicked when the file exceeded its size limit.", 1),
        ("Two sentences here. And another one.", 2),
        ("Three! Really? Yes.", 3),
        # Abbreviations and dates are not sentence ends.
        ("A third party (e.g. HashiCorp for Consul) was involved.", 1),
        ("Ended on Nov. 18 at 11:28 UTC.", 1),
        ("Traffic in the U.S. East region failed.", 1),
        ("It ran at 5 p.m. PST and failed.", 1),
        ("Fixed in v1.2. Then redeployed.", 2),
        ('He said "it broke." Then it did.', 2),
        ("Lowercase after a period. is not a new sentence", 1),
    ],
)
def test_sentence_count(text: str, expected: int) -> None:
    assert sentence_count(text) == expected
