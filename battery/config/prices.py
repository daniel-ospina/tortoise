"""Spend-meter price basis — the declared FALLBACK (#2874, #2906).

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

#2906: this module is a FALLBACK, not the source of truth. When a real call's
response carries ``usage.cost``, the meter uses that provider-reported charge
(``provider_reported``); the rates below only price calls whose provider does
not report one (mocks/offline/hermetic lanes, and routes without the field).
OpenRouter serves the pinned id from ~11 upstreams priced ~0.068–0.14 per 1M
input, and nothing pins which one serves a given call, so no constant can be
correct: the measured meter was 2.5x high on one lane and 1.5x low on another.

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
#: #2906: FALLBACK ONLY — not the source of truth. The real source of truth
#: is the provider's own ``usage.cost`` on the response; this constant prices
#: only the calls that do not carry one. OpenRouter serves the pinned id from
#: ~11 upstreams spread over ~0.068–0.14 per 1M input (measured 2026-09-10),
#: so any single pair here is wrong in both directions for a given call.
RATES_PER_1M_USD: tuple[float, float] = (0.084, 0.168)
#: Cache-read rate (USD per 1M tokens), for meters that model prompt caching.
#: Also from `PRICE_SOURCE` above (its `pricing.input_cache_read` field).
#: NOTE: no production caller passes `cached_tokens` yet, so this rate is
#: currently inert — every meter prices the full prompt at the input rate,
#: which OVER-estimates. That is the safe direction for a spend cap; wiring
#: caching in must not flip it (a cache hit is cheaper, never dearer).
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
