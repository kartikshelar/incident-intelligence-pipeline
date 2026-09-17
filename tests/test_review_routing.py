"""Routing policy (ADR-010 §1, §3): rank by ascending self-reported
confidence under the configured floor; budget applied after ranking."""

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text

from app.review.fields import record_decision
from app.review.routing import outstanding, rank_eligible, route_pending
from app.settings import Settings, settings
from tests.review_support import complete_extraction, field_id, field_rows


def _states(engine: Engine, extraction_id):  # type: ignore[no-untyped-def]
    return {f: r["review_state"] for f, r in field_rows(engine, extraction_id).items()}


def test_fields_below_the_floor_are_routed_lowest_first(engine: Engine) -> None:
    extraction_id = complete_extraction(
        engine, confidence={"trigger": 0.55, "mechanism": 0.62, "summary": 0.69, "title": 0.70}
    )
    with engine.begin() as conn:
        result = route_pending(conn, floor=0.70, budget=None)

    assert [f.field for f in result.routed] == ["trigger", "mechanism", "summary"]
    assert [f.confidence for f in result.routed] == [0.55, 0.62, 0.69]
    assert result.eligible == 3
    states = _states(engine, extraction_id)
    assert states["trigger"] == states["mechanism"] == states["summary"] == "routed"
    assert states["title"] == "unreviewed"  # at the floor is not below it
    assert states["detection_method"] == "unreviewed"  # 0.9
    routed_at = {
        r["routed_at"]
        for f, r in field_rows(engine, extraction_id).items()
        if f in {"trigger", "mechanism", "summary"}
    }
    assert None not in routed_at


def test_ranking_is_across_extractions_not_per_document(engine: Engine) -> None:
    a = complete_extraction(engine, confidence={"trigger": 0.60})
    b = complete_extraction(engine, confidence={"mechanism": 0.20, "trigger": 0.65})
    with engine.begin() as conn:
        ranked = rank_eligible(conn, floor=0.70)
    assert [(f.extraction_id, f.field) for f in ranked] == [
        (b, "mechanism"),
        (a, "trigger"),
        (b, "trigger"),
    ]


def test_budget_is_applied_after_ranking(engine: Engine) -> None:
    """The cap decides how many, never which: the lowest N are routed."""
    complete_extraction(
        engine, confidence={"trigger": 0.10, "mechanism": 0.50, "summary": 0.30, "title": 0.65}
    )
    with engine.begin() as conn:
        result = route_pending(conn, floor=0.70, budget=2)
    assert [f.field for f in result.routed] == ["trigger", "summary"]
    assert result.eligible == 4
    assert result.outstanding_before == 0


def test_budget_caps_outstanding_review_work_and_tops_up_as_reviews_land(
    engine: Engine,
) -> None:
    extraction_id = complete_extraction(
        engine, confidence={"trigger": 0.10, "mechanism": 0.50, "summary": 0.30, "title": 0.65}
    )
    with engine.begin() as conn:
        route_pending(conn, floor=0.70, budget=2)
    # Full: a second pass routes nothing more.
    with engine.begin() as conn:
        again = route_pending(conn, floor=0.70, budget=2)
        assert again.routed == []
        assert again.outstanding_before == 2
        assert outstanding(conn) == 2
    # The reviewer clears one; the next pass routes the next-lowest.
    with engine.begin() as conn:
        record_decision(
            conn, field_id(engine, extraction_id, "trigger"), action="accept", reviewer="k"
        )
    with engine.begin() as conn:
        topped = route_pending(conn, floor=0.70, budget=2)
    assert [f.field for f in topped.routed] == ["mechanism"]
    assert _states(engine, extraction_id)["title"] == "unreviewed"


def test_uncapped_routes_everything_eligible(engine: Engine) -> None:
    complete_extraction(engine, confidence={"trigger": 0.10, "mechanism": 0.50, "summary": 0.30})
    complete_extraction(engine, confidence={"title": 0.05})
    with engine.begin() as conn:
        result = route_pending(conn, floor=0.70, budget=None)
    assert len(result.routed) == 4
    assert result.eligible == 4


def test_zero_budget_routes_nothing(engine: Engine) -> None:
    complete_extraction(engine, confidence={"trigger": 0.10})
    with engine.begin() as conn:
        result = route_pending(conn, floor=0.70, budget=0)
    assert result.routed == [] and result.eligible == 1
    with pytest.raises(ValueError):
        with engine.begin() as conn:
            route_pending(conn, floor=0.70, budget=-1)


def test_a_pass_is_idempotent(engine: Engine) -> None:
    complete_extraction(engine, confidence={"trigger": 0.10, "mechanism": 0.50})
    with engine.begin() as conn:
        first = route_pending(conn, floor=0.70, budget=None)
    with engine.begin() as conn:
        second = route_pending(conn, floor=0.70, budget=None)
    assert len(first.routed) == 2
    assert second.routed == [] and second.eligible == 0


def test_the_floor_is_a_parameter_a_sweep_can_change_without_code(engine: Engine) -> None:
    """M5 sweeps the floor: the same data, different floors, different
    eligible sets — through configuration, not a code change."""
    complete_extraction(
        engine, confidence={"trigger": 0.30, "mechanism": 0.50, "summary": 0.75, "title": 0.85}
    )
    with engine.connect() as conn:
        by_floor = {
            floor: [f.field for f in rank_eligible(conn, floor=floor)]
            for floor in (0.2, 0.4, 0.7, 0.8, 0.9)
        }
    assert by_floor == {
        0.2: [],
        0.4: ["trigger"],
        0.7: ["trigger", "mechanism"],
        0.8: ["trigger", "mechanism", "summary"],
        0.9: ["trigger", "mechanism", "summary", "title"],
    }


def test_floor_and_budget_come_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    assert settings.review_confidence_floor == 0.70  # ADR-010 §3's provisional value
    assert settings.review_budget is None  # uncapped unless set

    monkeypatch.setenv("APP_REVIEW_CONFIDENCE_FLOOR", "0.55")
    monkeypatch.setenv("APP_REVIEW_BUDGET", "25")
    cfg = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (cfg.review_confidence_floor, cfg.review_budget) == (0.55, 25)

    monkeypatch.setenv("APP_REVIEW_BUDGET", "")
    assert Settings(_env_file=None).review_budget is None  # type: ignore[call-arg]

    monkeypatch.setenv("APP_REVIEW_CONFIDENCE_FLOOR", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_no_threshold_constant_in_the_routing_module() -> None:
    """The floor must not exist as a literal in the policy code: no float
    constant at all in app/review/routing.py (docstrings excluded)."""
    import ast
    import inspect

    from app.review import routing

    tree = ast.parse(inspect.getsource(routing))
    floats = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == []


def test_routed_fields_are_visible_to_sql(engine: Engine) -> None:
    complete_extraction(engine, confidence={"trigger": 0.10})
    with engine.begin() as conn:
        route_pending(conn, floor=0.70, budget=None)
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM field_reviews WHERE review_state = 'routed'")
        ).scalar_one()
    assert n == 1
