"""Source-text context: which quotes a field cites, and locating them."""

from app.review.context import quotes_for, snippets
from tests.fake_llm import valid_output
from tests.review_support import DOCUMENT_TEXT

RECORD = valid_output()["record"]


def test_quotes_cited_per_field() -> None:
    assert quotes_for("trigger", RECORD) == ["a change to one of our database systems' permissions"]
    assert quotes_for("mechanism", RECORD) == ["the software panicked"]
    assert quotes_for("detection_method", RECORD) == ["our first automated test detected the issue"]
    assert quotes_for("detection_quote", RECORD) == ["our first automated test detected the issue"]
    assert quotes_for("detected_at", RECORD) == ["11:31 automated tests"]
    assert quotes_for("title", RECORD) == ["Cloudflare outage on November 18, 2025"]
    assert quotes_for("summary", RECORD) == []
    assert quotes_for("contributing_factors", RECORD) == []
    assert quotes_for("time_to_detect_text", RECORD) == []  # null in the fixture


def test_blast_radius_cites_each_quantity_quote() -> None:
    record = dict(RECORD)
    record["blast_radius"] = {
        "qualitative": "bad",
        "quantitative": [
            {"metric": "projects", "value": "5,000", "quote": "5,000 projects"},
            {"metric": "webhooks", "value": "~50%", "quote": None},
            {"metric": "again", "value": "5,000", "quote": "5,000 projects"},
        ],
    }
    assert quotes_for("blast_radius", record) == ["5,000 projects"]


def test_null_field_cites_nothing() -> None:
    record = dict(RECORD)
    record["trigger"] = None
    assert quotes_for("trigger", record) == []


def test_snippet_locates_exact_quote_with_surrounding_text() -> None:
    (snippet,) = snippets(DOCUMENT_TEXT, ["the software panicked"], radius=20)
    assert snippet.found is True
    assert snippet.match == "the software panicked"
    assert snippet.before.endswith("its limit ")
    assert snippet.after.startswith(" and the core proxy")
    assert len(snippet.before) <= 20 and len(snippet.after) <= 20
    assert snippet.start == DOCUMENT_TEXT.index("the software panicked")


def test_snippet_falls_back_to_case_and_whitespace_insensitive_match() -> None:
    (snippet,) = snippets(DOCUMENT_TEXT, ["THE  software\npanicked"])
    assert snippet.found is True
    assert snippet.match == "the software panicked"


def test_snippet_marks_quotes_that_are_not_in_the_source() -> None:
    (snippet,) = snippets(DOCUMENT_TEXT, ["a phrase the model invented"])
    assert snippet.found is False
    assert snippet.start is None
    assert snippet.quote == "a phrase the model invented"


def test_snippet_window_clamps_at_document_edges() -> None:
    (snippet,) = snippets(DOCUMENT_TEXT, ["Cloudflare outage"], radius=1000)
    assert snippet.before == ""
    assert snippet.after == DOCUMENT_TEXT[len("Cloudflare outage") :]
