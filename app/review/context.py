"""Source-text context for judging one field (ADR-010 §1: "enough
surrounding source context to judge that field without re-reviewing
unrelated fields", and the M4 UI brief: without opening the original).

Two parts:
  quotes_for   the phrases the extraction itself cites as evidence for a
               field — `quote` on trigger/mechanism/time anchors/blast-
               radius quantities, `detection_quote` for detection_method,
               and the author's own phrase for the time_to_*_text fields.
  snippets     each quote located in the document text with a window of
               surrounding characters, or marked not found. A quote that
               cannot be located is itself a signal to the reviewer: the
               model may have paraphrased or invented it.

Fields with no citable quote (summary, affected, contributing_factors,
mitigations, remediations, ...) get no snippets; the UI and API carry the
full document text alongside, so those are judged against the whole.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

DEFAULT_RADIUS = 350

_ANCHOR_FIELDS = ("change_at", "impact_start", "detected_at", "mitigated_at", "resolved_at")


@dataclasses.dataclass(frozen=True)
class Snippet:
    quote: str
    found: bool
    before: str
    match: str
    after: str
    start: int | None  # character offset of `match` in the document text


def quotes_for(field: str, record: dict[str, Any]) -> list[str]:
    """The evidence phrases the record cites for `field`, in record order,
    de-duplicated, empty strings dropped."""
    value = record.get(field)
    quotes: list[str | None] = []
    if field in ("trigger", "mechanism") or field in _ANCHOR_FIELDS:
        if isinstance(value, dict):
            quotes.append(value.get("quote"))
    elif field in ("detection_method", "detection_quote"):
        quotes.append(record.get("detection_quote"))
    elif field == "blast_radius":
        if isinstance(value, dict):
            quotes.extend(q.get("quote") for q in value.get("quantitative", []) or [])
    elif field in ("time_to_detect_text", "time_to_mitigate_text", "title"):
        quotes.append(value if isinstance(value, str) else None)

    seen: set[str] = set()
    out: list[str] = []
    for quote in quotes:
        if isinstance(quote, str) and quote.strip() and quote not in seen:
            seen.add(quote)
            out.append(quote)
    return out


def _locate(document: str, quote: str) -> tuple[int, int] | None:
    """Exact match first; then case-insensitive with any whitespace run in
    the quote matching any whitespace run in the text (parsers collapse
    and re-wrap whitespace differently from what the model saw)."""
    start = document.find(quote)
    if start >= 0:
        return start, start + len(quote)
    words = quote.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(word) for word in words)
    found = re.search(pattern, document, re.IGNORECASE)
    if found is None:
        return None
    return found.start(), found.end()


def snippets(document: str, quotes: list[str], *, radius: int = DEFAULT_RADIUS) -> list[Snippet]:
    out: list[Snippet] = []
    for quote in quotes:
        span = _locate(document, quote)
        if span is None:
            out.append(Snippet(quote=quote, found=False, before="", match="", after="", start=None))
            continue
        start, end = span
        out.append(
            Snippet(
                quote=quote,
                found=True,
                before=document[max(0, start - radius) : start],
                match=document[start:end],
                after=document[end : end + radius],
                start=start,
            )
        )
    return out
