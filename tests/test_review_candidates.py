"""Candidate passages: deterministic, cue-driven, independent of the record."""

import inspect

import pytest

from app.extract.schema import RECORD_FIELDS
from app.review.candidates import (
    FIELD_CUES,
    MAX_CANDIDATES,
    MIN_CANDIDATES,
    WINDOW_CHARS,
    candidates,
    passages,
)
from tests.review_support import DOCUMENT_TEXT

LONG_DOCUMENT = "\n\n".join(
    [
        "Incident report: API outage on 3 March.",
        "Summary. For about two hours our public API returned errors to roughly 40% of "
        "requests in the EU region. Customers using the dashboard were also affected.",
        "Background. The API is served from three clusters behind a load balancer. Each "
        "cluster runs the same build and reads its configuration from a shared store.",
        "Timeline (all times UTC).\n"
        "09:12 A configuration change was deployed to the shared store by the platform team.\n"
        "09:20 Error rates began to climb in the EU region.\n"
        "09:26 An on-call engineer was paged by the latency alert and opened an incident.\n"
        "10:05 The change was rolled back and error rates returned to normal.\n"
        "11:30 The incident was declared resolved after backfills completed.",
        "What went wrong. The new configuration set a connection limit below the number of "
        "workers per cluster, so workers exceeded the limit and their requests failed. The "
        "software did not handle the rejected connections and each worker crashed in a loop.",
        "Contributing factors. The limit was not validated before rollout because the "
        "configuration store has no schema. The alert threshold was too high, and the "
        "dashboards did not show per-region error rates, which delayed detection.",
        "Remediation. We will add validation to the configuration store and lower the alert "
        "threshold. We plan to add per-region dashboards. An audit of other unvalidated "
        "settings is planned for next quarter.",
        "Appendix. Nothing else of note happened this week, and the weather was fine.",
    ]
)


def test_every_record_field_has_cues() -> None:
    assert set(FIELD_CUES) == set(RECORD_FIELDS)
    assert all(cues for cues in FIELD_CUES.values())


def test_selection_takes_only_the_field_and_the_text() -> None:
    """The record, its quotes and its confidence are never inputs — the
    reviewer must be an independent judge (module docstring)."""
    assert list(inspect.signature(candidates).parameters) == ["field", "document"]


def test_passages_are_true_offsets_and_bounded() -> None:
    for passage in passages(LONG_DOCUMENT):
        assert LONG_DOCUMENT[passage.start : passage.end] == passage.text
        assert passage.text == passage.text.strip()
        assert "\n\n" not in passage.text  # never across a blank line
        assert len(passage.text) <= WINDOW_CHARS or "\n" not in passage.text
    starts = [p.start for p in passages(LONG_DOCUMENT)]
    assert starts == sorted(starts)


def test_passages_pack_consecutive_sentences_within_the_window() -> None:
    (title, body) = passages(DOCUMENT_TEXT, window=1000)
    assert title.text == "Cloudflare outage on November 18, 2025."
    assert body.text.startswith("At 11:05 a change") and body.text.endswith("were restored.")
    small = passages(DOCUMENT_TEXT, window=120)
    assert len(small) > 2
    assert all(DOCUMENT_TEXT[p.start : p.end] == p.text for p in small)


def test_candidates_are_deterministic() -> None:
    first = candidates("detection_method", LONG_DOCUMENT)
    assert first == candidates("detection_method", LONG_DOCUMENT)


def test_candidates_are_chosen_by_field_cues_and_kept_in_document_order() -> None:
    detection = candidates("detection_method", LONG_DOCUMENT)
    assert MIN_CANDIDATES <= len(detection) <= MAX_CANDIDATES
    assert [c.start for c in detection] == sorted(c.start for c in detection)
    paged = [c for c in detection if "paged" in c.text]
    assert paged and {"paged", "alert*", "opened"} <= set(paged[0].cues)
    assert not any("weather was fine" in c.text for c in detection)

    trigger = candidates("trigger", LONG_DOCUMENT)
    assert any("configuration change was deployed" in c.text for c in trigger)

    remediations = candidates("remediations", LONG_DOCUMENT)
    assert any(c.text.startswith("Remediation.") for c in remediations)

    mitigated = candidates("mitigated_at", LONG_DOCUMENT)
    rolled_back = [c for c in mitigated if "rolled back" in c.text]
    assert rolled_back and "clock time" in rolled_back[0].cues


def test_every_candidate_is_a_verbatim_span_of_the_document() -> None:
    for field in RECORD_FIELDS:
        for c in candidates(field, LONG_DOCUMENT):
            assert LONG_DOCUMENT[c.start : c.end] == c.text


def test_lead_passages_fill_in_when_few_cues_match() -> None:
    document = "\n\n".join(["First paragraph.", "Second paragraph.", "Third.", "Fourth."])
    chosen = candidates("vendor_org", document)  # no org cue matches
    assert [c.text for c in chosen] == ["First paragraph.", "Second paragraph.", "Third."]
    assert all(c.cues == () for c in chosen)


def test_short_documents_yield_what_exists() -> None:
    assert candidates("trigger", "") == []
    (only,) = candidates("trigger", "One line, no cue.")
    assert only.text == "One line, no cue."


@pytest.mark.parametrize("field", RECORD_FIELDS)
def test_at_most_five_candidates_for_a_long_document(field: str) -> None:
    chosen = candidates(field, LONG_DOCUMENT)
    assert MIN_CANDIDATES <= len(chosen) <= MAX_CANDIDATES
