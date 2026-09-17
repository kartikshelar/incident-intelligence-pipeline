"""Process-wide configuration, read from environment variables.

Everything the app owns is prefixed APP_ and may also come from a `.env`
file in the working directory (never committed — see .gitignore and
.env.example). Nothing model-related is a constant in code: the LLM
provider, the model string, and the API key all come from here.

  APP_LLM_PROVIDER        which app.extract.llm implementation to use
  APP_EXTRACTION_MODEL    the model string passed to that provider verbatim
  APP_EXTRACTION_THINKING the thinking/effort setting, interpreted by the
                          provider (for anthropic: `default`, `adaptive`,
                          `disabled`, optionally `:<effort>` — see
                          app.extract.llm) and stored verbatim on every
                          extractions row next to provider and model
  APP_EXTRACTION_RUN_ID   labels which run produced a row (ADR-006 §8 needs
                          two independent runs at identical settings to be
                          distinguishable). Optional: unset means "generate
                          one UUID per process" (app.extract.llm), which is
                          enough to make a fresh `python -m
                          scripts.extraction_run` invocation its own run.
                          Set it explicitly to label a run by hand, or when
                          more than one worker process must share one run id.
  ANTHROPIC_API_KEY       the provider's key (its own name, not APP_-prefixed,
                          so the same variable the SDK reads still works)
  APP_REVIEW_CONFIDENCE_FLOOR
                          M4 routing (ADR-010 §3): a field whose self-
                          reported confidence is below this is eligible for
                          human review. 0.70 is the provisional operating
                          point the ADR names; M5 sweeps it against the gold
                          set, which is why it is configuration and not a
                          constant in app/review/routing.py.
  APP_REVIEW_BUDGET       Optional. Caps how many fields may be awaiting a
                          reviewer at once (ADR-010 §1: "at a fixed human
                          review budget"). Applied AFTER ranking, so it
                          changes how many of the ranked fields get routed,
                          never which ones rank first. Unset means uncapped,
                          which is also how M5 evaluates the routing policy
                          (ADR-010 §5).

Provider, model and thinking are optional at load time so that processes
which never call a model (API, migrations) start without them. The worker's
LLM factory (app.extract.llm.build_llm_client) is what insists on them, and
it fails per job with a recorded reason rather than at import time.
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

    # Extraction v0 (M3). Provider + model + thinking identify what produced
    # a row — all three are stored on every `extractions` row and are part
    # of its idempotency key. No default for any of them exists in code.
    llm_provider: str | None = None
    extraction_model: str | None = None
    extraction_thinking: str | None = None
    # Unset means "generate one UUID per client" (app.extract.llm), not "no
    # run id" — unlike provider/model/thinking, a missing run id is not a
    # configuration error, since it does not change what gets extracted.
    extraction_run_id: str | None = None
    extraction_max_tokens: int = 16000
    # Schema-validation retries *within* one job (validation error fed back
    # to the model). Distinct from job_max_attempts, which is the queue's
    # retry budget for transient failures.
    extraction_max_attempts: int = 3

    # Review routing (M4, ADR-010). The floor is a *provisional* operating
    # point (ADR-010 §3), not a tuned threshold: nothing in this repository
    # may adjust it against data before M5's sweep. The budget is an
    # operational cap on outstanding review work, separate from the policy.
    review_confidence_floor: float = Field(default=0.70, ge=0.0, le=1.0)
    review_budget: int | None = Field(default=None, ge=0)

    # Read under the SDK's own variable name so a shell `export
    # ANTHROPIC_API_KEY=...` and a `.env` line both work. Passed explicitly
    # to the SDK; if unset the SDK falls back to its own resolution (auth
    # token, `ant auth login` profile).
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    @field_validator(
        "llm_provider",
        "extraction_model",
        "extraction_thinking",
        "extraction_run_id",
        "review_budget",
        mode="before",
    )
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
