"""Minimal server-rendered review UI (M4). No SPA, no build step.

  GET  /ui/review        the next field in the queue (or "queue empty")
  GET  /ui/review/{id}   one specific field
  POST /ui/review/{id}   the decision form: accept / correct / skip

One field per page: the field's definition read from the schema itself
(app/review/definitions.py), its current value, self-reported confidence,
three to five candidate passages chosen from the source by field-specific
keyword cues (app/review/candidates.py), and the full normalised document
text below with a client-side text search (match count, jump between
matches, "find in source" from each candidate; inline script, no build
step). The page does NOT locate or highlight the quote the
extraction cites: candidate selection never sees the record, so the
reviewer judges the value against the source rather than against the
model's justification — corrections are gold-set input and must be
independent (the rationale is in candidates.py). The one thing said about
a cited quote is when it cannot be found in the source at all. No bulk
actions — every decision is one field, one form post.

The reviewer's name is a plain text input remembered in a cookie so it is
not retyped per field. That is identity for provenance, not
authentication (PROJECT_BRIEF §3: no auth theater).

Corrections are entered as JSON (the textarea is pre-filled with the
current value, so the common case is editing in place) and validated
against the field's own type before anything is written; a plain string
for a string-typed field is accepted as-is so an enum value does not have
to be quoted.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, cast, get_args, get_origin

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.engine import get_engine
from app.extract.schema import IncidentRecord
from app.review.candidates import candidates
from app.review.context import quotes_for, snippets
from app.review.definitions import field_definition
from app.review.fields import (
    Action,
    AlreadyReviewedError,
    FieldReviewNotFoundError,
    InvalidCorrectionError,
    NotRoutedError,
    record_decision,
)
from app.review.queries import ReviewItem, load_for_review, next_for_review, queue_size
from app.settings import settings

router = APIRouter(prefix="/ui/review", tags=["review-ui"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

REVIEWER_COOKIE = "reviewer"


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _render(
    request: Request,
    item: ReviewItem | None,
    *,
    error: str | None = None,
    submitted_value: str | None = None,
    status_code: int = 200,
) -> Response:
    engine = get_engine()
    with engine.connect() as conn:
        size = queue_size(conn)
    context: dict[str, Any] = {
        "item": item,
        "queue_size": size,
        "floor": settings.review_confidence_floor,
        "budget": settings.review_budget,
        "reviewer": request.cookies.get(REVIEWER_COOKIE, ""),
        "error": error,
    }
    if item is not None:
        context["value_json"] = _pretty(item.review.model_value)
        context["corrected_json"] = (
            submitted_value if submitted_value is not None else context["value_json"]
        )
        context["definition"] = field_definition(item.review.field)
        # (field, text) only — never the record. See candidates.py.
        context["candidates"] = candidates(item.review.field, item.document_text)
        # Located quotes are deliberately not rendered; only the ones that
        # are NOT in the source are surfaced, as a provenance warning.
        located = snippets(item.document_text, quotes_for(item.review.field, item.record))
        context["missing_quotes"] = [s.quote for s in located if not s.found]
    return templates.TemplateResponse(request, "review.html", context, status_code=status_code)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def next_field(request: Request) -> Response:
    engine = get_engine()
    with engine.connect() as conn:
        queued = next_for_review(conn)
        item = load_for_review(conn, queued.id) if queued is not None else None
    return _render(request, item)


@router.get("/{field_review_id}", response_class=HTMLResponse)
def one_field(request: Request, field_review_id: uuid.UUID) -> Response:
    engine = get_engine()
    with engine.connect() as conn:
        try:
            item = load_for_review(conn, field_review_id)
        except FieldReviewNotFoundError:
            return _render(request, None, error="That field does not exist.", status_code=404)
    return _render(request, item)


def _is_text_field(field: str) -> bool:
    """True for fields typed str, str | None, or a string enum (Literal)."""
    annotation = IncidentRecord.model_fields[field].annotation
    options = (
        get_args(annotation) if get_origin(annotation) in (Union, UnionType) else (annotation,)
    )
    return any(option is str or get_origin(option) is Literal for option in options)


def _parse_correction(field: str, raw: str) -> Any:
    """The textarea holds JSON. For a string-typed field, unquoted text is
    taken as the string itself so `monitoring` works without quotes."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if _is_text_field(field):
            return raw.strip()
        raise


@router.post("/{field_review_id}", response_class=HTMLResponse)
def decide(
    request: Request,
    field_review_id: uuid.UUID,
    action: str = Form(...),
    reviewer: str = Form(default=""),
    corrected_value: str = Form(default=""),
    note: str = Form(default=""),
) -> Response:
    if action not in ("accept", "correct", "skip"):
        return _render(request, None, error=f"Unknown action {action!r}.", status_code=422)

    engine = get_engine()
    with engine.begin() as conn:
        try:
            item = load_for_review(conn, field_review_id)
        except FieldReviewNotFoundError:
            return _render(request, None, error="That field does not exist.", status_code=404)

        value: Any = None
        if action == "correct":
            try:
                value = _parse_correction(item.review.field, corrected_value)
            except json.JSONDecodeError as exc:
                return _render(
                    request,
                    item,
                    error=f"Corrected value is not valid JSON: {exc.msg} at position {exc.pos}.",
                    submitted_value=corrected_value,
                    status_code=422,
                )
        try:
            record_decision(
                conn,
                field_review_id,
                action=cast(Action, action),  # validated above
                reviewer=reviewer,
                corrected_value=value,
                note=note.strip() or None,
            )
        except (AlreadyReviewedError, NotRoutedError) as exc:
            return _render(request, item, error=str(exc), status_code=409)
        except InvalidCorrectionError as exc:
            return _render(
                request, item, error=str(exc), submitted_value=corrected_value, status_code=422
            )

    response = RedirectResponse(url=router.prefix, status_code=303)
    if reviewer.strip():
        response.set_cookie(REVIEWER_COOKIE, reviewer.strip(), max_age=60 * 60 * 24 * 365)
    return response
