"""M4 review API and UI: the queue, one field, decisions, corrections."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.main import app
from app.review.routing import route_pending
from app.settings import settings
from tests.review_support import complete_extraction, field_id, field_rows

client = TestClient(app)


def _route(engine: Engine, floor: float = 0.70, budget: int | None = None) -> None:
    with engine.begin() as conn:
        route_pending(conn, floor=floor, budget=budget)


# --- queue -----------------------------------------------------------------


def test_queue_lists_routed_fields_lowest_confidence_first(engine: Engine) -> None:
    a = complete_extraction(engine, confidence={"trigger": 0.60})
    b = complete_extraction(engine, confidence={"mechanism": 0.20, "trigger": 0.65})
    _route(engine)

    response = client.get("/review/queue")
    assert response.status_code == 200
    body = response.json()
    assert body["size"] == 3
    assert body["routing"] == {"confidence_floor": 0.70, "budget": None}
    assert [(i["extraction_id"], i["field"], i["confidence"]) for i in body["items"]] == [
        (str(b), "mechanism", 0.20),
        (str(a), "trigger", 0.60),
        (str(b), "trigger", 0.65),
    ]
    item = body["items"][0]
    assert item["document_title"] == "Cloudflare outage"
    assert item["source_url"] == "https://example.com/pm"
    assert item["routed_at"] is not None
    assert item["skip_count"] == 0


def test_queue_excludes_unrouted_and_reviewed_fields(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5, "mechanism": 0.6})
    _route(engine)
    fid = field_id(engine, extraction_id, "trigger")
    client.post(f"/review/fields/{fid}/decision", json={"action": "accept", "reviewer": "k"})

    body = client.get("/review/queue").json()
    assert body["size"] == 1
    assert [i["field"] for i in body["items"]] == ["mechanism"]


def test_queue_paginates(engine: Engine) -> None:
    complete_extraction(engine, confidence={"trigger": 0.1, "mechanism": 0.2, "summary": 0.3})
    _route(engine)
    body = client.get("/review/queue", params={"limit": 2, "offset": 1}).json()
    assert body["size"] == 3
    assert [i["field"] for i in body["items"]] == ["mechanism", "summary"]


# --- one field -------------------------------------------------------------


def test_field_detail_carries_value_confidence_evidence_and_source(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"mechanism": 0.45})
    _route(engine)
    fid = field_id(engine, extraction_id, "mechanism")

    response = client.get(f"/review/fields/{fid}")
    assert response.status_code == 200
    body = response.json()
    assert body["field"] == "mechanism"
    assert body["confidence"] == 0.45
    assert body["model_value"]["label"] == "limit_violation"
    assert body["review_state"] == "routed"
    assert body["extraction_id"] == str(extraction_id)
    assert body["document_title"] == "Cloudflare outage"
    assert body["schema_version"] and body["run_id"]
    (snippet,) = body["snippets"]
    assert snippet["found"] is True
    assert snippet["match"] == "the software panicked"
    assert "exceeded its limit" in snippet["before"]
    assert "core proxy returned HTTP 5xx" in snippet["after"]
    assert "Impact starts 11:28" in body["document_text"]


def test_field_detail_404(engine: Engine) -> None:
    assert client.get(f"/review/fields/{uuid.uuid4()}").status_code == 404


# --- decisions -------------------------------------------------------------


def test_accept_via_api_removes_the_field_from_the_queue(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5})
    _route(engine)
    fid = field_id(engine, extraction_id, "trigger")

    response = client.post(
        f"/review/fields/{fid}/decision", json={"action": "accept", "reviewer": "kartik"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["review_state"] == "reviewed"
    assert body["decision"] == "accepted"
    assert body["reviewer"] == "kartik"
    assert body["reviewed_at"] is not None
    assert client.get("/review/queue").json()["size"] == 0


def test_correct_via_api_keeps_both_values(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"detection_method": 0.5})
    _route(engine)
    fid = field_id(engine, extraction_id, "detection_method")

    response = client.post(
        f"/review/fields/{fid}/decision",
        json={
            "action": "correct",
            "reviewer": "kartik",
            "corrected_value": "internal_manual",
            "note": "an engineer noticed first",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "corrected"
    assert body["model_value"] == "monitoring"
    assert body["corrected_value"] == "internal_manual"
    assert body["reviewer_note"] == "an engineer noticed first"


def test_correct_to_json_null_is_accepted(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5})
    _route(engine)
    fid = field_id(engine, extraction_id, "trigger")
    response = client.post(
        f"/review/fields/{fid}/decision",
        json={"action": "correct", "reviewer": "k", "corrected_value": None},
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "corrected"
    assert response.json()["corrected_value"] is None


def test_correct_without_a_value_is_422(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5})
    _route(engine)
    fid = field_id(engine, extraction_id, "trigger")
    response = client.post(
        f"/review/fields/{fid}/decision", json={"action": "correct", "reviewer": "k"}
    )
    assert response.status_code == 422
    assert "corrected_value" in response.json()["detail"]


def test_invalid_correction_is_422_and_changes_nothing(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"detection_method": 0.5})
    _route(engine)
    fid = field_id(engine, extraction_id, "detection_method")
    response = client.post(
        f"/review/fields/{fid}/decision",
        json={"action": "correct", "reviewer": "k", "corrected_value": "automated"},
    )
    assert response.status_code == 422
    assert "detection_method" in response.json()["detail"]
    assert field_rows(engine, extraction_id)["detection_method"]["review_state"] == "routed"


def test_second_decision_is_409(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.5})
    _route(engine)
    fid = field_id(engine, extraction_id, "trigger")
    client.post(f"/review/fields/{fid}/decision", json={"action": "accept", "reviewer": "a"})
    response = client.post(
        f"/review/fields/{fid}/decision", json={"action": "accept", "reviewer": "b"}
    )
    assert response.status_code == 409
    assert field_rows(engine, extraction_id)["trigger"]["reviewer"] == "a"


def test_skip_returns_the_field_to_the_back_of_the_queue(engine: Engine) -> None:
    extraction_id = complete_extraction(engine, confidence={"trigger": 0.1, "mechanism": 0.2})
    _route(engine)
    fid = field_id(engine, extraction_id, "trigger")

    response = client.post(f"/review/fields/{fid}/decision", json={"action": "skip"})
    assert response.status_code == 200
    assert response.json()["review_state"] == "routed"
    assert response.json()["skip_count"] == 1

    body = client.get("/review/queue").json()
    assert body["size"] == 2  # still in the queue
    assert [i["field"] for i in body["items"]] == ["mechanism", "trigger"]


def test_skip_on_an_unrouted_field_is_409(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    fid = field_id(engine, extraction_id, "trigger")
    assert client.post(f"/review/fields/{fid}/decision", json={"action": "skip"}).status_code == 409


def test_decision_on_unknown_field_is_404(engine: Engine) -> None:
    response = client.post(
        f"/review/fields/{uuid.uuid4()}/decision", json={"action": "accept", "reviewer": "k"}
    )
    assert response.status_code == 404


def test_unknown_action_is_422(engine: Engine) -> None:
    extraction_id = complete_extraction(engine)
    fid = field_id(engine, extraction_id, "trigger")
    assert (
        client.post(f"/review/fields/{fid}/decision", json={"action": "approve"}).status_code == 422
    )


# --- routing pass ----------------------------------------------------------


def test_route_endpoint_uses_configured_floor_and_budget(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete_extraction(engine, confidence={"trigger": 0.1, "mechanism": 0.5, "summary": 0.3})
    monkeypatch.setattr(settings, "review_confidence_floor", 0.4)
    monkeypatch.setattr(settings, "review_budget", 1)

    response = client.post("/review/route")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "floor": 0.4,
        "budget": 1,
        "eligible": 2,
        "outstanding_before": 0,
        "routed": 1,
        "fields": body["fields"],
    }
    assert [f["field"] for f in body["fields"]] == ["trigger"]
    assert client.get("/review/queue").json()["routing"] == {
        "confidence_floor": 0.4,
        "budget": 1,
    }


# --- corrections: the closed loop -------------------------------------------


def test_corrections_are_queryable_as_gold_set_input(engine: Engine) -> None:
    extraction_id = complete_extraction(
        engine, confidence={"detection_method": 0.5, "trigger": 0.6, "mechanism": 0.4}
    )
    _route(engine)
    rows = field_rows(engine, extraction_id)
    client.post(
        f"/review/fields/{rows['detection_method']['id']}/decision",
        json={"action": "correct", "reviewer": "kartik", "corrected_value": "customer_report"},
    )
    client.post(
        f"/review/fields/{rows['trigger']['id']}/decision",
        json={"action": "accept", "reviewer": "kartik"},
    )
    client.post(f"/review/fields/{rows['mechanism']['id']}/decision", json={"action": "skip"})

    corrections = client.get("/review/corrections").json()
    assert corrections["decision"] == "corrected"
    assert corrections["count"] == 1
    (item,) = corrections["items"]
    assert item["field"] == "detection_method"
    assert item["model_value"] == "monitoring"
    assert item["corrected_value"] == "customer_report"
    assert item["reviewer"] == "kartik"
    assert item["extraction_id"] == str(extraction_id)
    # Provenance a gold-set builder needs: the document identity (ADR-007)
    # and what produced the value being corrected.
    assert item["text_hash"] and item["source_url"] == "https://example.com/pm"
    assert item["schema_version"] and item["provider"] == "fake" and item["model"] == "fake-model"
    assert item["run_id"]
    assert item["confidence"] == 0.5
    assert item["routed_at"] is not None

    accepted = client.get("/review/corrections", params={"decision": "accepted"}).json()
    assert [i["field"] for i in accepted["items"]] == ["trigger"]
    everything = client.get("/review/corrections", params={"decision": "all"}).json()
    assert {i["field"] for i in everything["items"]} == {"detection_method", "trigger"}
    by_field = client.get(
        "/review/corrections", params={"decision": "all", "field": "trigger"}
    ).json()
    assert [i["decision"] for i in by_field["items"]] == ["accepted"]


