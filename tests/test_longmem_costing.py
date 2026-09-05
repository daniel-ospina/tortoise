"""Costing tests (#2185 Task 5 — tools/longmem_eval/costing.py).

Pins the pricing engine contract:

- ``price_usage_envelope(envelope) -> (cost_usd, priced: bool, breakdown)``
  prices a usage ENVELOPE (the collector's drain shape) with the versioned
  PRICING_MAP — USD per 1M tokens, Σ prompt×rate/1e6 + completion×rate/1e6
  rounded to 6dp.
- dual wire-form keys: both ``deepseek/deepseek-v4-flash`` and the bare
  ``deepseek-v4-flash`` resolve (the openrouter lane carries the vendor-
  prefixed id; the deepseek-direct lane the bare id).
- provider normalization folds the reader/judge resolution names
  (``deepseek``, ``openai``) and the adapter class names
  (``deepseek-direct``) onto the same price table where they denote the
  same API family.
- out-of-map lane → ``priced=False`` with a loud marker — never a crash,
  never a silent $0, never a guessed ``estimated`` row.
- cache-hit priced at the reduced cache_read rate ONLY where the map entry
  carries a verified ``cache_read_per_1m`` (the deepseek lanes); the prompt
  leg is flagged ``cache_discount_unpriced`` when the entry has no cache
  rate but the bucket has cache-hit tokens (never silently full-priced).
- map entries carry source + verified_on + estimated (honest provenance);
  ``estimated`` lanes are totaled but surfaced in the breakdown.
- reasoning tokens are NOT double-added (completion_tokens covers them).
"""
from __future__ import annotations

import math

import pytest

from tools.longmem_eval.costing import (
    PRICING_MAP,
    PRICING_MAP_VERSION,
    lookup_rate,
    price_usage_envelope,
)


def _env(rows_by_lane, total=None):
    """Envelope from {lane_key: bucket} — lane_key=(stage, provider, model)."""
    by_stage = {}
    for (stage, provider, model), bucket in rows_by_lane.items():
        by_stage.setdefault(stage, {}).setdefault(provider, {})[model] = bucket
    if total is None:
        total = {
            "prompt_tokens": sum(b.get("prompt_tokens", 0)
                                 for b in rows_by_lane.values()),
            "completion_tokens": sum(b.get("completion_tokens", 0)
                                     for b in rows_by_lane.values()),
            "calls": sum(b.get("calls", 0) for b in rows_by_lane.values()),
        }
    return {"by_stage": by_stage, "total": total}


# ── metadata / map sanity ───────────────────────────────────────────────────

def test_map_version_and_provenance_fields():
    assert isinstance(PRICING_MAP_VERSION, str) and PRICING_MAP_VERSION
    for provider, models in PRICING_MAP.items():
        for model, entry in models.items():
            for field in ("prompt_per_1m", "completion_per_1m", "source",
                          "verified_on", "estimated"):
                assert field in entry, (provider, model, field)
            assert entry["prompt_per_1m"] >= 0
            assert entry["completion_per_1m"] >= 0
            assert isinstance(entry["verified_on"], str)
            assert isinstance(entry["estimated"], bool)


def test_default_eval_lanes_are_priced():
    """The three lanes a DEFAULT eval run reaches are all in-map: the reader
    (openrouter deepseek/deepseek-v4-flash), the official judge (openai
    gpt-4o-2024-08-06) and the default extractor lanes."""
    assert lookup_rate("openrouter", "deepseek/deepseek-v4-flash")
    assert lookup_rate("openai", "gpt-4o-2024-08-06")
    assert lookup_rate("deepseek-direct", "deepseek-v4-flash")


# ── price math ──────────────────────────────────────────────────────────────

def test_known_lane_price_math():
    usd, priced, breakdown = price_usage_envelope(_env({
        ("reader", "openrouter", "deepseek/deepseek-v4-flash"): {
            "prompt_tokens": 1_000_000, "completion_tokens": 500_000,
            "calls": 1, "usage_present": True},
    }))
    assert priced is True
    # 1.0M × 0.14/1M + 0.5M × 0.28/1M = 0.14 + 0.14
    assert math.isclose(usd, 0.28, abs_tol=1e-6)
    assert breakdown["priced"] is True
    lane = breakdown["lanes"][0]
    assert lane["provider"] == "openrouter"
    assert lane["model"] == "deepseek/deepseek-v4-flash"
    assert lane["usd"] == pytest.approx(0.28)


def test_rounding_to_six_dp():
    usd, priced, _ = price_usage_envelope(_env({
        ("reader", "openrouter", "deepseek/deepseek-v4-flash"): {
            "prompt_tokens": 12345, "completion_tokens": 6789,
            "calls": 1, "usage_present": True},
    }))
    # 12345/1e6×0.14 + 6789/1e6×0.28
    expected = 12345 * 0.14 / 1e6 + 6789 * 0.28 / 1e6
    assert usd == round(expected, 6)
    assert priced is True


def test_dual_wire_form_keys_resolve():
    """deepseek/deepseek-v4-flash (openrouter) and deepseek-v4-flash
    (deepseek-direct) both resolve — never an unpriced lane for the bare
    form."""
    r_or = lookup_rate("openrouter", "deepseek/deepseek-v4-flash")
    r_dd = lookup_rate("deepseek-direct", "deepseek-v4-flash")
    assert r_or is not None and r_dd is not None
    assert r_or["prompt_per_1m"] == r_dd["prompt_per_1m"] == 0.14
    assert r_or["completion_per_1m"] == r_dd["completion_per_1m"] == 0.28


def test_judge_gpt4o_lane():
    usd, priced, _ = price_usage_envelope(_env({
        ("judge", "openai", "gpt-4o-2024-08-06"): {
            "prompt_tokens": 100_000, "completion_tokens": 10_000,
            "calls": 20, "usage_present": True},
    }))
    assert priced is True
    assert math.isclose(usd, 0.1 * 2.5 + 0.01 * 10.0, abs_tol=1e-6)


def test_multi_lane_totals_additive():
    usd, _, breakdown = price_usage_envelope(_env({
        ("reader", "openrouter", "deepseek/deepseek-v4-flash"): {
            "prompt_tokens": 1_000_000, "completion_tokens": 0,
            "calls": 1, "usage_present": True},
        ("judge", "openai", "gpt-4o-2024-08-06"): {
            "prompt_tokens": 0, "completion_tokens": 100_000,
            "calls": 1, "usage_present": True},
    }))
    assert math.isclose(usd, 0.14 + 1.0, abs_tol=1e-6)
    assert len(breakdown["lanes"]) == 2


# ── unpriced / out-of-map ───────────────────────────────────────────────────

def test_out_of_map_lane_priced_false_no_crash():
    usd, priced, breakdown = price_usage_envelope(_env({
        ("reader", "openrouter", "totally/unknown-model-x"): {
            "prompt_tokens": 1000, "completion_tokens": 10,
            "calls": 1, "usage_present": True},
    }))
    assert usd == 0.0
    assert priced is False
    assert breakdown["unpriced"] and breakdown["unpriced"][0]["model"] == (
        "totally/unknown-model-x")


def test_mixed_priced_and_unpriced_lanes():
    usd, priced, breakdown = price_usage_envelope(_env({
        ("reader", "openrouter", "deepseek/deepseek-v4-flash"): {
            "prompt_tokens": 1_000_000, "completion_tokens": 0,
            "calls": 1, "usage_present": True},
        ("judge", "openai", "some-unknown-judge"): {
            "prompt_tokens": 1000, "completion_tokens": 1000,
            "calls": 1, "usage_present": True},
    }))
    assert priced is False  # ANY unpriced lane → the envelope is not fully priced
    assert math.isclose(usd, 0.14, abs_tol=1e-9)  # priced lanes still totaled
    assert len(breakdown["unpriced"]) == 1
    assert breakdown["unpriced"][0]["provider"] == "openai"


def test_empty_envelope_is_priced_trivially():
    usd, priced, breakdown = price_usage_envelope(None)
    assert usd == 0.0 and priced is True and not breakdown["lanes"]


# ── cache-detail handling ───────────────────────────────────────────────────

def test_cache_hit_priced_at_reduced_rate_when_verified():
    """deepseek lane verified cache_read: bill miss at prompt rate + hit at
    the reduced cache_read rate (prompt_tokens INCLUDES the cached tokens)."""
    rate = lookup_rate("openrouter", "deepseek/deepseek-v4-flash")
    cache_read = rate["cache_read_per_1m"]
    usd, _, breakdown = price_usage_envelope(_env({
        ("ingest", "openrouter", "deepseek/deepseek-v4-flash"): {
            "prompt_tokens": 1000, "completion_tokens": 0,
            "prompt_cache_hit_tokens": 800,  # 800 cached of 1000
            "calls": 1, "usage_present": True},
    }))
    miss = 1000 - 800
    expected = round((miss * 0.14 + 800 * cache_read) / 1e6, 6)
    assert math.isclose(usd, expected, abs_tol=1e-12)
    lane = breakdown["lanes"][0]
    assert lane["cache_discount_applied"] is True


def test_cache_tokens_without_verified_rate_flagged_not_silent():
    """gpt-4o entry has no cache_read (unverified): cache-hit tokens present
    → the lane is full-priced BUT flagged (never silently discounted, never
    silently full-priced without disclosure)."""
    usd, _, breakdown = price_usage_envelope(_env({
        ("judge", "openai", "gpt-4o-2024-08-06"): {
            "prompt_tokens": 1000, "completion_tokens": 0,
            "prompt_cache_hit_tokens": 900,
            "calls": 1, "usage_present": True},
    }))
    assert math.isclose(usd, 1000 * 2.5 / 1e6, abs_tol=1e-9)
    lane = breakdown["lanes"][0]
    assert lane["cache_discount_unpriced"] is True


def test_reasoning_not_double_counted():
    """reasoning_tokens ride completion_tokens — only completion is billed."""
    usd, _, _ = price_usage_envelope(_env({
        ("ingest", "deepseek-direct", "deepseek-v4-flash"): {
            "prompt_tokens": 10_000, "completion_tokens": 2000,
            "reasoning_tokens": 1500,  # included in completion_tokens
            "calls": 1, "usage_present": True},
    }))
    assert math.isclose(usd, (10_000 * 0.14 + 2000 * 0.28) / 1e6,
                        abs_tol=1e-9)


# ── provider normalization ──────────────────────────────────────────────────

def test_provider_normalization_folds_direct_spellings():
    """_PROVIDERS resolution names ('deepseek') and adapter class names
    ('deepseek-direct') hit the same DeepSeek table."""
    assert lookup_rate("deepseek", "deepseek-v4-flash") is not None
    assert lookup_rate("deepseek-direct", "deepseek-v4-flash") is not None


def test_usage_present_false_lane_unpriced_loud():
    """A lane whose provider returned NO usage numbers can't be costed — it
    must surface as unpriced (spend exists, tokens unknown), never $0."""
    usd, priced, breakdown = price_usage_envelope(_env({
        ("reader", "openrouter", "deepseek/deepseek-v4-flash"): {
            "prompt_tokens": 0, "completion_tokens": 0, "calls": 1,
            "usage_present": False},
    }))
    assert priced is False
    assert breakdown["unpriced"] and breakdown["unpriced"][0][
        "usage_present"] is False
    assert usd == 0.0


# ── round-2 code-review regressions (#2250) ─────────────────────────────────

def test_openrouter_gpt4o_judge_lane_prices_estimated():
    """Bug-scan P2: the judge lane is served via the openrouter transport in
    a both-keys env (spec openai:gpt-4o-2024-08-06 + OPENROUTER_API_KEY) and
    records the BARE model id — the map must price it (estimated: OpenRouter
    list = OpenAI list + ~5% platform fee), not report $0/priced:false."""
    rate = lookup_rate("openrouter", "gpt-4o-2024-08-06")
    assert rate is not None
    assert rate["estimated"] is True
    # the vendor-prefixed OpenRouter spelling resolves via the bare fallback
    rate2 = lookup_rate("openrouter", "openai/gpt-4o-2024-08-06")
    assert rate2 is not None
    usd, priced, breakdown = price_usage_envelope(_env({
        ("judge", "openrouter", "gpt-4o-2024-08-06"): {
            "prompt_tokens": 1000, "completion_tokens": 100,
            "calls": 1, "usage_present": True},
    }))
    assert priced is True
    assert math.isclose(usd, (1000 * 2.625 + 100 * 10.5) / 1e6,
                        abs_tol=1e-9)
    assert breakdown["lanes"][0]["estimated"] is True


def test_poison_lane_values_never_crash_pricing():
    """Security review P2 belt: a tampered checkpoint bucket carrying
    non-finite / >1e300 / bool token values degrades to 0 (lane priced at
    0 tokens — never OverflowError / inf round at report assembly).
    Round-2 addendum: the calls_without_usage scalar gets the SAME
    finite + magnitude guard before int() (int(inf) raises OverflowError
    — the exact round-1 crash class on the scalar this round added)."""
    _, priced, breakdown = price_usage_envelope(_env({
        ("judge", "openai", "gpt-4o-2024-08-06"): {
            "prompt_tokens": float("nan"),
            "completion_tokens": 10 ** 400,
            "calls": 1, "usage_present": True,
            "calls_without_usage": float("inf")},
    }))
    lane = breakdown["lanes"][0]
    assert lane["prompt_tokens"] == 0
    assert lane["completion_tokens"] == 0
    assert lane["calls_without_usage"] == 0
    assert math.isclose(lane["usd"], 0.0, abs_tol=1e-12)
    assert priced is True  # the lane IS priceable — tokens just bounded out
    # NaN variant (int(nan) raises ValueError — must also degrade)
    _, _, breakdown = price_usage_envelope(_env({
        ("judge", "openai", "gpt-4o-2024-08-06"): {
            "prompt_tokens": 1, "completion_tokens": 1,
            "calls": 1, "usage_present": True,
            "calls_without_usage": float("nan")},
    }))
    assert breakdown["lanes"][0]["calls_without_usage"] == 0


def test_lane_calls_without_usage_disclosed():
    """Bug-scan P2: the lane row discloses how many calls lacked a usage
    block (calls_without_usage) alongside the conservative priced:false
    flag when ANY row was usage-less."""
    usd, _, breakdown = price_usage_envelope(_env({
        ("reader", "openrouter", "deepseek/deepseek-v4-flash"): {
            "prompt_tokens": 1000, "completion_tokens": 0,
            "calls": 2, "usage_present": False,
            "calls_without_usage": 1},
    }))
    lane = breakdown["lanes"][0]
    assert lane["calls_without_usage"] == 1
    assert lane["priced"] is False
    assert lane["reason"] == "usage_present_false"
    assert usd == 0.0
