"""Spend-meter price basis — ONE source of truth (#2874).

Why this module exists: the battery's cost figures were computed from a rate
constant that had been copied into five places (`model_calls`, `probe_runner`,
`judge/client`, `arms.yaml`, and the parity MABench lane). Copies drift, and
this one had drifted from reality entirely: it claimed 0.27 in / 1.10 out per
1M tokens for `deepseek/deepseek-v4-flash`, which matches no provider for that
model. Every published `real_spend_usd` was therefore inflated, the mid-run
spend cap (#2603) tripped early, and the judge reserve was denominated in the
wrong units.

The rule now: exactly one place declares the basis, and it records WHERE the
number came from and WHEN it was checked, so a stale basis is visible rather
than silent. Consumers import it; a test fails if they stop agreeing.

Basis: the price of the model id the arms actually PIN, on the route the real
lane actually calls (OpenRouter, via ``OPENROUTER_API_KEY``) — not the direct
DeepSeek API, not a sibling model.
"""
from __future__ import annotations

#: The model the rates below apply to — the arms.yaml `model_pin` value.
PRICE_MODEL_ID = "deepseek/deepseek-v4-flash"
#: Where the numbers came from (the publisher's own pricing feed).
PRICE_SOURCE = "https://openrouter.ai/api/v1/models"
#: When they were checked. Re-check on any model-pin change (#2284 Task 6
#: treats the pin as a protocol input, so the price must move with it).
PRICE_CHECKED_ON = "2026-09-10"

#: (input, output) USD per 1M tokens. OpenRouter, 2026-09-10.
RATES_PER_1M_USD: tuple[float, float] = (0.084, 0.168)
#: Cache-read rate (USD per 1M tokens), for meters that model prompt caching.
CACHE_READ_PER_1M_USD = 0.0168


def cost_usd(prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0) -> float:
    """Cost of one call under the declared basis.

    ``cached_tokens`` (when the caller knows them) are billed at the cache-read
    rate; a meter that does not track caching passes 0 and over-estimates
    slightly, which is the safe direction for a spend cap.
    """
    p_in, p_out = RATES_PER_1M_USD
    billed_in = max(int(prompt_tokens) - int(cached_tokens), 0)
    return ((billed_in * p_in
             + int(cached_tokens) * CACHE_READ_PER_1M_USD
             + int(completion_tokens) * p_out) / 1_000_000.0)


def price_per_1k_usd() -> float:
    """The arms.yaml pre-run guard's conservative per-1k bound.

    All tokens priced as OUTPUT (the most expensive rate) — the guard exists
    to refuse a run that could exceed its budget, so it must not under-estimate.
    """
    return RATES_PER_1M_USD[1] / 1000.0
