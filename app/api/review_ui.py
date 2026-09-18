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

Corrections are entered in a form derived from the field's own Pydantic
type (app/review/forms.py) — labelled inputs per part, selects for closed
vocabularies, one line per item for string lists, add/remove rows for
lists of objects, an explicit null toggle — pre-filled with the current
value, so the common case is editing in place and the reviewer never has
to recall the wire format. The submitted inputs are parsed back into the
field's value and validated against its type before anything is written;
on failure the page re-renders with the error and the reviewer's own
input still in the form.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.db.engine import get_engine
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
from app.review.forms import bind, form_spec, parse
from app.review.queries import ReviewItem, load_for_review, next_for_review, queue_size
from app.settings import settings

router = APIRouter(prefix="/ui/review", tags=["review-ui"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

REVIEWER_COOKIE = "reviewer"
CORRECTION_ROOT = "v"  # the top-level input name; parts are "v.label", "v.0.text", ...

_NOT_SUBMITTED = object()


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _render(
    request: Request,
    item: ReviewItem | None,
    *,
    error: str | None = None,
    correction: Any = _NOT_SUBMITTED,
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
        # The form shows the reviewer's own (rejected) input when there is
        # one, else the extracted value.
        current = item.review.model_value if correction is _NOT_SUBMITTED else correction
        context["form"] = bind(form_spec(item.review.field), CORRECTION_ROOT, current)
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


@router.post("/{field_review_id}", response_class=HTMLResponse)
async def decide(request: Request, field_review_id: uuid.UUID) -> Response:
    # The correction inputs are named by path and their number depends on
    # the field's type, so the whole form is read rather than declared
    # parameter by parameter. The database work stays synchronous like
    # the other handlers and runs off the event loop.
    form = await request.form()
    data = {key: value for key, value in form.items() if isinstance(value, str)}
    return await run_in_threadpool(_decide, request, field_review_id, data)


def _decide(request: Request, field_review_id: uuid.UUID, data: dict[str, str]) -> Response:
    action = data.get("action", "")
    reviewer = data.get("reviewer", "")
    note = data.get("note", "")
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
            value = parse(form_spec(item.review.field), CORRECTION_ROOT, data)
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
            # Re-render with the reviewer's own input bound into the form.
            return _render(
                request,
                item,
                error=str(exc),
                correction=value if action == "correct" else _NOT_SUBMITTED,
                status_code=422,
            )

    response = RedirectResponse(url=router.prefix, status_code=303)
    if reviewer.strip():
        response.set_cookie(REVIEWER_COOKIE, reviewer.strip(), max_age=60 * 60 * 24 * 365)
    return response
