"""M6 part 2: HTTP Basic Auth gating /review/* and /ui/review/* (app/api/auth.py).

Every case configures settings directly (monkeypatch.setattr, same pattern
tests/test_worker_end_to_end.py already uses) rather than through env vars,
since app.settings.settings is a module-level singleton imported once."""

import base64

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.main import app
from app.settings import settings

client = TestClient(app)


def _basic_header(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def _reset_auth_settings() -> None:
    # Every other test in the suite relies on the gate being off by
    # default; this fixture is the only place that turns it on, and it
    # always turns itself back off.
    yield
    settings.review_basic_auth_user = None
    settings.review_basic_auth_pass = None


def test_review_queue_is_open_when_auth_is_not_configured() -> None:
    assert settings.review_basic_auth_user is None
    assert settings.review_basic_auth_pass is None
    response = client.get("/review/queue")
    assert response.status_code == 200


def test_review_ui_is_open_when_auth_is_not_configured() -> None:
    response = client.get("/ui/review")
    assert response.status_code == 200


def test_non_review_endpoints_are_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "review_basic_auth_user", "reviewer")
    monkeypatch.setattr(settings, "review_basic_auth_pass", SecretStr("secret"))
    # /health, /metrics, /cost, /sources, /jobs/{id} carry no auth dependency.
    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/cost").status_code == 200


def test_review_queue_requires_credentials_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "review_basic_auth_user", "reviewer")
    monkeypatch.setattr(settings, "review_basic_auth_pass", SecretStr("secret"))

    no_creds = client.get("/review/queue")
    assert no_creds.status_code == 401
    assert no_creds.headers["www-authenticate"] == "Basic"


def test_review_queue_rejects_wrong_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "review_basic_auth_user", "reviewer")
    monkeypatch.setattr(settings, "review_basic_auth_pass", SecretStr("secret"))

    wrong_user = client.get("/review/queue", headers=_basic_header("nope", "secret"))
    assert wrong_user.status_code == 401
    wrong_pass = client.get("/review/queue", headers=_basic_header("reviewer", "nope"))
    assert wrong_pass.status_code == 401


def test_review_queue_accepts_correct_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "review_basic_auth_user", "reviewer")
    monkeypatch.setattr(settings, "review_basic_auth_pass", SecretStr("secret"))

    response = client.get("/review/queue", headers=_basic_header("reviewer", "secret"))
    assert response.status_code == 200


def test_review_ui_requires_and_accepts_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "review_basic_auth_user", "reviewer")
    monkeypatch.setattr(settings, "review_basic_auth_pass", SecretStr("secret"))

    assert client.get("/ui/review").status_code == 401
    assert client.get("/ui/review", headers=_basic_header("reviewer", "secret")).status_code == 200


def test_write_endpoints_are_gated_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not just the listing pages — the endpoints that actually write
    gold-set data (ADR-010 §6) are gated, which is the whole point."""
    monkeypatch.setattr(settings, "review_basic_auth_user", "reviewer")
    monkeypatch.setattr(settings, "review_basic_auth_pass", SecretStr("secret"))

    assert client.post("/review/route").status_code == 401
    assert (
        client.post(
            "/review/fields/00000000-0000-0000-0000-000000000000/decision",
            json={"action": "skip"},
        ).status_code
        == 401
    )


def test_partially_configured_auth_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only one of the two set is a deployment mistake; the safe failure is
    refusing every request, not treating it as unconfigured (open)."""
    monkeypatch.setattr(settings, "review_basic_auth_user", "reviewer")
    monkeypatch.setattr(settings, "review_basic_auth_pass", None)

    response = client.get("/review/queue")
    assert response.status_code == 500

    response_with_creds = client.get("/review/queue", headers=_basic_header("reviewer", "anything"))
    assert response_with_creds.status_code == 500


def test_partially_configured_auth_fails_closed_other_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "review_basic_auth_user", None)
    monkeypatch.setattr(settings, "review_basic_auth_pass", SecretStr("secret"))

    assert client.get("/review/queue").status_code == 500
