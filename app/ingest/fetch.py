"""Fetch a source URL over HTTP.

Classifies failures per ADR-003 §4: a 404/410 (or any other 4xx that isn't
a rate limit) is permanent — the URL doesn't exist, retrying won't help.
Timeouts, connection errors, and 5xx responses are transient — the job goes
back to `queued`.
"""

import dataclasses

import httpx

from app.ingest.errors import PermanentIngestError, TransientIngestError

FETCH_TIMEOUT_SECONDS = 30.0
USER_AGENT = "incident-intelligence-pipeline/0.1 (+M2 ingest worker)"

# 429 is excluded: rate limiting is transient (retry after backoff), not a
# permanent rejection of the URL.
_PERMANENT_STATUS_CODES = {400, 401, 403, 404, 405, 410, 451}


@dataclasses.dataclass(frozen=True)
class FetchedDocument:
    url: str
    content_type: str | None
    raw_bytes: bytes


def fetch(url: str) -> FetchedDocument:
    """Fetch `url`, returning raw bytes and the response Content-Type.

    Raises `PermanentIngestError` for client errors that mean the resource
    is genuinely gone/forbidden, `TransientIngestError` for everything else
    that might succeed on retry (timeouts, connection errors, 5xx, redirects
    exhausted).
    """
    try:
        response = httpx.get(
            url,
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except httpx.TimeoutException as exc:
        raise TransientIngestError(f"timeout fetching {url}: {exc}") from exc
    except httpx.TransportError as exc:
        # DNS failures, connection refused, TLS errors, too-many-redirects.
        # Bucketed as transient: most are as likely to be a local network
        # blip as a permanently broken URL, and ADR-003's retry budget
        # (settings.job_max_attempts) bounds the cost of guessing wrong.
        raise TransientIngestError(f"transport error fetching {url}: {exc}") from exc

    if response.status_code in _PERMANENT_STATUS_CODES:
        raise PermanentIngestError(
            f"permanent HTTP error {response.status_code} fetching {url}"
        )
    if response.is_client_error or response.is_server_error:
        raise TransientIngestError(
            f"HTTP error {response.status_code} fetching {url}"
        )

    return FetchedDocument(
        url=str(response.url),
        content_type=response.headers.get("content-type"),
        raw_bytes=response.content,
    )
