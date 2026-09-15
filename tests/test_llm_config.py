"""Provider/model/key come from configuration, and the factory is the only
place a provider is chosen. No network: constructing an SDK client does
not connect."""

import pytest
from pydantic import SecretStr

from app.extract.errors import PermanentExtractionError
from app.extract.llm import SUPPORTED_PROVIDERS, AnthropicClient, build_llm_client
from app.settings import Settings


def _settings(**overrides: object) -> Settings:
    # _env_file=None: the test must not pick up a developer's real .env.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_factory_builds_the_configured_provider_and_model() -> None:
    cfg = _settings(
        llm_provider="anthropic",
        extraction_model="configured-model",
        extraction_max_tokens=777,
        anthropic_api_key=SecretStr("k"),
    )
    client = build_llm_client(cfg)
    assert isinstance(client, AnthropicClient)
    assert client.provider == "anthropic"
    assert client.model == "configured-model"
    assert client.max_tokens == 777


def test_provider_name_is_case_insensitive() -> None:
    cfg = _settings(
        llm_provider="Anthropic", extraction_model="m", anthropic_api_key=SecretStr("k")
    )
    assert build_llm_client(cfg).provider == "anthropic"


def test_unset_provider_is_a_recorded_configuration_error() -> None:
    with pytest.raises(PermanentExtractionError, match="APP_LLM_PROVIDER"):
        build_llm_client(_settings(extraction_model="m"))


def test_unknown_provider_is_a_recorded_configuration_error() -> None:
    with pytest.raises(PermanentExtractionError, match="unknown LLM provider 'nope'"):
        build_llm_client(_settings(llm_provider="nope", extraction_model="m"))


def test_unset_model_is_a_recorded_configuration_error() -> None:
    with pytest.raises(PermanentExtractionError, match="APP_EXTRACTION_MODEL"):
        build_llm_client(_settings(llm_provider="anthropic"))


def test_no_model_default_exists_in_code() -> None:
    assert _settings().extraction_model is None
    assert _settings().llm_provider is None
    assert SUPPORTED_PROVIDERS == ("anthropic",)


def test_settings_read_provider_model_and_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("APP_EXTRACTION_MODEL", "  model-from-env ")
    monkeypatch.setenv("ANTHROPIC_API_KEY", " sk-with-stray-space ")  # as a .env line might
    cfg = _settings()
    assert cfg.llm_provider == "anthropic"
    assert cfg.extraction_model == "model-from-env"
    assert cfg.anthropic_api_key is not None
    assert cfg.anthropic_api_key.get_secret_value() == "sk-with-stray-space"


def test_blank_env_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_EXTRACTION_MODEL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    cfg = _settings()
    assert cfg.extraction_model is None
    assert cfg.anthropic_api_key is None


def test_settings_read_from_dotenv_file(tmp_path: pytest.TempPathFactory) -> None:
    env_file = tmp_path / ".env"  # type: ignore[operator]
    env_file.write_text(
        "APP_LLM_PROVIDER=anthropic\nAPP_EXTRACTION_MODEL= dotenv-model\nANTHROPIC_API_KEY= sk-x\n"
    )
    cfg = Settings(_env_file=str(env_file))  # type: ignore[call-arg]
    assert (cfg.llm_provider, cfg.extraction_model) == ("anthropic", "dotenv-model")
    assert cfg.anthropic_api_key is not None
    assert cfg.anthropic_api_key.get_secret_value() == "sk-x"
