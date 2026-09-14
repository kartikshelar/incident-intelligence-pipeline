"""Normalize markdown / HTML / PDF bytes to (title, text).

Every fix here is traced to a numbered finding in spike/FINDINGS.md §2,
found by running the M0 spike's throwaway extractor against the real
10-document corpus. This is that extractor's production replacement.

FINDINGS.md §2 items NOT handled here (explicitly out of scope for what
PROJECT_BRIEF's M2 line asked for — "handle at minimum" the four below):
  §2.5 inline-code-per-line: cosmetic, does not affect extraction quality.
  §2.6 status-page section authority / JSON feed: a schema/ingestion-source
       decision (see FINDINGS.md §7.2-3), not a text-normalization fix.
  §2.8 timezone normalization: a schema-typing decision for M3's typed
       time anchors (FINDINGS.md §4 item 1), not something to resolve by
       mangling the extracted text.
  §2.9 document != incident: a schema decision, out of scope for a
       document-level parser.
"""

from __future__ import annotations

import dataclasses
import re
from io import BytesIO

from bs4 import BeautifulSoup
from bs4.element import Tag
from pypdf import PdfReader

from app.ingest.errors import EmptyExtractionError, PermanentIngestError

# FINDINGS.md §2 item 3: related-post cards, "previous/next post", and
# newsletter signups leak other incidents' dates and headlines into the
# text. Truncate at the first line that is exactly one of these markers
# (case-insensitive), rather than searching for the substring anywhere,
# since incident prose legitimately contains words like "share" or
# "related" (e.g. Datadog: "share this article" is fine mid-sentence but
# GitHub's tail line "Related posts" on its own line is the boundary).
_TAIL_MARKERS = (
    "related posts",
    "related news",
    "recommended reading",
    "further reading",
    "previous post",
    "next post",
    "share this article",
    "we do newsletters, too",
)

_CHROME_TAGS = (
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "svg",
    "iframe",
    "form",
    "aside",
)

# FINDINGS.md §2 item 4: pypdf emits running headers/footers like
# "Page 4 of 12  2024-08-06" inline with body text, splitting sentences.
# The date here is the document's publication/revision date, not the
# incident date — stripping it (rather than trying to interpret it) avoids
# feeding a wrong date into any later date extraction.
_PDF_PAGE_FURNITURE_RE = re.compile(
    r"^\s*Page \d+ of \d+\s+\d{4}-\d{2}-\d{2}\s*$", re.MULTILINE
)


@dataclasses.dataclass(frozen=True)
class ParsedDocument:
    title: str | None
    text: str


def parse(raw_bytes: bytes, *, fmt: str) -> ParsedDocument:
    """Normalize `raw_bytes` (already known to be `fmt`) to (title, text).

    Raises `EmptyExtractionError` if normalization produces no usable text
    — PROJECT_BRIEF.md M2: "A silent empty extraction is a HARD FAILURE,
    not a partial record." Raises `PermanentIngestError` if `fmt` isn't one
    the parser recognizes (should not happen if `detect.detect_format` was
    used first, but the parser does not trust its caller blindly).
    """
    if fmt == "markdown":
        parsed = _parse_markdown(raw_bytes)
    elif fmt == "html":
        parsed = _parse_html(raw_bytes)
    elif fmt == "pdf":
        parsed = _parse_pdf(raw_bytes)
    else:
        raise PermanentIngestError(f"parse() does not support format {fmt!r}")

    if not parsed.text.strip():
        raise EmptyExtractionError(
            f"{fmt} parsing produced no text (raw {len(raw_bytes)} bytes)"
        )
    return parsed


def _truncate_at_tail_marker(text: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().lower() in _TAIL_MARKERS:
            return "\n".join(lines[:i]).strip()
    return text


def _parse_markdown(raw_bytes: bytes) -> ParsedDocument:
    text = raw_bytes.decode("utf-8", errors="replace")
    # Title from document metadata, never body text (FINDINGS.md §2.2): for
    # markdown, the closest thing to metadata is the first H1, since
    # markdown-in-a-repo (corpus doc J) has no separate <title> tag or
    # front matter in any of the M0 documents. A YAML front-matter `title:`
    # key would take priority if present; none of the corpus uses one.
    title = None
    front_matter_title = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text, re.MULTILINE)
    h1_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if front_matter_title:
        title = front_matter_title.group(1).strip()
    elif h1_match:
        title = h1_match.group(1).strip()

    text = _truncate_at_tail_marker(text)
    return ParsedDocument(title=title, text=text.strip())


def _parse_html(raw_bytes: bytes) -> ParsedDocument:
    soup = BeautifulSoup(raw_bytes, "lxml")

    # FINDINGS.md §2.2: title from <title>/og:title metadata, never from
    # the body — A/D/E's <h1> lives inside a stripped <header> and is lost
    # entirely if title extraction waits until after chrome stripping.
    title = _extract_html_title(soup)

    for tag in soup(_CHROME_TAGS):
        tag.decompose()

    # FINDINGS.md §2.1: "first <article>" silently returned an author-bio
    # card (GitHub) or a related-post card (Slack) with HTTP 200 and no
    # error. Fix: consider every <article>/<main> candidate and keep the
    # longest, falling back to <body> only if neither tag exists at all
    # (GitLab has neither).
    candidates: list[Tag] = soup.find_all(["article", "main"])
    if not candidates:
        candidates = [soup.body or soup]
    node = max(candidates, key=lambda n: len(n.get_text()))

    text = node.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    text = _truncate_at_tail_marker(text)

    return ParsedDocument(title=title, text=text)


def _extract_html_title(soup: BeautifulSoup) -> str | None:
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if isinstance(og_title, Tag):
        content = og_title.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    if soup.title and soup.title.string:
        raw_title = soup.title.string.strip()
        # Strip a trailing " - Site Name" / " | Site Name" suffix common on
        # blog platforms (e.g. corpus doc E: "October 21 post-incident
        # analysis - The GitHub Blog"), since the site name is chrome, not
        # part of the incident title.
        for sep in (" - ", " | ", " – "):
            if sep in raw_title:
                head, _, tail = raw_title.rpartition(sep)
                # Only strip if the tail looks like a site name (short,
                # capitalized), not if it's part of the actual title.
                if head and len(tail) < 40:
                    return head.strip()
        return raw_title

    return None


def _parse_pdf(raw_bytes: bytes) -> ParsedDocument:
    reader = PdfReader(BytesIO(raw_bytes))

    title = None
    if reader.metadata and reader.metadata.title:
        title = reader.metadata.title.strip() or None

    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n\n".join(pages)

    # FINDINGS.md §2.4: strip "Page N of M  YYYY-MM-DD" running furniture
    # injected mid-sentence between page boundaries.
    text = _PDF_PAGE_FURNITURE_RE.sub("", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()

    return ParsedDocument(title=title, text=text)
