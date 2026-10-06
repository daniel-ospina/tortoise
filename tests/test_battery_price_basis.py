"""#2874 — ONE declared price basis, and it must match reality.

The battery's spend figures were computed from a rate constant copied into five
places; it had drifted to 0.27 in / 1.10 out per 1M for
`deepseek/deepseek-v4-flash`, which matches no provider price for that model.
Every published `real_spend_usd` was inflated by the same factor.

These tests pin the two properties that prevent a repeat: there is exactly one
declaration, and every consumer (including the YAML guard) agrees with it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.config.prices import (
    PRICE_CHECKED_ON,
    PRICE_MODEL_ID,
    PRICE_SOURCE,
    RATES_PER_1M_USD,
    cost_usd,
    price_per_1k_usd,
)


class TestDeclaredBasis:
    def test_basis_records_its_provenance(self):
        """A bare constant is how this drifted; the basis must say what it is
        for, where it came from, and when it was checked."""
        assert PRICE_MODEL_ID == "deepseek/deepseek-v4-flash"
        assert PRICE_SOURCE.startswith("https://")
        assert PRICE_CHECKED_ON == "2026-09-10"

    def test_rates_are_positive_and_output_costs_more(self):
        p_in, p_out = RATES_PER_1M_USD
        assert 0 < p_in < p_out, "output is the dearer direction at every vendor"

    def test_cost_is_per_million_tokens(self):
        assert cost_usd(1_000_000, 0) == pytest.approx(RATES_PER_1M_USD[0])
        assert cost_usd(0, 1_000_000) == pytest.approx(RATES_PER_1M_USD[1])
        assert cost_usd(0, 0) == 0.0

    def test_cached_tokens_are_cheaper_and_never_negative(self):
        full = cost_usd(1000, 0)
        cached = cost_usd(1000, 0, cached_tokens=1000)
        assert cached < full
        # a caller reporting more cached tokens than prompt tokens must not
        # produce a negative charge
        assert cost_usd(10, 0, cached_tokens=999) >= 0.0

    def test_per_1k_bound_is_the_output_rate(self):
        """The pre-run guard must never UNDER-estimate: it prices every token
        as output."""
        assert price_per_1k_usd() == pytest.approx(RATES_PER_1M_USD[1] / 1000)


class TestNoDriftBetweenConsumers:
    """Every consumer imports the one basis (#2874)."""

    def test_model_calls_uses_the_basis(self):
        from battery.runner import model_calls
        assert model_calls._REAL_RATES_PER_1M_USD == RATES_PER_1M_USD
        assert model_calls._usage_cost_usd(1000, 2000) == pytest.approx(
            cost_usd(1000, 2000))

    def test_probe_runner_uses_the_basis(self):
        from battery.probes import probe_runner
        assert probe_runner._PROBE_RATES_PER_1M_USD == RATES_PER_1M_USD

    def test_judge_price_table_uses_the_basis(self):
        import inspect

        from battery.judge import client
        src = inspect.getsource(client)
        assert '"deepseek": RATES_PER_1M_USD' in src, (
            "the judge price table must IMPORT the declared basis, not copy it")

    def test_parity_lane_cost_uses_the_basis(self):
        from battery.parity import mabench_run
        assert mabench_run._cost(1000, 2000) == pytest.approx(cost_usd(1000, 2000))

    def test_arms_yaml_guard_matches_the_basis(self):
        """The YAML pre-run guard is the one consumer that cannot import
        Python — so a test has to keep it honest."""
        arms = yaml.safe_load(
            (Path(__file__).resolve().parent.parent / "battery" / "config"
             / "arms.yaml").read_text())
        entries = arms["arms"] if isinstance(arms, dict) and "arms" in arms else arms
        # the REAL lanes share the basis; mock/lane-cap arms carry their own
        # (deliberately conservative) bounds
        real_lane_prices = [e["price_per_1k_usd"] for e in entries
                            if str(e.get("model_pin", "")).startswith("deepseek/")]
        assert real_lane_prices, "no real-lane arm found in arms.yaml"
        for price in real_lane_prices:
            assert float(price) == pytest.approx(price_per_1k_usd()), (
                f"arm guard {price} != declared basis {price_per_1k_usd()} — "
                f"a stale guard refuses runs that are actually affordable")
