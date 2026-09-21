"""app.settings.Settings.database_url: Heroku Postgres's bare DATABASE_URL
fallback and postgres:// -> postgresql+psycopg:// scheme normalization
(M6 Heroku deployment)."""

import pytest

from app.settings import Settings


def _settings(**overrides: object) -> Settings:
    # _env_file=None: the test must not pick up a developer's real .env.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_default_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # tests/__init__.py sets APP_DATABASE_URL for the whole test session
    # (routing tests at the _test database); clear it here to observe the
    # field's actual default rather than that session-wide override.
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert (
        _settings().database_url
        == "postgresql+psycopg://postgres:postgres@localhost:5432/incident_intel"
    )


def test_app_database_url_env_var_is_used_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg://u:p@render-host:5432/db")
    assert _settings().database_url == "postgresql+psycopg://u:p@render-host:5432/db"


def test_bare_database_url_is_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heroku Postgres sets DATABASE_URL, never APP_DATABASE_URL."""
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:secretpass@heroku-host:5432/db")
    assert _settings().database_url == "postgresql+psycopg://u:secretpass@heroku-host:5432/db"


def test_app_database_url_takes_priority_over_bare_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg://u:p@render-host:5432/render-db")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@heroku-host:5432/heroku-db")
    assert _settings().database_url == "postgresql+psycopg://u:p@render-host:5432/render-db"


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_heroku_style_schemes_are_normalized(scheme: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"{scheme}://u:p@host:5432/db")
    assert _settings().database_url == "postgresql+psycopg://u:p@host:5432/db"


def test_already_correct_scheme_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host:5432/db")
    assert _settings().database_url == "postgresql+psycopg://u:p@host:5432/db"


def test_password_is_preserved_verbatim_not_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_as_string(hide_password=False) is required here — plain
    str(url) masks the password as '***', which would silently break every
    real connection made with the normalized value."""
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:s3cr3t-p%40ss@host:5432/db")
    assert "s3cr3t-p%40ss" in _settings().database_url
    assert "***" not in _settings().database_url


def test_query_params_survive_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db?sslmode=require")
    assert _settings().database_url == (
        "postgresql+psycopg://u:p@host:5432/db?sslmode=require"
    )


def test_direct_kwarg_construction_is_also_normalized() -> None:
    """tests/conftest.py and every other caller that constructs Settings
    directly (not just from env) must see the same normalization."""
    cfg = _settings(database_url="postgres://u:p@host:5432/db")
    assert cfg.database_url == "postgresql+psycopg://u:p@host:5432/db"
