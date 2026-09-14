"""Detect which of the three M2 formats a fetched document is.

Content-Type is authoritative when present and recognized (a server that
says `application/pdf` is telling the truth far more reliably than a URL
that happens to end in something else). Falls back to the URL's extension,
then to content sniffing (PDF magic bytes; HTML tag sniff), because several
of the spike corpus URLs serve HTML with no extension and status pages can
omit or misreport Content-Type.
"""

from app.ingest.errors import PermanentIngestError

SUPPORTED_FORMATS = ("markdown", "html", "pdf")

_PDF_MAGIC = b"%PDF-"


def detect_format(*, url: str, content_type: str | None, raw_bytes: bytes) -> str:
    """Return one of `SUPPORTED_FORMATS`, or raise `PermanentIngestError`.

    An unrecognized format is permanent, not transient: refetching the same
    URL will not change what kind of document it is.
    """
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type == "application/pdf":
            return "pdf"
        if media_type in ("text/html", "application/xhtml+xml"):
            return "html"
        if media_type in ("text/markdown", "text/x-markdown"):
            return "markdown"
        # text/plain and application/octet-stream are common for raw
        # markdown served from repos (e.g. raw.githubusercontent.com) —
        # not authoritative on their own, fall through to sniffing.

    if raw_bytes.startswith(_PDF_MAGIC):
        return "pdf"

    lower_url = url.lower().split("?", 1)[0].split("#", 1)[0]
    if lower_url.endswith((".md", ".markdown")):
        return "markdown"
    if lower_url.endswith(".pdf"):
        return "pdf"
    if lower_url.endswith((".html", ".htm")):
        return "html"

    sniff = raw_bytes[:2048].lstrip().lower()
    if sniff.startswith(b"<!doctype html") or sniff.startswith(b"<html") or b"<body" in sniff:
        return "html"

    # Bare text with no HTML/PDF signature and no markdown extension:
    # postmortems are occasionally served as plain .txt or extension-less
    # raw files (e.g. a repo's raw markdown proxied without a content
    # type). Markdown is the most conservative guess for prose text —
    # normalizing plain text as markdown is a no-op close to identity,
    # while treating it as HTML would run tag-stripping over prose that
    # was never markup and corrupt nothing but also fix nothing.
    if raw_bytes and not sniff.startswith(b"<"):
        return "markdown"

    raise PermanentIngestError(
        f"could not detect a supported format for {url} "
        f"(content_type={content_type!r})"
    )
