"""HTTP Basic Auth gate for the review surface (M6 part 2).

The review UI and its JSON API write gold-set data (field_reviews.decision
/ corrected_value, ADR-010 §6's "closes the loop from extraction ->
confidence -> routing -> human correction -> evaluation data"). That is
the one write path this system exposes to a human over HTTP, so it is the
one endpoint group gated. This is a lock on the door, not an identity
system — no accounts, no sessions, one shared credential pair — consistent
with PROJECT_BRIEF §3's "no auth/SSO theater" and distinct from DERIVE-04
(multi-tenancy), which this does not answer and is not trying to.

Configuration (app.settings):
  unset (both APP_REVIEW_BASIC_AUTH_USER and _PASS absent)
      the gate is not engaged — every request passes. This is what lets
      `docker compose up` and the test suite work with no credentials.
      Not appropriate for a public deployment; render.yaml sets both.
  only one of the two set
      refuses every request (500, not "half-open"): a deployment that set
      one but not the other made a mistake, and the safe failure is closed.
  both set
      requests without valid HTTP Basic credentials get 401 with a
      `WWW-Authenticate: Basic` challenge, per RFC 7617 — a browser opens
      its native login prompt, `curl -u user:pass` works, no JS/cookies.

Comparison uses `secrets.compare_digest` (constant-time) so a shared
low-value credential is not trivially timeable; it is not trying to defend
against anything stronger than casual scraping of a public URL.
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.settings import settings

_security = HTTPBasic(auto_error=False)


def require_review_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),  # noqa: B008 - FastAPI idiom
) -> None:
    configured_user = settings.review_basic_auth_user
    configured_pass = settings.review_basic_auth_pass

    if configured_user is None and configured_pass is None:
        return  # gate not engaged (see module docstring)

    if configured_user is None or configured_pass is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "review auth is misconfigured: both APP_REVIEW_BASIC_AUTH_USER and "
                "APP_REVIEW_BASIC_AUTH_PASS must be set together"
            ),
        )

    challenge = HTTPException(
        status_code=401,
        detail="review endpoints require authentication",
        headers={"WWW-Authenticate": "Basic"},
    )
    if credentials is None:
        raise challenge

    user_ok = secrets.compare_digest(credentials.username, configured_user)
    pass_ok = secrets.compare_digest(
        credentials.password, configured_pass.get_secret_value()
    )
    if not (user_ok and pass_ok):
        raise challenge
