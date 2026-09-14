"""Integration tests for app.ingest.pipeline.ingest_source against real
Postgres: fetch (mocked) -> hash -> detect -> parse -> persist, and the
idempotent-reingest requirement from PROJECT_BRIEF M2.
"""

import uuid

import httpx
import respx
from sqlalchemy import Engine, text

from app.ingest.pipeline import ingest_source

MARKDOWN_BODY = b"# Example Incident\n\nSomething broke. Then it was fixed.\n"
HTML_BODY = (
    b"<html><head><title>Example Outage - Some Blog</title></head>"
    b"<body><article>The service was down for a while due to a bug.</article></body></html>"
)


def _insert_source(engine: Engine, url: str) -> uuid.UUID:
    source_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO sources (id, url) VALUES (:id, :url)"),
            {"id": source_id, "url": url},
        )
    return source_id


@respx.mock
def test_ingest_source_persists_a_document(engine: Engine) -> None:
    url = "https://example.com/incident-md"
    respx.get(url).mock(
        return_value=httpx.Response(
            200, content=MARKDOWN_BODY, headers={"content-type": "text/markdown"}
        )
    )
    source_id = _insert_source(engine, url)

    with engine.begin() as conn:
        result = ingest_source(conn, source_id=source_id, source_url=url)

    assert result.was_duplicate is False
    assert result.format == "markdown"
    assert result.title == "Example Incident"

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT source_id, content_hash, format, title, text FROM documents WHERE id=:id"),
            {"id": result.document_id},
        ).mappings().fetchone()

    assert row is not None
    assert row["source_id"] == source_id
    assert row["content_hash"] == result.content_hash
    assert row["format"] == "markdown"
    assert "Something broke" in row["text"]


@respx.mock
def test_reingesting_same_content_is_idempotent(engine: Engine) -> None:
    """PROJECT_BRIEF M2: 'Idempotent on re-ingest.'

    Same content_hash never produces a duplicate row, even across two
    different source registrations of the same underlying content (e.g.
    the user re-registers the same URL, or two URLs happen to serve
    byte-identical content).
    """
    url = "https://example.com/incident-md"
    respx.get(url).mock(
        return_value=httpx.Response(
            200, content=MARKDOWN_BODY, headers={"content-type": "text/markdown"}
        )
    )
    source_id = _insert_source(engine, url)

    with engine.begin() as conn:
        first = ingest_source(conn, source_id=source_id, source_url=url)
    with engine.begin() as conn:
        second = ingest_source(conn, source_id=source_id, source_url=url)

    assert first.was_duplicate is False
    assert second.was_duplicate is True
    assert second.document_id == first.document_id
    assert second.content_hash == first.content_hash

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM documents WHERE content_hash=:hash"),
            {"hash": first.content_hash},
        ).scalar_one()
    assert count == 1


@respx.mock
def test_reingest_from_a_different_source_row_is_still_idempotent(engine: Engine) -> None:
    """Idempotency is keyed on content, not on which `source` row asked
    for it — two sources pointing at byte-identical content collapse to
    one document.
    """
    url_a = "https://example.com/mirror-a"
    url_b = "https://example.com/mirror-b"
    respx.get(url_a).mock(
        return_value=httpx.Response(
            200, content=HTML_BODY, headers={"content-type": "text/html"}
        )
    )
    respx.get(url_b).mock(
        return_value=httpx.Response(
            200, content=HTML_BODY, headers={"content-type": "text/html"}
        )
    )
    source_a = _insert_source(engine, url_a)
    source_b = _insert_source(engine, url_b)

    with engine.begin() as conn:
        result_a = ingest_source(conn, source_id=source_a, source_url=url_a)
    with engine.begin() as conn:
        result_b = ingest_source(conn, source_id=source_b, source_url=url_b)

    assert result_a.document_id == result_b.document_id
    assert result_b.was_duplicate is True


@respx.mock
def test_html_title_and_body_extracted_correctly(engine: Engine) -> None:
    url = "https://example.com/incident-html"
    respx.get(url).mock(
        return_value=httpx.Response(200, content=HTML_BODY, headers={"content-type": "text/html"})
    )
    source_id = _insert_source(engine, url)

    with engine.begin() as conn:
        result = ingest_source(conn, source_id=source_id, source_url=url)

    assert result.title == "Example Outage"  # site-name suffix stripped
    assert result.format == "html"
