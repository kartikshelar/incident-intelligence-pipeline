"""Process-wide configuration, read from environment variables.

Everything the app owns is prefixed APP_ and may also come from a `.env`
file in the working directory (never committed — see .gitignore and
.env.example). Nothing model-related is a constant in code: the LLM
provider, the model string, and the API key all come from here.

  APP_LLM_PROVIDER        which app.extract.llm implementation to use
  APP_EXTRACTION_MODEL    the model string passed to that provider verbatim
  ANTHROPIC_API_KEY       the provider's key (its own name, not APP_-prefixed,
                          so the same variable the SDK reads still works)

Provider and model are optional at load time so that processes which never
call a model (API, migrations) start without them. The worker's LLM factory
(app.extract.llm.build_llm_client) is what insists on them, and it fails per
job with a recorded reason rather than at import time.
"""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/incident_intel"

    # Worker polling and job-claim behavior (mirrors ADR-003 §4).
    worker_poll_interval_seconds: float = 1.0
    job_visibility_timeout_seconds: int = 600  # 10 minutes, per ADR-003
    job_max_attempts: int = 5

    # Extraction v0 (M3). Provider + model identify what produced a row —
    # both are stored on every `extractions` row.
    llm_provider: str | None = None
    extraction_model: str | None = None
    extraction_max_tokens: int = 16000
    # Schema-validation retries *within* one job (validation error fed back
    # to the model). Distinct from job_max_attempts, which is the queue's
    # retry budget for transient failures.
    extraction_max_attempts: int = 3

    # Read under the SDK's own variable name so a shell `export
    # ANTHROPIC_API_KEY=...` and a `.env` line both work. Passed explicitly
    # to the SDK; if unset the SDK falls back to its own resolution (auth
    # token, `ant auth login` profile).
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    @field_validator("llm_provider", "extraction_model", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `APP_EXTRACTION_MODEL=` (empty) means "not configured", not "".
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _strip_key(cls, value: object) -> object:
        # A stray space around `=` in a .env line or shell export would
        # otherwise be sent verbatim in the header and fail as a bad key.
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


settings = Settings()
