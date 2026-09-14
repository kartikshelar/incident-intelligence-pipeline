"""Format detection tests."""

import pytest

from app.ingest.detect import detect_format
from app.ingest.errors import PermanentIngestError


def test_detects_pdf_from_content_type() -> None:
    assert detect_format(url="https://x/y", content_type="application/pdf", raw_bytes=b"") == "pdf"


def test_detects_html_from_content_type() -> None:
    fmt = detect_format(
        url="https://x/y", content_type="text/html; charset=utf-8", raw_bytes=b""
    )
    assert fmt == "html"


def test_detects_pdf_from_magic_bytes_when_content_type_missing() -> None:
    fmt = detect_format(url="https://x/y", content_type=None, raw_bytes=b"%PDF-1.4 ...")
    assert fmt == "pdf"


def test_detects_pdf_from_magic_bytes_over_misleading_content_type() -> None:
    # application/octet-stream is common on misconfigured servers; sniff wins.
    fmt = detect_format(
        url="https://x/y", content_type="application/octet-stream", raw_bytes=b"%PDF-1.7"
    )
    assert fmt == "pdf"


def test_detects_markdown_from_url_extension() -> None:
    fmt = detect_format(
        url="https://raw.githubusercontent.com/org/repo/master/POSTMORTEM.md",
        content_type=None,
        raw_bytes=b"# Title\n\nBody",
    )
    assert fmt == "markdown"


def test_detects_html_from_doctype_sniff_with_no_extension() -> None:
    fmt = detect_format(
        url="https://status.example.com/incidents/abc123",
        content_type=None,
        raw_bytes=b"<!DOCTYPE html><html><body>hi</body></html>",
    )
    assert fmt == "html"


def test_url_query_string_and_fragment_do_not_break_extension_check() -> None:
    fmt = detect_format(
        url="https://raw.githubusercontent.com/org/repo/master/POST.md?raw=true#section",
        content_type=None,
        raw_bytes=b"# Title",
    )
    assert fmt == "markdown"


def test_bare_text_with_no_signature_falls_back_to_markdown() -> None:
    fmt = detect_format(
        url="https://example.com/incident-report",
        content_type="text/plain",
        raw_bytes=b"Plain prose incident report with no markup at all.",
    )
    assert fmt == "markdown"


def test_unrecognizable_empty_content_raises_permanent_error() -> None:
    with pytest.raises(PermanentIngestError):
        detect_format(url="https://example.com/incident", content_type=None, raw_bytes=b"")
