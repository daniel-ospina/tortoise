"""#14277 — the cost measurement's own arithmetic.

The measurement is only worth what its arithmetic is worth: the two constants
it reports are a least-squares fit, the cost tables are derived from that fit,
and the allowance re-statement divides by the measured token count. A silent
sign error in any of those produces a number that looks like a result.

No API calls and no corpus walk here — these pin the pure functions against
hand-computable inputs, so the tool can be trusted on the day it is run and
re-run without spending money to check itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tools import extractor_cost_measure as m  # noqa: E402


def test_least_squares_recovers_a_line_it_was_given():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [5.0 + 2.0 * x for x in xs]
    slope, intercept = m._least_squares(xs, ys)
    assert slope == pytest.approx(2.0)
    assert intercept == pytest.approx(5.0)


def test_least_squares_on_a_degenerate_range_does_not_divide_by_zero():
    """Every item the same size is a real possibility in a narrow corpus; the
    fit must degrade to the mean rather than raise."""
    slope, intercept = m._least_squares([3.0, 3.0, 3.0], [7.0, 9.0, 11.0])
    assert slope == 0.0
    assert intercept == pytest.approx(9.0)


def test_a_perfect_fit_reports_r2_of_one_and_zero_residual():
    points = [(1.0, 10.0), (2.0, 20.0), (3.0, 30.0)]
    fit = m._fit(points)
    assert fit["slope"] == pytest.approx(10.0)
    assert fit["intercept"] == pytest.approx(0.0)
    assert fit["r2"] == pytest.approx(1.0)
    assert fit["max_abs_resid"] == pytest.approx(0.0)


def test_item_cost_splits_the_prompt_at_the_measured_hit_rate():
    """The whole cache question in one assertion: at a 50% hit share, half the
    prompt pays the input rate and half the cache rate, and the output is
    untouched by caching (there is nothing to cache on the emission side)."""
    rates = {"input": 1.0, "output": 2.0, "cache_hit": 0.1}
    cold = m._item_cost(1_000_000, 0, 0.0, rates)
    half = m._item_cost(1_000_000, 0, 0.5, rates)
    warm = m._item_cost(1_000_000, 0, 1.0, rates)
    assert cold == pytest.approx(1.0)
    assert half == pytest.approx(0.55)
    assert warm == pytest.approx(0.1)
    # a hit share outside [0, 1] is clamped, never extrapolated
    assert m._item_cost(1_000_000, 0, 1.5, rates) == pytest.approx(0.1)
    assert m._item_cost(1_000_000, 0, -1.0, rates) == pytest.approx(1.0)


def test_the_issue_model_is_the_model_the_issue_states():
    """`#14275`'s numbers are the thing under test, so they are restated in ONE
    place: `1.5T + 7k` input, `0.2T` output. A change here is a change to the
    claim being tested, which is why it is pinned rather than inline."""
    tin, tout = m._issue_model(5_162)
    assert tin == pytest.approx(1.5 * 5_162 + 7_000)
    assert tout == pytest.approx(0.2 * 5_162)


def _row(**kw) -> dict:
    base = {"kind": "doc", "arm": "cold", "item_tokens": 5_000,
            "prompt_tokens": 50_000, "completion_tokens": 16_000,
            "cache_hit_tokens": 25_000, "cache_miss_tokens": 25_000,
            "calls": 4, "seconds": 1.0,
            "stages": {"s1": {"calls": 1, "prompt_tokens": 12_000,
                              "completion_tokens": 2_000,
                              "cache_hit_tokens": 4_000,
                              "cache_miss_tokens": 8_000},
                       "s2": {"calls": 1, "prompt_tokens": 15_000,
                              "completion_tokens": 11_000,
                              "cache_hit_tokens": 9_000,
                              "cache_miss_tokens": 6_000},
                       "s4": {"calls": 1, "prompt_tokens": 23_000,
                              "completion_tokens": 3_000,
                              "cache_hit_tokens": 12_000,
                              "cache_miss_tokens": 11_000}}}
    base.update(kw)
    return base


def test_measurement_cost_uses_the_measured_cache_share():
    rates = {"input": 1.0, "output": 2.0, "cache_hit": 0.1}
    rows = [{"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
             "cache_hit_tokens": 500_000, "calls": 3},
            {"prompt_tokens": 0, "completion_tokens": 0,
             "cache_hit_tokens": 0, "calls": 0}]
    cost = m.measurement_cost(rows, rates, rates)
    assert cost["items"] == 2
    assert cost["hit_ratio"] == pytest.approx(0.5)
    # 0.5M miss at 1.0/M + 0.5M hit at 0.1/M + 1M out at 2.0/M
    assert cost["usd_peak"] == pytest.approx(0.5 + 0.05 + 2.0)
    assert cost["usd_peak_no_cache"] == pytest.approx(1.0 + 2.0)


def test_a_failed_item_is_counted_but_never_priced():
    """A run that half-failed must not report the surviving half's cost as the
    run's cost, and must not hide the failure either."""
    rows = [{"prompt_tokens": 1_000_000, "completion_tokens": 0,
             "cache_hit_tokens": 0, "calls": 1},
            {"failed": True, "error": "HTTP 500"}]
    cost = m.measurement_cost(rows, {"input": 1.0, "output": 1.0,
                                     "cache_hit": 0.0},
                              {"input": 1.0, "output": 1.0, "cache_hit": 0.0})
    assert cost["items"] == 1
    report = m.build_report(rows)
    assert report["failed"] == 1
    assert report["rows"] == 2


def test_allowance_table_derives_documents_from_the_measured_token_count():
    """`#14275`'s 3M-token allowance buys `3M / tokens-per-document` items —
    the arithmetic that turned ≈200 documents into ~47. It is derived here,
    never asserted, so a re-measured token count moves the answer."""
    rows = [_row(item_tokens=5_000 + i, prompt_tokens=48_000,
                 completion_tokens=16_000) for i in range(5)]
    report = m.build_report(rows)
    report["_rows"] = [r for r in rows]
    cost = m.cost_table(report, m.ISSUE_RATES["peak"],
                        m.ISSUE_RATES["off_peak"])
    per_doc = cost["tokens_per_median_doc"]
    assert per_doc == 64_000
    table = m.allowance_table(cost, per_doc)
    solo = next(a for a in table if a["tier"] == "solo")
    assert solo["documents"] == round(3_000_000 / 64_000)
    assert solo["documents"] == 47
    assert solo["usd_peak"] > 0.0
    assert solo["pct_peak"] > 0.0
    free = next(a for a in table if a["tier"] == "free")
    assert free["documents"] == 0 and free["usd_peak"] == 0.0


def test_the_corpus_is_deterministic_and_carries_the_length_spread():
    """Cold and warm arms are comparable only because they measure the SAME
    corpus; and a corpus of 50 similar files would answer a different
    question than the one asked."""
    first = m.pick_corpus(limit=20, seed=7)
    second = m.pick_corpus(limit=20, seed=7)
    assert [r["path"] for r in first] == [r["path"] for r in second]
    sizes = [r["tokens"] for r in first]
    assert sizes == sorted(sizes)
    inventory = m.doc_inventory()
    assert sizes[-1] == max(r["tokens"] for r in inventory), (
        "the longest document in the repo must be in the sample — the "
        "constant's leverage on long documents is the thing being measured")
    assert sizes[0] < 2_000, "the small end of the spread must be present"
    assert max(sizes) > 10 * sizes[0], "the spread must span an order of magnitude"
