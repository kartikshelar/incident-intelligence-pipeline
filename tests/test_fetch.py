"""Fetch tests, mocking HTTP with respx (no real network calls)."""

import httpx
import pytest
import respx

from app.ingest.errors import PermanentIngestError, TransientIngestError
from app.ingest.fetch import fetch


@respx.mock
def test_fetch_returns_bytes_and_content_type_on_success() -> None:
    respx.get("https://example.com/post").mock(
        return_value=httpx.Response(200, content=b"hello", headers={"content-type": "text/html"})
    )
    result = fetch("https://example.com/post")
    assert result.raw_bytes == b"hello"
    assert result.content_type == "text/html"
    assert result.url == "https://example.com/post"


@respx.mock
def test_fetch_404_is_permanent() -> None:
    respx.get("https://example.com/gone").mock(return_value=httpx.Response(404))
    with pytest.raises(PermanentIngestError):
        fetch("https://example.com/gone")


@respx.mock
def test_fetch_403_is_permanent() -> None:
    respx.get("https://example.com/forbidden").mock(return_value=httpx.Response(403))
    with pytest.raises(PermanentIngestError):
        fetch("https://example.com/forbidden")


@respx.mock
def test_fetch_500_is_transient() -> None:
    respx.get("https://example.com/broken").mock(return_value=httpx.Response(500))
    with pytest.raises(TransientIngestError):
        fetch("https://example.com/broken")


@respx.mock
def test_fetch_429_is_transient_not_permanent() -> None:
    respx.get("https://example.com/limited").mock(return_value=httpx.Response(429))
    with pytest.raises(TransientIngestError):
        fetch("https://example.com/limited")


@respx.mock
def test_fetch_timeout_is_transient() -> None:
    respx.get("https://example.com/slow").mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(TransientIngestError):
        fetch("https://example.com/slow")


@respx.mock
def test_fetch_connection_error_is_transient() -> None:
    respx.get("https://example.com/down").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(TransientIngestError):
        fetch("https://example.com/down")


@respx.mock
def test_fetch_follows_redirects_and_records_final_url() -> None:
    respx.get("https://example.com/old").mock(
        return_value=httpx.Response(301, headers={"location": "https://example.com/new"})
    )
    respx.get("https://example.com/new").mock(
        return_value=httpx.Response(200, content=b"moved", headers={"content-type": "text/html"})
    )
    result = fetch("https://example.com/old")
    assert result.url == "https://example.com/new"
    assert result.raw_bytes == b"moved"
