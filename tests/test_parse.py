"""Parser tests against the real M0 spike corpus (spike/raw/*).

PROJECT_BRIEF's M2 instruction: "Test against spike/raw/* — all 10 must
parse." This file is that acceptance test, plus regression tests for each
FINDINGS.md §2 fix `parse()` is required to handle:
  §2.1 longest-article-candidate, not first (GitHub, Slack, GitLab)
  §2.2 title from metadata, not body (Cloudflare, Datadog, GitHub)
  §2.3 truncate related-post tails (GitHub, Roblox, Slack)
  §2.4 PDF page furniture stripped (CrowdStrike)
and the hard-failure rule: a silent empty extraction must raise, not
produce a `partial` record.
"""

import json
from pathlib import Path

import pytest

from app.ingest.detect import detect_format
from app.ingest.errors import EmptyExtractionError, PermanentIngestError
from app.ingest.parse import parse

SPIKE_ROOT = Path(__file__).parent.parent / "spike"
MANIFEST = json.loads((SPIKE_ROOT / "corpus_manifest.json").read_text(encoding="utf-8"))

_FORMAT_MAP = {
    "blog_html": "html",
    "vendor_post_event_summary_html": "html",
    "status_page_html": "html",
    "pdf": "pdf",
    "markdown_in_repo": "markdown",
}


def _corpus_doc(doc_id: str) -> dict:
    for doc in MANIFEST["documents"]:
        if doc["id"] == doc_id:
            return doc
    raise KeyError(doc_id)


def _load_raw(doc_id: str) -> tuple[bytes, str, str]:
    """Return (raw_bytes, expected_fmt, url) for a corpus document id."""
    doc = _corpus_doc(doc_id)
    raw_bytes = (SPIKE_ROOT.parent / doc["raw_file"]).read_bytes()
    return raw_bytes, _FORMAT_MAP[doc["format"]], doc["url"]


ALL_DOC_IDS = [d["id"] for d in MANIFEST["documents"]]


@pytest.mark.parametrize("doc_id", ALL_DOC_IDS)
def test_all_ten_corpus_documents_parse(doc_id: str) -> None:
    """PROJECT_BRIEF M2: all 10 of spike/raw/* must parse."""
    raw_bytes, expected_fmt, url = _load_raw(doc_id)

    detected = detect_format(url=url, content_type=None, raw_bytes=raw_bytes)
    assert detected == expected_fmt, f"{doc_id}: detected {detected}, expected {expected_fmt}"

    parsed = parse(raw_bytes, fmt=detected)
    assert parsed.text.strip(), f"{doc_id}: parsed to empty text"
    assert len(parsed.text) > 200, f"{doc_id}: suspiciously short ({len(parsed.text)} chars)"


@pytest.mark.parametrize("doc_id", ALL_DOC_IDS)
def test_no_document_parses_to_empty_text(doc_id: str) -> None:
    """The hard-failure rule, checked positively: none of the 10 trip it."""
    raw_bytes, fmt, _ = _load_raw(doc_id)
    parsed = parse(raw_bytes, fmt=fmt)
    assert parsed.text != ""


def test_empty_html_raises_not_partial() -> None:
    """PROJECT_BRIEF M2: a silent empty extraction is a HARD FAILURE."""
    empty_page = b"<html><head><title>Nothing here</title></head><body></body></html>"
    with pytest.raises(EmptyExtractionError):
        parse(empty_page, fmt="html")


def test_whitespace_only_markdown_raises() -> None:
    with pytest.raises(EmptyExtractionError):
        parse(b"   \n\n\t  \n  ", fmt="markdown")


def test_unsupported_format_raises_permanent_error() -> None:
    with pytest.raises(PermanentIngestError):
        parse(b"whatever", fmt="docx")


# --- FINDINGS.md §2.1: longest-article-candidate, not first -----------------


def test_github_first_article_is_author_bio_not_incident_text() -> None:
    """FINDINGS.md §2.1: GitHub's first <article> is an author-bio card.

    v1 of the spike extractor returned 0 chars here with HTTP 200 and no
    error. The production parser must pick the longest candidate instead.
    """
    raw_bytes, fmt, _ = _load_raw("E")
    parsed = parse(raw_bytes, fmt=fmt)
    assert "43 second" in parsed.text or "Orchestrator" in parsed.text


def test_slack_first_article_is_related_post_not_incident_text() -> None:
    """FINDINGS.md §2.1: Slack's first <article> is a related-post card.

    v1 returned 124 chars here. The incident content mentions Vitess/Consul.
    """
    raw_bytes, fmt, _ = _load_raw("H")
    parsed = parse(raw_bytes, fmt=fmt)
    assert "Vitess" in parsed.text or "Consul" in parsed.text
    assert len(parsed.text) > 5000


def test_gitlab_falls_through_to_body_when_no_article_or_main() -> None:
    """FINDINGS.md §2.1: GitLab has neither <article> nor <main>."""
    raw_bytes, fmt, _ = _load_raw("B")
    parsed = parse(raw_bytes, fmt=fmt)
    assert "pg_basebackup" in parsed.text or "PostgreSQL" in parsed.text


# --- FINDINGS.md §2.2: title from metadata, not body -------------------------


def test_cloudflare_title_from_metadata_not_lost_with_stripped_header() -> None:
    """FINDINGS.md §2.2: A's <h1> lives in a stripped <header>; body-only
    extraction loses the title entirely unless it's read from <title>/
    og:title first.
    """
    raw_bytes, fmt, _ = _load_raw("A")
    parsed = parse(raw_bytes, fmt=fmt)
    assert parsed.title is not None
    assert "outage" in parsed.title.lower() or "cloudflare" in parsed.title.lower()


def test_github_title_strips_site_name_suffix() -> None:
    raw_bytes, fmt, _ = _load_raw("E")
    parsed = parse(raw_bytes, fmt=fmt)
    assert parsed.title is not None
    assert "github blog" not in parsed.title.lower()


def test_kubernetes_markdown_title_from_h1() -> None:
    raw_bytes, fmt, _ = _load_raw("J")
    parsed = parse(raw_bytes, fmt=fmt)
    assert parsed.title is not None
    assert "prow" in parsed.title.lower()


# --- FINDINGS.md §2.3: truncate related-post tails ---------------------------


def test_github_tail_does_not_leak_unrelated_incident_headlines() -> None:
    """FINDINGS.md §2.3: E's tail contains 'The August 17 outage, and the
    work ahead' and an availability-report headline from a different month
    — neither is about the October 21, 2018 incident this document covers.
    """
    raw_bytes, fmt, _ = _load_raw("E")
    parsed = parse(raw_bytes, fmt=fmt)
    assert "august 17 outage" not in parsed.text.lower()
    assert "availability report" not in parsed.text.lower()


def test_roblox_tail_does_not_leak_2026_related_headlines() -> None:
    raw_bytes, fmt, _ = _load_raw("I")
    parsed = parse(raw_bytes, fmt=fmt)
    assert "kafka platform" not in parsed.text.lower()
    assert "age-assurance" not in parsed.text.lower()


def test_slack_tail_does_not_leak_next_post_teaser() -> None:
    raw_bytes, fmt, _ = _load_raw("H")
    parsed = parse(raw_bytes, fmt=fmt)
    assert "continuous load testing" not in parsed.text.lower()
    assert "flaky tests at scale" not in parsed.text.lower()


# --- FINDINGS.md §2.4: PDF page furniture ------------------------------------


def test_crowdstrike_pdf_page_furniture_stripped() -> None:
    """FINDINGS.md §2.4: pypdf emits 'Page 4 of 12  2024-08-06' inline,
    splitting 'These fixes' from 'are being backported'.
    """
    raw_bytes, fmt, _ = _load_raw("F")
    parsed = parse(raw_bytes, fmt=fmt)
    assert "Page 4 of 12" not in parsed.text
    assert "are being backported" in parsed.text


def test_crowdstrike_pdf_text_survives_furniture_removal() -> None:
    raw_bytes, fmt, _ = _load_raw("F")
    parsed = parse(raw_bytes, fmt=fmt)
    assert "Channel File 291" in parsed.text
    assert "out-of-bounds" in parsed.text.lower()
