"""Process-wide configuration, read from environment variables.

M1 scope: just enough to connect to Postgres and run the worker loop.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/incident_intel"

    # Worker polling and job-claim behavior (mirrors ADR-003 §4).
    worker_poll_interval_seconds: float = 1.0
    job_visibility_timeout_seconds: int = 600  # 10 minutes, per ADR-003
    job_max_attempts: int = 5


settings = Settings()
