"""Process-wide configuration, read from environment variables.

Everything here is prefixed APP_. The Anthropic API key is NOT here: the SDK
reads ANTHROPIC_API_KEY (or an `ant auth login` profile) itself, and
duplicating it under another name would be one more thing to get wrong.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/incident_intel"

    # Worker polling and job-claim behavior (mirrors ADR-003 §4).
    worker_poll_interval_seconds: float = 1.0
    job_visibility_timeout_seconds: int = 600  # 10 minutes, per ADR-003
    job_max_attempts: int = 5

    # Extraction v0 (M3).
    extraction_model: str = "claude-opus-5"
    extraction_max_tokens: int = 16000
    # Schema-validation retries *within* one job (validation error fed back
    # to the model). Distinct from job_max_attempts, which is the queue's
    # retry budget for transient failures.
    extraction_max_attempts: int = 3


settings = Settings()
