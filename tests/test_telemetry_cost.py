"""app.telemetry.cost: same PRICE_*_USD_PER_MTOK convention as
scripts/extraction_run.py, now read through app.settings."""

from app.settings import Settings
from app.telemetry.cost import Pricing, cost_usd, pricing_from_settings

USAGE = {
    "input_tokens": 1_000_000,
    "output_tokens": 500_000,
    "cache_read_input_tokens": 2_000_000,
    "cache_creation_input_tokens": 100_000,
}


def _settings(**price: float) -> Settings:
    return Settings(  # type: ignore[call-arg]
        llm_provider=None,
        extraction_model=None,
        extraction_thinking=None,
        **{f"price_{k}_usd_per_mtok": v for k, v in price.items()},
    )


def test_pricing_is_none_unless_all_four_prices_are_set() -> None:
    assert pricing_from_settings(_settings()) is None
    assert (
        pricing_from_settings(_settings(input=1.0, output=2.0, cache_read=0.1)) is None
    )  # cache_write missing


def test_pricing_present_when_all_four_are_set() -> None:
    pricing = pricing_from_settings(
        _settings(input=3.0, output=15.0, cache_read=0.3, cache_write=3.75)
    )
    assert pricing == Pricing(
        input_usd_per_mtok=3.0,
        output_usd_per_mtok=15.0,
        cache_read_usd_per_mtok=0.3,
        cache_write_usd_per_mtok=3.75,
    )


def test_cost_usd_is_none_without_pricing() -> None:
    assert cost_usd(USAGE, None) is None


def test_cost_usd_matches_hand_computed_total() -> None:
    pricing = Pricing(
        input_usd_per_mtok=3.0,
        output_usd_per_mtok=15.0,
        cache_read_usd_per_mtok=0.3,
        cache_write_usd_per_mtok=3.75,
    )
    expected = (
        1_000_000 * 3.0 / 1_000_000
        + 500_000 * 15.0 / 1_000_000
        + 2_000_000 * 0.3 / 1_000_000
        + 100_000 * 3.75 / 1_000_000
    )
    assert cost_usd(USAGE, pricing) == expected


def test_cost_usd_treats_missing_usage_keys_as_zero() -> None:
    pricing = Pricing(
        input_usd_per_mtok=1.0,
        output_usd_per_mtok=1.0,
        cache_read_usd_per_mtok=1.0,
        cache_write_usd_per_mtok=1.0,
    )
    assert cost_usd({}, pricing) == 0.0
