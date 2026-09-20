"""Cost per document (PROJECT_BRIEF §9): computed from stored token counts,
never a separate tracked total that can drift from `extractions.usage`.

Same pricing convention as scripts/extraction_run.py (PRICE_*_USD_PER_MTOK),
now read through app.settings so the running system (/metrics, per-run_id
queries) and the offline report use one source of truth for what "cost"
means. Cost is `None` — never a guess, never zero — whenever any of the
four prices is unset; a partially-priced run is not a partial cost.
"""

from __future__ import annotations

import dataclasses

from app.settings import Settings

# extractions.usage key -> (the Settings field priced against it, the
# matching Pricing field). Mirrors scripts/extraction_run.py's _PRICE_KEYS,
# in terms of this module's own config source instead of raw os.environ.
_PRICE_FIELDS: dict[str, tuple[str, str]] = {
    "input_tokens": ("price_input_usd_per_mtok", "input_usd_per_mtok"),
    "output_tokens": ("price_output_usd_per_mtok", "output_usd_per_mtok"),
    "cache_read_input_tokens": ("price_cache_read_usd_per_mtok", "cache_read_usd_per_mtok"),
    "cache_creation_input_tokens": ("price_cache_write_usd_per_mtok", "cache_write_usd_per_mtok"),
}


@dataclasses.dataclass(frozen=True)
class Pricing:
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cache_read_usd_per_mtok: float
    cache_write_usd_per_mtok: float

    def rate_for(self, usage_key: str) -> float:
        return {
            "input_tokens": self.input_usd_per_mtok,
            "output_tokens": self.output_usd_per_mtok,
            "cache_read_input_tokens": self.cache_read_usd_per_mtok,
            "cache_creation_input_tokens": self.cache_write_usd_per_mtok,
        }[usage_key]


def pricing_from_settings(settings: Settings) -> Pricing | None:
    """None unless all four PRICE_*_USD_PER_MTOK values are configured —
    same all-or-nothing rule as scripts/extraction_run.py's `_pricing`."""
    values = {
        pricing_field: getattr(settings, settings_field)
        for settings_field, pricing_field in _PRICE_FIELDS.values()
    }
    if any(v is None for v in values.values()):
        return None
    return Pricing(**values)  # type: ignore[arg-type]


def cost_usd(usage: dict[str, int], pricing: Pricing | None) -> float | None:
    """The cost of one `extractions.usage` blob, or None if unpriced."""
    if pricing is None:
        return None
    return sum(usage.get(key, 0) * pricing.rate_for(key) / 1_000_000 for key in _PRICE_FIELDS)
