"""M4 review API (ADR-010): the queue, one field, a decision, corrections.

  GET  /review/queue                 routed fields in presentation order
  GET  /review/fields/{id}           one field: definition, value, candidate
                                     passages, cited-quote locations, source
  POST /review/fields/{id}/decision  accept / correct / skip
  POST /review/route                 run a routing pass at the configured
                                     floor and budget (the worker also runs
                                     one after every completed extraction)
  GET  /review/corrections           reviewed fields as gold-set input

Same conventions as the M1 handlers in app/api/main.py: Pydantic request
and response contracts, raw SQL through app.review, one transaction per
request. The floor and budget are read from app.settings on every call —
no endpoint accepts a threshold, so the operating point cannot be changed
per request; changing it is a configuration change (ADR-010 §3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db.engine import get_engine
from app.review.candidates import candidates
from app.review.context import quotes_for, snippets
from app.review.definitions import field_definition
from app.review.fields import (
    AlreadyReviewedError,
    FieldReview,
    FieldReviewNotFoundError,
    InvalidCorrectionError,
    NotRoutedError,
    record_decision,
)
from app.review.queries import list_queue, list_reviewed, load_for_review, queue_size
from app.review.routing import route_pending
from app.settings import settings

router = APIRouter(prefix="/review", tags=["review"])


class RoutingConfig(BaseModel):
    confidence_floor: float
    budget: int | None


class QueueItem(BaseModel):
    id: uuid.UUID
    extraction_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str | None
    source_url: str
    schema_version: str
    run_id: str
    field: str
    confidence: float
    routed_at: datetime | None
    skip_count: int


class QueueResponse(BaseModel):
    size: int
    routing: RoutingConfig
    items: list[QueueItem]


class SnippetOut(BaseModel):
    quote: str
    found: bool
    before: str
    match: str
    after: str
    start: int | None


class FieldReviewState(BaseModel):
    id: uuid.UUID
    extraction_id: uuid.UUID
    field: str
    confidence: float
    model_value: Any
    review_state: str
    routed_at: datetime | None
    skip_count: int
    decision: str | None
    corrected_value: Any
    reviewer: str | None
    reviewer_note: str | None
    reviewed_at: datetime | None

    @classmethod
    def from_review(cls, review: FieldReview) -> FieldReviewState:
        return cls(
            id=review.id,
            extraction_id=review.extraction_id,
            field=review.field,
            confidence=review.confidence,
            model_value=review.model_value,
            review_state=review.review_state,
            routed_at=review.routed_at,
            skip_count=review.skip_count,
            decision=review.decision,
            corrected_value=review.corrected_value,
            reviewer=review.reviewer,
            reviewer_note=review.reviewer_note,
            reviewed_at=review.reviewed_at,
        )


class SubFieldOut(BaseModel):
    path: str
    description: str | None
    enum: list[str]
    nullable: bool


class RelatedOut(BaseModel):
    field: str
    summary: str | None


class FieldDefinitionOut(BaseModel):
    """The field's meaning, read from the schema (app/review/definitions.py)."""

    field: str
    description: str | None
    object_description: str | None
    object_is_shared: bool
    summary: str | None
    enum: list[str]
    nullable: bool
    exclusions: list[str]
    related: list[RelatedOut]
    parts: list[SubFieldOut]
    prompt_rules: list[str]


class CandidateOut(BaseModel):
    """A passage chosen by field-specific keyword cues, independent of the
    extraction (app/review/candidates.py). In document order."""

    start: int
    end: int
    text: str
    cues: list[str]


class FieldReviewDetail(FieldReviewState):
    document_id: uuid.UUID
    document_title: str | None
    source_url: str
    schema_version: str
    run_id: str
    definition: FieldDefinitionOut
    candidates: list[CandidateOut]
    # Where the quotes the extraction cites sit in the source, for
    # programmatic consumers. The review page renders only the not-found
    # ones (see app/api/review_ui.py).
    snippets: list[SnippetOut]
    document_text: str


class DecisionRequest(BaseModel):
    action: Literal["accept", "correct", "skip"]
    reviewer: str | None = Field(default=None, description="Required for accept and correct.")
    # JSON null is a legitimate correction (the field should have been
    # null), so "not provided" is detected via model_fields_set, not None.
    corrected_value: Any = Field(default=None, description="Required for correct.")
    note: str | None = None


class QueueItemRef(BaseModel):
    id: uuid.UUID
    extraction_id: uuid.UUID
    field: str
    confidence: float


class RouteResponse(BaseModel):
    floor: float
    budget: int | None
    eligible: int
    outstanding_before: int
    routed: int
    fields: list[QueueItemRef]


class ReviewedFieldOut(BaseModel):
    id: uuid.UUID
    extraction_id: uuid.UUID
    document_id: uuid.UUID
    text_hash: str
    source_url: str
    document_title: str | None
    schema_version: str
    provider: str
    model: str
    thinking: str
    run_id: str
    field: str
    confidence: float
    model_value: Any
    decision: str
    corrected_value: Any
    reviewer: str
    reviewer_note: str | None
    reviewed_at: datetime
    routed_at: datetime | None


class CorrectionsResponse(BaseModel):
    decision: Literal["corrected", "accepted", "all"]
    count: int
    items: list[ReviewedFieldOut]


def _routing_config() -> RoutingConfig:
    return RoutingConfig(
        confidence_floor=settings.review_confidence_floor, budget=settings.review_budget
    )


@router.get("/queue", response_model=QueueResponse)
def get_queue(
    limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)
) -> QueueResponse:
    engine = get_engine()
    with engine.connect() as conn:
        size = queue_size(conn)
        items = list_queue(conn, limit=limit, offset=offset)
    return QueueResponse(
        size=size,
        routing=_routing_config(),
        items=[
            QueueItem(
                id=i.id,
                extraction_id=i.extraction_id,
                document_id=i.document_id,
                document_title=i.document_title,
                source_url=i.source_url,
                schema_version=i.schema_version,
                run_id=i.run_id,
                field=i.field,
                confidence=i.confidence,
                routed_at=i.routed_at,
                skip_count=i.skip_count,
            )
            for i in items
        ],
    )


@router.get("/fields/{field_review_id}", response_model=FieldReviewDetail)
def get_field(field_review_id: uuid.UUID) -> FieldReviewDetail:
    engine = get_engine()
    with engine.connect() as conn:
        try:
            item = load_for_review(conn, field_review_id)
        except FieldReviewNotFoundError:
            raise HTTPException(status_code=404, detail="field review not found") from None
    found = snippets(item.document_text, quotes_for(item.review.field, item.record))
    state = FieldReviewState.from_review(item.review)
    definition = field_definition(item.review.field)
    return FieldReviewDetail(
        **state.model_dump(),
        document_id=item.document_id,
        document_title=item.document_title,
        source_url=item.source_url,
        schema_version=item.schema_version,
        run_id=item.run_id,
        definition=FieldDefinitionOut(
            field=definition.field,
            description=definition.description,
            object_description=definition.object_description,
            object_is_shared=definition.object_is_shared,
            summary=definition.summary,
            enum=list(definition.enum),
            nullable=definition.nullable,
            exclusions=list(definition.exclusions),
            related=[RelatedOut(**r.__dict__) for r in definition.related],
            parts=[
                SubFieldOut(
                    path=p.path, description=p.description, enum=list(p.enum), nullable=p.nullable
                )
                for p in definition.parts
            ],
            prompt_rules=list(definition.prompt_rules),
        ),
        candidates=[
            CandidateOut(start=c.start, end=c.end, text=c.text, cues=list(c.cues))
            for c in candidates(item.review.field, item.document_text)
        ],
        snippets=[SnippetOut(**s.__dict__) for s in found],
        document_text=item.document_text,
    )


@router.post("/fields/{field_review_id}/decision", response_model=FieldReviewState)
def post_decision(field_review_id: uuid.UUID, body: DecisionRequest) -> FieldReviewState:
    if body.action == "correct" and "corrected_value" not in body.model_fields_set:
        raise HTTPException(status_code=422, detail="correct requires corrected_value")
    engine = get_engine()
    with engine.begin() as conn:
        try:
            review = record_decision(
                conn,
                field_review_id,
                action=body.action,
                reviewer=body.reviewer,
                corrected_value=body.corrected_value,
                note=body.note,
            )
        except FieldReviewNotFoundError:
            raise HTTPException(status_code=404, detail="field review not found") from None
        except (AlreadyReviewedError, NotRoutedError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except InvalidCorrectionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    return FieldReviewState.from_review(review)


@router.post("/route", response_model=RouteResponse)
def post_route() -> RouteResponse:
    engine = get_engine()
    with engine.begin() as conn:
        result = route_pending(
            conn, floor=settings.review_confidence_floor, budget=settings.review_budget
        )
    return RouteResponse(
        floor=result.floor,
        budget=result.budget,
        eligible=result.eligible,
        outstanding_before=result.outstanding_before,
        routed=len(result.routed),
        fields=[
            QueueItemRef(
                id=f.id, extraction_id=f.extraction_id, field=f.field, confidence=f.confidence
            )
            for f in result.routed
        ],
    )


@router.get("/corrections", response_model=CorrectionsResponse)
def get_corrections(
    decision: Literal["corrected", "accepted", "all"] = "corrected",
    field: str | None = None,
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
) -> CorrectionsResponse:
    engine = get_engine()
    with engine.connect() as conn:
        items = list_reviewed(
            conn,
            decision=None if decision == "all" else decision,
            field=field,
            limit=limit,
            offset=offset,
        )
    return CorrectionsResponse(
        decision=decision,
        count=len(items),
        items=[ReviewedFieldOut(**i.__dict__) for i in items],
    )
