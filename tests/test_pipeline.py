"""Integration tests for app.ingest.pipeline.ingest_source against real
Postgres: fetch (mocked) -> hash -> detect -> parse -> persist, the
idempotent-reingest requirement from PROJECT_BRIEF M2, and document
identity over extracted text (ADR-007).
"""

import hashlib
import uuid

import httpx
import pytest
import respx
from sqlalchemy import Engine, text

from app.ingest.pipeline import ingest_source, text_hash_of

MARKDOWN_BODY = b"# Example Incident\n\nSomething broke. Then it was fixed.\n"
HTML_BODY = (
    b"<html><head><title>Example Outage - Some Blog</title></head>"
    b"<body><article>The service was down for a while due to a bug.</article></body></html>"
)


_BODY = "The service was down for a while due to a bug."


def _html_with_nonce(nonce: str, body: str = _BODY) -> bytes:
    """The AWS / Google Cloud shape from ADR-007: a per-response nonce in a
    CSP meta tag and a script tag, both outside the extracted text."""
    return (
        "<html><head><title>Example Outage - Some Blog</title>"
        f'<meta http-equiv="Content-Security-Policy" content="script-src \'nonce-{nonce}\'">'
        f'<script nonce="{nonce}">window.__x = 1;</script></head>'
        f"<body><article>{body}</article></body></html>"
    ).encode()


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
    assert result.provenance_updated is False


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
    assert second.provenance_updated is False
    assert second.document_id == first.document_id
    assert second.content_hash == first.content_hash

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM documents WHERE content_hash=:hash"),
            {"hash": first.content_hash},
        ).scalar_one()
    assert count == 1


# --- ADR-007: document identity is the extracted text -------------------------


def _document_row(engine: Engine, document_id: uuid.UUID) -> dict:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT source_url, content_hash, text_hash, raw_bytes, text, title, "
                    "fetched_at, created_at FROM documents WHERE id=:id"
                ),
                {"id": document_id},
            )
            .mappings()
            .fetchone()
        )
    assert row is not None
    return dict(row)


def _count_documents(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM documents")).scalar_one()


@respx.mock
def test_text_hash_is_sha256_of_the_stored_text(engine: Engine) -> None:
    url = "https://example.com/hashes"
    respx.get(url).mock(
        return_value=httpx.Response(200, content=HTML_BODY, headers={"content-type": "text/html"})
    )
    with engine.begin() as conn:
        result = ingest_source(conn, source_id=_insert_source(engine, url), source_url=url)

    row = _document_row(engine, result.document_id)
    assert row["content_hash"] == hashlib.sha256(HTML_BODY).hexdigest()
    assert row["text_hash"] == hashlib.sha256(row["text"].encode("utf-8")).hexdigest()
    assert row["text_hash"] == text_hash_of(row["text"]) == result.text_hash
    assert row["text_hash"] != row["content_hash"]


@respx.mock
def test_same_text_different_bytes_is_one_document_with_updated_provenance(
    engine: Engine,
) -> None:
    """ADR-007, the AWS / Google Cloud case: the page is re-served with new
    nonces, so the raw hash changes while the extracted text does not.
    That is the same document. No new row; the existing row's provenance
    (final URL, fetched_at, content_hash, raw_bytes) moves to this fetch."""
    url_a = "https://example.com/pm"
    url_b = "https://example.com/blog/pm-republished"  # the page moved; new final URL
    bytes_a, bytes_b = _html_with_nonce("qC6cJtZbjdqNuWy3"), _html_with_nonce("0XXU0l8etiwNXk")
    assert bytes_a != bytes_b
    respx.get(url_a).mock(
        return_value=httpx.Response(200, content=bytes_a, headers={"content-type": "text/html"})
    )
    respx.get(url_b).mock(
        return_value=httpx.Response(200, content=bytes_b, headers={"content-type": "text/html"})
    )

    with engine.begin() as conn:
        first = ingest_source(conn, source_id=_insert_source(engine, url_a), source_url=url_a)
    before = _document_row(engine, first.document_id)
    with engine.begin() as conn:
        second = ingest_source(conn, source_id=_insert_source(engine, url_b), source_url=url_b)

    assert second.document_id == first.document_id
    assert second.was_duplicate is True
    assert second.provenance_updated is True
    assert second.text_hash == first.text_hash
    assert second.content_hash != first.content_hash
    assert _count_documents(engine) == 1

    after = _document_row(engine, first.document_id)
    # Provenance follows the latest fetch...
    assert after["content_hash"] == second.content_hash == hashlib.sha256(bytes_b).hexdigest()
    assert bytes(after["raw_bytes"]) == bytes_b
    assert after["source_url"] == url_b
    assert after["fetched_at"] > before["fetched_at"]
    # ...identity does not.
    assert after["text_hash"] == before["text_hash"]
    assert after["text"] == before["text"]
    assert after["title"] == before["title"]
    assert after["created_at"] == before["created_at"]


@respx.mock
def test_same_bytes_leaves_provenance_untouched(engine: Engine) -> None:
    url = "https://example.com/stable"
    respx.get(url).mock(
        return_value=httpx.Response(200, content=HTML_BODY, headers={"content-type": "text/html"})
    )
    source_id = _insert_source(engine, url)
    with engine.begin() as conn:
        first = ingest_source(conn, source_id=source_id, source_url=url)
    before = _document_row(engine, first.document_id)
    with engine.begin() as conn:
        second = ingest_source(conn, source_id=source_id, source_url=url)

    assert second.provenance_updated is False
    assert _document_row(engine, first.document_id) == before


@respx.mock
def test_different_text_is_a_second_document(engine: Engine) -> None:
    """Same chrome, different body: a different document, even from the same URL."""
    url = "https://example.com/edited"
    respx.get(url).mock(
        side_effect=[
            httpx.Response(
                200, content=_html_with_nonce("n1"), headers={"content-type": "text/html"}
            ),
            httpx.Response(
                200,
                content=_html_with_nonce("n1", body="The service was down; a corrected account."),
                headers={"content-type": "text/html"},
            ),
        ]
    )
    source_id = _insert_source(engine, url)
    with engine.begin() as conn:
        first = ingest_source(conn, source_id=source_id, source_url=url)
    with engine.begin() as conn:
        second = ingest_source(conn, source_id=source_id, source_url=url)

    assert second.was_duplicate is False
    assert second.provenance_updated is False
    assert second.document_id != first.document_id
    assert second.text_hash != first.text_hash
    assert _count_documents(engine) == 2
    # The first document is untouched by the second ingest.
    assert _document_row(engine, first.document_id)["content_hash"] == first.content_hash


def test_text_hash_is_unique_at_the_database(engine: Engine) -> None:
    source_id = _insert_source(engine, "https://example.com/x")
    params = {
        "source_id": source_id,
        "text_hash": "t" * 64,
        "text": "same text",
    }
    insert = text(
        "INSERT INTO documents (id, source_id, source_url, content_hash, text_hash, format, "
        "fetched_at, raw_bytes, text) VALUES (:id, :source_id, 'https://example.com/x', "
        ":content_hash, :text_hash, 'html', now(), '', :text)"
    )
    with engine.begin() as conn:
        conn.execute(insert, {**params, "id": uuid.uuid4(), "content_hash": "a" * 64})
    with engine.begin() as conn, pytest.raises(Exception, match="documents_text_hash_key"):
        conn.execute(insert, {**params, "id": uuid.uuid4(), "content_hash": "b" * 64})


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
