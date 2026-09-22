"""Pure-logic pieces of scripts/load_test.py: no DB, no worker, no model.

The script itself is validated by running it (same convention as every
other scripts/*.py in this repo — none have a dedicated test file; their
JSON report is the artifact). These are the two places that are easy to
get subtly wrong with no other way to notice: the HTML marker injection
(silently invisible to app.ingest.parse's candidate selection is exactly
the bug this covers, see the module's own docstring for how it was
found) and the percentile helper duplicated between load_test.py and
latency_report.py.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from scripts import latency_report, load_test


def _extracted_text(html_bytes: bytes) -> str:
    """The same candidate selection app.ingest.parse._parse_html uses:
    largest of every <article>/<main>, else <body>."""
    soup = BeautifulSoup(html_bytes, "lxml")
    candidates = soup.find_all(["article", "main"])
    if not candidates:
        candidates = [soup.body or soup]
    node = max(candidates, key=lambda n: len(n.get_text()))
    return node.get_text("\n")


def test_marker_survives_extraction_when_body_has_no_article_or_main() -> None:
    html = b"<html><body><p>Some real content that is long.</p></body></html>"
    marked = load_test._with_marker(html, "text/html", 7)
    assert "Load test document marker 7." in _extracted_text(marked)


def test_marker_survives_extraction_when_article_is_selected() -> None:
    html = (
        b"<html><body>"
        b"<header>nav chrome</header>"
        b"<article>Real incident narrative long enough to be picked.</article>"
        b"</body></html>"
    )
    marked = load_test._with_marker(html, "text/html", 3)
    assert "Load test document marker 3." in _extracted_text(marked)


def test_marker_survives_extraction_when_main_is_the_larger_candidate() -> None:
    html = (
        b"<html><body>"
        b"<article>short</article>"
        b"<main>" + b"Real incident narrative, much longer than the article tag. " * 5 + b"</main>"
        b"</body></html>"
    )
    marked = load_test._with_marker(html, "text/html", 11)
    assert "Load test document marker 11." in _extracted_text(marked)


def test_marker_does_not_change_parsed_content_type_marker_for_markdown() -> None:
    original = b"# Title\n\nSome incident text.\n"
    marked = load_test._with_marker(original, "text/markdown", 2)
    assert marked.decode() == original.decode() + "\n\nLoad test document marker 2.\n"


def test_marker_is_a_documented_noop_for_pdf() -> None:
    raw = b"%PDF-1.4\n...\n%%EOF"
    assert load_test._with_marker(raw, "application/pdf", 5) == raw


def test_fixture_bodies_cycle_through_all_ten_fixtures() -> None:
    bodies = load_test._fixture_bodies(25)
    assert len(bodies) == 25
    urls = list(bodies)
    # Document 0 and document 10 use the same fixture (index 0 % 10).
    fixture_0_url = load_test.FIXTURES[0][2]
    assert f"{fixture_0_url}?doc=0" in urls
    assert f"{fixture_0_url}?doc=10" in urls


def test_percentile_matches_between_the_two_scripts() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert load_test._percentile(values, 0.50) == latency_report._percentile(values, 0.50)
    assert load_test._percentile(values, 0.95) == latency_report._percentile(values, 0.95)


def test_percentile_p50_of_odd_length_is_the_middle_value() -> None:
    assert load_test._percentile([1.0, 2.0, 3.0], 0.50) == 2.0


def test_percentile_p95_is_between_max_and_second_highest_for_small_n() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    p95 = load_test._percentile(values, 0.95)
    assert 3.0 <= p95 <= 4.0


def test_find_duplicate_claims_is_empty_when_every_id_is_unique() -> None:
    assert load_test._find_duplicate_claims(["a", "b", "c"]) == []


def test_find_duplicate_claims_finds_a_single_repeat() -> None:
    assert load_test._find_duplicate_claims(["a", "b", "a"]) == ["a"]


def test_find_duplicate_claims_finds_every_repeated_id_sorted() -> None:
    # "b" appears three times but is still reported once.
    assert load_test._find_duplicate_claims(["c", "a", "b", "a", "b", "b"]) == ["a", "b"]


def test_find_duplicate_claims_handles_empty_input() -> None:
    assert load_test._find_duplicate_claims([]) == []
